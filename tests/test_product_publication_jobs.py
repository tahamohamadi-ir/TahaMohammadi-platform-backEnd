"""Tests for PU-07-jobs: Persist idempotent publication jobs and expose
admin status/retry plus authenticated build-result callback.

Contract: Docs/03-contracts/PRODUCT-INTERFACES-V2.md §I06
Packet: Docs/05-delivery/concept-alignment-v2/product-packets/PU-07-jobs.md
"""

from __future__ import annotations

import json
import time
import uuid

import pytest
from django.contrib.auth import get_user_model
from django.test import Client, override_settings
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.rebuild.models import (
    PublicationJob,
    PublicationMachineNonce,
    RemovalState,
)
from apps.rebuild.services import (
    sign_machine_request,
)

TEST_MACHINE_SECRET = "test-machine-secret-very-secure-key-12345"


@pytest.fixture(autouse=True)
def db_access(db):
    """Ensure database access for all tests."""
    pass


@pytest.fixture
def admin_client():
    """Client with staff session and verified OTP device."""
    user_model = get_user_model()
    user = user_model.objects.create_superuser(
        username="admin-jobs",
        email="admin-jobs@example.com",
        password="ValidPassword123!",
    )
    device = TOTPDevice.objects.create(user=user, name="default", confirmed=True)
    client = Client()
    client.force_login(user)
    session = client.session
    session["otp_device_id"] = device.persistent_id
    session["django_otp_device_id"] = device.persistent_id
    session.save()
    csrf_token = client.get("/api/v1/admin/auth/csrf").json()["csrfToken"]
    client.defaults["HTTP_X_CSRFTOKEN"] = csrf_token
    return client


def _machine_headers(
    method: str,
    path: str,
    body: bytes = b"",
    secret: str = TEST_MACHINE_SECRET,
    timestamp: int | None = None,
    nonce: str | None = None,
) -> dict[str, str]:
    """Generate authenticated machine runner request headers per §I06."""
    ts = timestamp if timestamp is not None else int(time.time())
    n = nonce if nonce is not None else str(uuid.uuid4())
    sig = sign_machine_request(
        secret=secret,
        method=method,
        path=path,
        timestamp=ts,
        nonce=n,
        body_bytes=body,
    )
    return {
        "HTTP_X_PUBLICATION_TIMESTAMP": str(ts),
        "HTTP_X_PUBLICATION_NONCE": n,
        "HTTP_X_PUBLICATION_SIGNATURE": sig,
    }


def test_publication_job_model_and_wire_shape():
    """PublicationJob model stores all fields and outputs §I06 compliant wire dictionary."""
    job = PublicationJob.objects.create(
        locale="en",
        requested_revision="rev-abc-123",
        affected_paths=["/en/", "/en/blog/test-article/"],
        removal_state=RemovalState.NOT_REQUESTED,
    )
    data = job.to_dict()
    assert data["id"] == str(job.id)
    assert data["state"] == "queued"
    assert data["locale"] == "en"
    assert data["requestedRevision"] == "rev-abc-123"
    assert data["deployedRevision"] is None
    assert data["affectedPaths"] == ["/en/", "/en/blog/test-article/"]
    assert data["removalState"] == "not_requested"
    assert data["errorCode"] is None
    assert data["createdAt"].endswith("Z")
    assert data["startedAt"] is None
    assert data["finishedAt"] is None
    assert data["updatedAt"].endswith("Z")


@override_settings(PUBLICATION_MACHINE_SECRET=TEST_MACHINE_SECRET)
def test_machine_auth_header_validation():
    """Machine authentication validates freshness, signature, and rejects replay."""
    client = Client()
    job = PublicationJob.objects.create(locale="fa", requested_revision="rev-fa-1")
    path = f"/api/v1/internal/publication-jobs/{job.id}"

    # 1. Valid signature succeeds
    headers = _machine_headers("GET", path)
    res = client.get(path, **headers)
    assert res.status_code == 200
    assert res.json()["id"] == str(job.id)

    # 2. Missing headers rejected with 401
    bad_res = client.get(path)
    assert bad_res.status_code == 401

    # 3. Stale timestamp rejected with 401
    stale_ts = int(time.time()) - 400
    stale_headers = _machine_headers("GET", path, timestamp=stale_ts)
    assert client.get(path, **stale_headers).status_code == 401

    # 4. Reused nonce rejected with 401
    fixed_nonce = str(uuid.uuid4())
    headers1 = _machine_headers("GET", path, nonce=fixed_nonce)
    assert client.get(path, **headers1).status_code == 200
    # Second call with same nonce must fail
    headers2 = _machine_headers("GET", path, nonce=fixed_nonce)
    assert client.get(path, **headers2).status_code == 401

    # 5. Invalid signature rejected with 401
    invalid_headers = _machine_headers("GET", path)
    invalid_headers["HTTP_X_PUBLICATION_SIGNATURE"] = "0" * 64
    assert client.get(path, **invalid_headers).status_code == 401


@override_settings(PUBLICATION_MACHINE_SECRET=TEST_MACHINE_SECRET)
def test_invalid_signature_consumes_no_nonce_a10():
    """A10: a request with a bad signature must not create or consume a nonce.

    Regression: nonce was persisted before the HMAC check, so an attacker
    could pollute nonce state / burn a victim nonce with a forged signature.
    """
    client = Client()
    job = PublicationJob.objects.create(locale="en", requested_revision="rev-a10-1")
    path = f"/api/v1/internal/publication-jobs/{job.id}"

    forged_nonce = f"a10-forged-{uuid.uuid4().hex}"
    assert not PublicationMachineNonce.objects.filter(nonce=forged_nonce).exists()

    forged = _machine_headers("GET", path, nonce=forged_nonce)
    forged["HTTP_X_PUBLICATION_SIGNATURE"] = "0" * 64
    assert client.get(path, **forged).status_code == 401
    # The forged request must leave zero nonce state behind.
    assert not PublicationMachineNonce.objects.filter(nonce=forged_nonce).exists()

    # The same nonce with a now-valid signature must be accepted exactly once.
    valid = _machine_headers("GET", path, nonce=forged_nonce)
    assert client.get(path, **valid).status_code == 200
    assert PublicationMachineNonce.objects.filter(nonce=forged_nonce).count() == 1
    replay = _machine_headers("GET", path, nonce=forged_nonce)
    assert client.get(path, **replay).status_code == 401
    assert PublicationMachineNonce.objects.filter(nonce=forged_nonce).count() == 1


@override_settings(PUBLICATION_MACHINE_SECRET=TEST_MACHINE_SECRET)
def test_stale_timestamp_consumes_no_nonce_a10():
    """A10: stale-timestamp rejections must not leave nonce state behind."""
    client = Client()
    job = PublicationJob.objects.create(locale="en", requested_revision="rev-a10-2")
    path = f"/api/v1/internal/publication-jobs/{job.id}"

    stale_nonce = f"a10-stale-{uuid.uuid4().hex}"
    stale = _machine_headers(
        "GET", path, timestamp=int(time.time()) - 400, nonce=stale_nonce
    )
    assert client.get(path, **stale).status_code == 401
    assert not PublicationMachineNonce.objects.filter(nonce=stale_nonce).exists()


@override_settings(PUBLICATION_MACHINE_SECRET="")
def test_machine_auth_fails_closed_when_secret_unconfigured():
    """Machine endpoints fail closed (HTTP 403) when secret is empty."""
    client = Client()
    job = PublicationJob.objects.create(locale="en")
    path = f"/api/v1/internal/publication-jobs/{job.id}"
    res = client.get(path, **_machine_headers("GET", path, secret="something"))
    assert res.status_code == 403
    assert res.json()["code"] == "AUTH_FAILED"


@override_settings(PUBLICATION_MACHINE_SECRET=TEST_MACHINE_SECRET)
def test_machine_runner_callback_workflow_and_compare_and_set():
    """Runner reads payload, posts transitions, compare-and-set enforces terminal states."""
    client = Client()
    job = PublicationJob.objects.create(
        locale="en",
        requested_revision="rev-runner-100",
        affected_paths=["/en/resources/test/"],
        revoked_paths=["/en/resources/test/"],
        removal_state="pending",
    )
    detail_path = f"/api/v1/internal/publication-jobs/{job.id}"
    result_path = f"/api/v1/internal/publication-jobs/{job.id}/result"

    # Step 1: Runner fetches payload
    get_res = client.get(detail_path, **_machine_headers("GET", detail_path))
    assert get_res.status_code == 200
    payload = get_res.json()
    assert payload["requestedRevision"] == "rev-runner-100"
    assert payload["state"] == "queued"

    # Step 2: Runner reports running state
    body_running = json.dumps({"state": "running"}).encode("utf-8")
    headers_running = _machine_headers("POST", result_path, body=body_running)
    res_run = client.post(
        result_path,
        data=body_running,
        content_type="application/json",
        **headers_running,
    )
    assert res_run.status_code == 200
    assert res_run.json()["state"] == "running"
    assert res_run.json()["startedAt"] is not None

    # Step 3: Succeeded state with mismatched revision is rejected (400)
    body_mismatch = json.dumps({
        "state": "succeeded",
        "artifactRevision": "wrong-revision",
    }).encode("utf-8")
    headers_mismatch = _machine_headers("POST", result_path, body=body_mismatch)
    res_bad = client.post(
        result_path,
        data=body_mismatch,
        content_type="application/json",
        **headers_mismatch,
    )
    assert res_bad.status_code == 400
    assert res_bad.json()["code"] == "VALIDATION_FAILED"

    # Step 4: Succeeded state with matching revision succeeds and updates removalState
    body_succ = json.dumps({
        "state": "succeeded",
        "artifactRevision": "rev-runner-100",
        "removalState": "effective",
    }).encode("utf-8")
    headers_succ = _machine_headers("POST", result_path, body=body_succ)
    res_succ = client.post(
        result_path,
        data=body_succ,
        content_type="application/json",
        **headers_succ,
    )
    assert res_succ.status_code == 200
    succ_json = res_succ.json()
    assert succ_json["state"] == "succeeded"
    assert succ_json["deployedRevision"] == "rev-runner-100"
    assert succ_json["removalState"] == "effective"
    assert succ_json["finishedAt"] is not None

    # Step 5: Idempotent replay of identical succeeded result returns 200
    headers_replay = _machine_headers("POST", result_path, body=body_succ)
    res_replay = client.post(
        result_path,
        data=body_succ,
        content_type="application/json",
        **headers_replay,
    )
    assert res_replay.status_code == 200
    assert res_replay.json()["state"] == "succeeded"

    # Step 6: Incompatible terminal transition (succeeded -> failed) returns 409 Conflict
    body_conflict = json.dumps({"state": "failed", "errorCode": "BUILD_FAILED"}).encode("utf-8")
    headers_conflict = _machine_headers("POST", result_path, body=body_conflict)
    res_conf = client.post(
        result_path,
        data=body_conflict,
        content_type="application/json",
        **headers_conflict,
    )
    assert res_conf.status_code == 409
    assert res_conf.json()["code"] == "CONFLICT"


def test_admin_publication_jobs_list_filter_and_detail(admin_client):
    """Admin API exposes paged and filterable publication jobs with security controls."""
    # 1. Anonymous access fails
    anon = Client()
    assert anon.get("/api/v1/admin/publication-jobs").status_code == 401

    # 2. Seed jobs
    PublicationJob.objects.all().delete()
    job1 = PublicationJob.objects.create(
        locale="en", state="succeeded", requested_revision="r1"
    )
    job2 = PublicationJob.objects.create(
        locale="fa", state="failed", error_code="BUILD_FAILED", requested_revision="r2"
    )
    PublicationJob.objects.create(
        locale="en", state="queued", requested_revision="r3"
    )

    # 3. List all
    res = admin_client.get("/api/v1/admin/publication-jobs")
    assert res.status_code == 200
    data = res.json()
    assert data["count"] == 3
    assert len(data["items"]) == 3

    # 4. Filter by state
    res_state = admin_client.get("/api/v1/admin/publication-jobs?state=failed")
    assert res_state.status_code == 200
    assert res_state.json()["count"] == 1
    assert res_state.json()["items"][0]["id"] == str(job2.id)

    # 5. Filter by locale
    res_loc = admin_client.get("/api/v1/admin/publication-jobs?locale=en")
    assert res_loc.status_code == 200
    assert res_loc.json()["count"] == 2

    # 6. Single detail
    detail = admin_client.get(f"/api/v1/admin/publication-jobs/{job1.id}")
    assert detail.status_code == 200
    assert detail.json()["id"] == str(job1.id)
    assert detail.json()["requestedRevision"] == "r1"


def test_admin_publication_jobs_retry_idempotent(admin_client):
    """Admin retry requires If-Match, reuses existing job with Idempotency-Key."""
    job = PublicationJob.objects.create(
        locale="en",
        state="failed",
        error_code="BUILD_FAILED",
        requested_revision="rev-retry-test",
        affected_paths=["/en/about/"],
    )

    # 1. Stale If-Match returns 412
    stale_res = admin_client.post(
        f"/api/v1/admin/publication-jobs/{job.id}/retry",
        data="{}",
        content_type="application/json",
        HTTP_IF_MATCH="2020-01-01T00:00:00Z",
    )
    assert stale_res.status_code == 412

    # 2. Matching If-Match + Idempotency-Key creates retry job
    fresh_updated_at = job.updated_at.isoformat()
    retry_key = f"retry-key-{uuid.uuid4()}"
    res1 = admin_client.post(
        f"/api/v1/admin/publication-jobs/{job.id}/retry",
        data="{}",
        content_type="application/json",
        HTTP_IF_MATCH=fresh_updated_at,
        HTTP_IDEMPOTENCY_KEY=retry_key,
    )
    assert res1.status_code in (200, 201)
    retried_job1 = res1.json()
    assert retried_job1["state"] == "queued"
    assert retried_job1["requestedRevision"] == "rev-retry-test"
    assert retried_job1["affectedPaths"] == ["/en/about/"]

    # 3. Repeated request with same Idempotency-Key returns EXACT same retry job
    res2 = admin_client.post(
        f"/api/v1/admin/publication-jobs/{job.id}/retry",
        data="{}",
        content_type="application/json",
        HTTP_IF_MATCH=fresh_updated_at,
        HTTP_IDEMPOTENCY_KEY=retry_key,
    )
    assert res2.status_code in (200, 201)
    assert res2.json()["id"] == retried_job1["id"]
