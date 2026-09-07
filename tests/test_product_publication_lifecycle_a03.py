"""Tests for A03: valid publication-job lifecycle (dispatch after commit, atomic
claim, revision discipline, no concurrent execution, worker-cut recovery).

Contract: Docs/03-contracts/PRODUCT-INTERFACES-V2.md §I06
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from django.db import transaction
from django.test import TestCase

from apps.rebuild.models import PublicationJob, PublicationJobState, RemovalState


@pytest.fixture(autouse=True)
def db_access(db):
    pass


def _result(job_id, state, **kwargs):
    from apps.rebuild.services import record_job_result

    return record_job_result(job_id, state=state, **kwargs)


def test_enqueue_generates_revision_when_empty_a03():
    """Every job carries a valid non-empty opaque revision for its artifact."""
    from apps.rebuild.services import enqueue_publication_job

    job = enqueue_publication_job(locale="en", affected_paths=["/en/"])
    assert job.requested_revision
    assert job.requested_revision.strip() != ""

    explicit = enqueue_publication_job(
        locale="en", requested_revision="rev-explicit-1", affected_paths=["/en/"]
    )
    assert explicit.requested_revision == "rev-explicit-1"


def test_enqueue_dispatches_after_commit_with_job_id_a03():
    """Dispatch fires once, after transaction commit, carrying the job UUID."""
    from apps.rebuild.services import enqueue_publication_job

    with patch("apps.rebuild.services.invoke_static_rebuild") as mocked:
        with TestCase.captureOnCommitCallbacks(execute=True):
            with transaction.atomic():
                job = enqueue_publication_job(locale="en", affected_paths=["/en/"])
                # Still inside the transaction: no dispatch yet.
                mocked.assert_not_called()
        mocked.assert_called_once()
        assert mocked.call_args.kwargs.get("job_id") == str(job.id)


def test_rollback_dispatches_nothing_a03():
    """A rolled-back transaction leaves neither a job row nor a dispatch."""
    from apps.rebuild.services import enqueue_publication_job

    with patch("apps.rebuild.services.invoke_static_rebuild") as mocked:
        with TestCase.captureOnCommitCallbacks(execute=True):
            try:
                with transaction.atomic():
                    enqueue_publication_job(locale="en", affected_paths=["/en/"])
                    raise RuntimeError("boom")
            except RuntimeError:
                pass
        mocked.assert_not_called()
    assert PublicationJob.objects.count() == 0


def test_claim_is_exclusive_a03():
    """queued→running claims once; a second claim is rejected with 409."""
    job = PublicationJob.objects.create(
        locale="en", requested_revision="rev-claim-1", affected_paths=["/en/"]
    )
    status, _ = _result(job.id, PublicationJobState.RUNNING)
    assert status == 200

    status, payload = _result(job.id, PublicationJobState.RUNNING)
    assert status == 409
    assert payload["code"] == "CONFLICT"

    # Terminal result from the claim holder still works afterwards.
    status, payload = _result(
        job.id, PublicationJobState.SUCCEEDED, artifact_revision="rev-claim-1"
    )
    assert status == 200
    assert payload["state"] == "succeeded"


def test_terminal_from_queued_is_rejected_a03():
    """A runner must claim (queued→running) before reporting terminal state."""
    job = PublicationJob.objects.create(
        locale="en", requested_revision="rev-claim-2", affected_paths=["/en/"]
    )
    status, payload = _result(
        job.id, PublicationJobState.SUCCEEDED, artifact_revision="rev-claim-2"
    )
    assert status == 409
    assert payload["code"] == "CONFLICT"

    status, payload = _result(
        job.id, PublicationJobState.FAILED, error_code="BUILD_FAILED"
    )
    assert status == 409


def test_succeeded_requires_matching_nonempty_revision_a03():
    """Succeeded demands a non-empty artifactRevision matching the request."""
    job = PublicationJob.objects.create(
        locale="en", requested_revision="rev-match-1", affected_paths=["/en/"]
    )
    assert _result(job.id, PublicationJobState.RUNNING)[0] == 200

    status, payload = _result(job.id, PublicationJobState.SUCCEEDED)
    assert status == 400
    assert payload["code"] == "VALIDATION_FAILED"

    status, _ = _result(
        job.id, PublicationJobState.SUCCEEDED, artifact_revision="wrong"
    )
    assert status == 400

    status, payload = _result(
        job.id, PublicationJobState.SUCCEEDED, artifact_revision="rev-match-1"
    )
    assert status == 200
    assert payload["deployedRevision"] == "rev-match-1"


def test_effective_removal_requires_pending_a03():
    """removalState effective/failed is only meaningful for pending removals."""
    plain = PublicationJob.objects.create(
        locale="en", requested_revision="rev-rem-1", affected_paths=["/en/"]
    )
    assert _result(plain.id, PublicationJobState.RUNNING)[0] == 200
    status, payload = _result(
        plain.id,
        PublicationJobState.SUCCEEDED,
        artifact_revision="rev-rem-1",
        removal_state="effective",
    )
    assert status == 400
    assert payload["code"] == "VALIDATION_FAILED"

    pending = PublicationJob.objects.create(
        locale="en",
        requested_revision="rev-rem-2",
        affected_paths=["/en/blog/old/"],
        revoked_paths=["/en/blog/old/"],
        removal_state=RemovalState.PENDING,
    )
    assert _result(pending.id, PublicationJobState.RUNNING)[0] == 200
    status, payload = _result(
        pending.id,
        PublicationJobState.SUCCEEDED,
        artifact_revision="rev-rem-2",
        removal_state="effective",
    )
    assert status == 200
    assert payload["removalState"] == "effective"


def test_unknown_job_id_is_404_a03():
    status, payload = _result(
        uuid.uuid4(), PublicationJobState.RUNNING
    )
    assert status == 404
    assert payload["code"] == "NOT_FOUND"
