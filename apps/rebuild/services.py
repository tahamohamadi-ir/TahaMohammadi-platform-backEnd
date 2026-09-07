"""Rebuild trigger and publication jobs services (P3-08, PU-07-jobs).

Implements HMAC-SHA256 signed triggers, machine runner authentication,
job persistence, and transactional compare-and-set result transitions.
Contract: Docs/03-contracts/PRODUCT-INTERFACES-V2.md §I06.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.rebuild.models import (
    PublicationJob,
    PublicationJobState,
    PublicationMachineNonce,
    RemovalState,
)

MESSAGE_PREFIX = "taha-rebuild"
MAX_TRIGGER_AGE_SECONDS = 300
logger = logging.getLogger(__name__)

SAFE_ERROR_CODES: set[str] = {
    "BUILD_FAILED",
    "PAGEFIND_FAILED",
    "SITEMAP_FAILED",
    "EDGE_DENY_FAILED",
    "REMOVAL_FAILED",
    "TIMEOUT",
    "VALIDATION_FAILED",
}


def _sign(secret: str, timestamp: int) -> str:
    message = f"{MESSAGE_PREFIX}:{timestamp}"
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def validate_rebuild_token(token: str, secret: str, timestamp: int) -> bool:
    """Return True when ``token`` matches the HMAC of ``taha-rebuild:<timestamp>``."""
    if not secret:
        return False
    return hmac.compare_digest(token, _sign(secret, timestamp))


def build_signed_rebuild_url(secret: str, base_url: str) -> str:
    """Return a rebuild-trigger URL carrying a fresh token and its timestamp."""
    timestamp = int(time.time())
    query = urlencode({"token": _sign(secret, timestamp), "timestamp": timestamp})
    return f"{base_url.rstrip('/')}/rebuild-trigger/?{query}"


def rebuild_script_path() -> Path:
    configured = getattr(settings, "REBUILD_SCRIPT_PATH", "") or ""
    if str(configured).strip():
        return Path(str(configured))
    here = Path(__file__).resolve()
    if len(here.parents) > 4:
        return here.parents[4] / "infra" / "deploy" / "rebuild-web.sh"
    return Path("/nonexistent-rebuild-web.sh")


def invoke_static_rebuild(*, enabled: bool | None = None, job_id: str | None = None) -> bool:
    """Dispatch a publication build after transaction commit (A03, §I06).

    Prefers the standalone product runner (``PUBLICATION_RUNNER_ARGV`` + job
    UUID, see ``Infra/staging/rebuild-product.py``); falls back to the legacy
    loopback script for signed-trigger compatibility until the replacement is
    verified. Never raises: a dispatch failure only skips the background
    trigger — the persisted job stays queued for the operator/runner.
    """
    if enabled is None:
        enabled = bool(getattr(settings, "REBUILD_TRIGGER_ENABLED", False))
    if not enabled:
        return False
    runner_argv = getattr(settings, "PUBLICATION_RUNNER_ARGV", None) or []
    if runner_argv:
        cmd = [str(part) for part in runner_argv]
        if job_id:
            cmd.append(str(job_id))
        try:
            subprocess.Popen(  # noqa: S603
                cmd,
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            logger.exception("failed to dispatch publication runner")
            return False
        logger.info("publication runner dispatched for job %s", job_id)
        return True
    script = rebuild_script_path()
    if not script.is_file():
        logger.warning("rebuild script missing: %s", script)
        return False
    try:
        subprocess.Popen(  # noqa: S603
            ["bash", str(script)],
            cwd=str(script.parent),
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        logger.exception("failed to start rebuild script")
        return False
    logger.info("rebuild script started: %s", script)
    return True


def new_job_revision() -> str:
    """Generate an opaque, traceable revision string (A03, §I06)."""
    stamp = timezone.now().strftime("%Y%m%dT%H%M%SZ")
    return f"rev-{stamp}-{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# Machine Authentication & Publication Jobs (PU-07-jobs, §I06)
# ---------------------------------------------------------------------------


def get_publication_machine_secret() -> str:
    """Return configured machine secret or fallback to rebuild secret."""
    secret = getattr(settings, "PUBLICATION_MACHINE_SECRET", "")
    if secret:
        return str(secret)
    return str(getattr(settings, "REBUILD_TRIGGER_SECRET", "") or "")


def sign_machine_request(
    secret: str,
    method: str,
    path: str,
    timestamp: int,
    nonce: str,
    body_bytes: bytes,
) -> str:
    """Produce hex HMAC-SHA256 signature for machine runner requests per §I06.

    Signed text joins uppercase method, path, timestamp, nonce, and hex SHA256
    of exact body bytes with newline separators.
    """
    body_sha256 = hashlib.sha256(body_bytes).hexdigest()
    signed_text = f"{method.upper()}\n{path}\n{timestamp}\n{nonce}\n{body_sha256}"
    return hmac.new(secret.encode("utf-8"), signed_text.encode("utf-8"), hashlib.sha256).hexdigest()


def validate_machine_request(request) -> tuple[bool, str, int]:
    """Validate machine runner headers against replay, skew, and bad signatures.

    Returns ``(is_valid, error_message, status_code)``.
    """
    secret = get_publication_machine_secret()
    if not secret.strip():
        return False, "Machine authentication secret not configured", 403

    raw_ts = request.headers.get("X-Publication-Timestamp") or request.META.get(
        "HTTP_X_PUBLICATION_TIMESTAMP"
    )
    nonce = request.headers.get("X-Publication-Nonce") or request.META.get(
        "HTTP_X_PUBLICATION_NONCE"
    )
    signature = request.headers.get("X-Publication-Signature") or request.META.get(
        "HTTP_X_PUBLICATION_SIGNATURE"
    )

    if not raw_ts or not nonce or not signature:
        return False, "Missing machine authentication headers", 401

    try:
        ts = int(raw_ts)
    except (ValueError, TypeError):
        return False, "Invalid timestamp format", 401

    if abs(int(time.time()) - ts) > MAX_TRIGGER_AGE_SECONDS:
        return False, "Stale timestamp", 401

    if not nonce or len(nonce) > 64:
        return False, "Invalid nonce", 401

    # A10: verify the HMAC signature BEFORE persisting any nonce state, so a
    # forged request can neither pollute the nonce table nor burn a nonce.
    body_bytes = request.body if hasattr(request, "body") else b""
    expected_sig = sign_machine_request(
        secret=secret,
        method=request.method,
        path=request.path,
        timestamp=ts,
        nonce=nonce,
        body_bytes=body_bytes,
    )

    if not hmac.compare_digest(signature, expected_sig):
        return False, "Invalid signature", 401

    # Signature is valid: atomically claim the nonce; a duplicate means replay.
    try:
        with transaction.atomic():
            PublicationMachineNonce.objects.create(nonce=nonce)
    except IntegrityError:
        return False, "Reused nonce", 401

    return True, "", 200


def enqueue_publication_job(
    *,
    locale: str | None = None,
    requested_revision: str = "",
    affected_paths: list[str] | None = None,
    revoked_paths: list[str] | None = None,
    idempotency_key: str | None = None,
    removal_state: str = RemovalState.NOT_REQUESTED,
) -> PublicationJob:
    """Persist an idempotent publication job record.

    A03: every job carries a valid non-empty opaque revision, and the build
    dispatch fires only after the caller's transaction commits (carrying the
    job UUID), never synchronously inside it. A rolled-back transaction
    dispatches nothing.
    """
    if idempotency_key:
        existing = PublicationJob.objects.filter(idempotency_key=idempotency_key).first()
        if existing:
            return existing

    if not (requested_revision or "").strip():
        requested_revision = new_job_revision()

    job = PublicationJob.objects.create(
        idempotency_key=idempotency_key,
        state=PublicationJobState.QUEUED,
        locale=locale,
        requested_revision=requested_revision,
        affected_paths=affected_paths or [],
        revoked_paths=revoked_paths or [],
        removal_state=removal_state,
    )

    job_pk = str(job.id)
    transaction.on_commit(lambda: invoke_static_rebuild(job_id=job_pk))
    return job


def record_job_result(
    job_id: str | uuid.UUID,
    *,
    state: str,
    artifact_revision: str | None = None,
    error_code: str | None = None,
    removal_state: str | None = None,
) -> tuple[int, dict[str, Any]]:
    """Transactional compare-and-set for runner publication result callback."""
    with transaction.atomic():
        try:
            job = PublicationJob.objects.select_for_update().get(id=job_id)
        except PublicationJob.DoesNotExist:
            return 404, {"code": "NOT_FOUND", "message": f"Job {job_id} not found"}

        if state not in (
            PublicationJobState.RUNNING,
            PublicationJobState.SUCCEEDED,
            PublicationJobState.FAILED,
        ):
            return 400, {"code": "VALIDATION_FAILED", "message": f"Invalid state {state}"}

        # Terminal state check: idempotent replay vs 409 conflict
        if job.state in (PublicationJobState.SUCCEEDED, PublicationJobState.FAILED):
            if job.state == state:
                if state == PublicationJobState.SUCCEEDED:
                    if not artifact_revision or job.deployed_revision == artifact_revision:
                        return 200, job.to_dict()
                elif state == PublicationJobState.FAILED:
                    if not error_code or job.error_code == error_code:
                        return 200, job.to_dict()
            return 409, {
                "code": "CONFLICT",
                "message": f"Job {job_id} is already in terminal state '{job.state}'",
            }

        if state == PublicationJobState.RUNNING:
            # A03: atomic claim. Only queued→running claims the job; a second
            # claim (concurrent runner) is rejected so two workers never build
            # the same job. Recovery after a worker cut is via admin retry,
            # which enqueues a fresh job.
            if job.state != PublicationJobState.QUEUED:
                return 409, {
                    "code": "CONFLICT",
                    "message": f"Job {job_id} is already claimed (state '{job.state}')",
                }
            job.state = PublicationJobState.RUNNING
            if not job.started_at:
                job.started_at = timezone.now()

        elif state == PublicationJobState.SUCCEEDED:
            # A03: terminal results require a prior claim.
            if job.state != PublicationJobState.RUNNING:
                return 409, {
                    "code": "CONFLICT",
                    "message": f"Job {job_id} must be claimed (running) before completion",
                }
            # A03: succeeded demands a valid non-empty artifact revision that
            # matches the requested publication snapshot.
            if not (artifact_revision or "").strip():
                return 400, {
                    "code": "VALIDATION_FAILED",
                    "message": "artifactRevision is required for succeeded results",
                }
            if (
                job.requested_revision
                and artifact_revision != job.requested_revision
            ):
                return 400, {
                    "code": "VALIDATION_FAILED",
                    "message": "artifactRevision does not match requested publication revision",
                }
            job.state = PublicationJobState.SUCCEEDED
            job.deployed_revision = artifact_revision or job.requested_revision
            if not job.started_at:
                job.started_at = timezone.now()
            job.finished_at = timezone.now()

        elif state == PublicationJobState.FAILED:
            # A03: failures also require a prior claim (no phantom failures).
            if job.state != PublicationJobState.RUNNING:
                return 409, {
                    "code": "CONFLICT",
                    "message": f"Job {job_id} must be claimed (running) before completion",
                }
            if not error_code or error_code not in SAFE_ERROR_CODES:
                error_code = "BUILD_FAILED"
            job.state = PublicationJobState.FAILED
            job.error_code = error_code
            if not job.started_at:
                job.started_at = timezone.now()
            job.finished_at = timezone.now()

        if removal_state:
            if removal_state in (
                RemovalState.EFFECTIVE,
                RemovalState.FAILED,
            ):
                # A03: effective/failed removal is only meaningful for a job
                # whose removal was requested (pending). Publish jobs must not
                # report removal outcomes.
                if job.removal_state != RemovalState.PENDING:
                    return 400, {
                        "code": "VALIDATION_FAILED",
                        "message": "removalState effective/failed requires a pending removal",
                    }
                job.removal_state = removal_state
            elif removal_state in (
                RemovalState.PENDING,
                RemovalState.NOT_REQUESTED,
            ):
                job.removal_state = removal_state

        job.save()
        return 200, job.to_dict()


def compute_affected_paths(entity: str, item) -> list[str]:
    """Compute canonical affected frontend paths for a content entity per §I02.

    Returns deduplicated, normalized paths including detail, parent, list,
    and locale home pages.
    """
    locale = getattr(item, "locale", "en") or "en"
    slug = getattr(item, "slug", "") or ""
    paths = [f"/{locale}/"]

    normalized = str(entity).lower().replace("_", "-")

    if normalized == "landing":
        if slug and slug != "home":
            paths.append(f"/{locale}/{slug}/")
    elif normalized == "profile":
        paths.append(f"/{locale}/about/")
        if slug and slug != "index":
            paths.append(f"/{locale}/about/{slug}/")
    elif normalized == "article":
        paths.append(f"/{locale}/blog/")
        if slug:
            paths.append(f"/{locale}/blog/{slug}/")
        series_rel = getattr(item, "series", None)
        if series_rel is not None:
            if hasattr(series_rel, "all"):
                for s in series_rel.all():
                    if getattr(s, "slug", ""):
                        paths.append(f"/{locale}/blog/series/{s.slug}/")
            elif hasattr(series_rel, "slug") and series_rel.slug:
                paths.append(f"/{locale}/blog/series/{series_rel.slug}/")
    elif normalized == "series":
        paths.append(f"/{locale}/blog/")
        if slug:
            paths.append(f"/{locale}/blog/series/{slug}/")
    elif normalized in ("research-topic", "researchtopic"):
        paths.append(f"/{locale}/research/")
        if slug:
            paths.append(f"/{locale}/research/{slug}/")
    elif normalized in ("research-statement", "researchstatement"):
        paths.append(f"/{locale}/research/")
        if slug:
            paths.append(f"/{locale}/research/statements/{slug}/")
    elif normalized == "project":
        paths.append(f"/{locale}/projects/")
        if slug:
            paths.append(f"/{locale}/projects/{slug}/")
    elif normalized == "publication":
        paths.append(f"/{locale}/publications/")
        if slug:
            paths.append(f"/{locale}/publications/{slug}/")
    elif normalized == "book":
        paths.append(f"/{locale}/books/")
        if slug:
            paths.append(f"/{locale}/books/{slug}/")
    elif normalized == "talk":
        paths.append(f"/{locale}/talks/")
        if slug:
            paths.append(f"/{locale}/talks/{slug}/")
    elif normalized == "download":
        paths.append(f"/{locale}/resources/")
        if slug:
            paths.append(f"/{locale}/resources/{slug}/")
            paths.append(f"/{locale}/resources/{slug}/file/")
    elif normalized == "course":
        paths.append(f"/{locale}/education/")
        if slug:
            paths.append(f"/{locale}/education/{slug}/")
    elif normalized in ("creative-work", "creativework"):
        paths.append(f"/{locale}/gallery/")
        if slug:
            paths.append(f"/{locale}/gallery/{slug}/")
    elif normalized == "collection":
        paths.append(f"/{locale}/collections/")
        if slug:
            paths.append(f"/{locale}/collections/{slug}/")
    elif normalized == "lesson":
        paths.append(f"/{locale}/education/")
        course = getattr(item, "course", None)
        if course and getattr(course, "slug", ""):
            paths.append(f"/{locale}/education/{course.slug}/")
            if slug:
                paths.append(f"/{locale}/education/{course.slug}/lessons/{slug}/")

    return list(dict.fromkeys(paths))


def compute_revoked_paths(entity: str, item) -> list[str]:
    """Compute edge-deny paths for a content entity (A01, §I06).

    Only the record's own detail/file URLs are revoked on archive/unpublish.
    Shared pages (locale home, list indexes, series/parent aggregates) are
    never revoked; they are refreshed through the rebuild (``affected_paths``).
    Unknown entities or missing slugs revoke nothing (fail-closed toward
    availability of shared pages, never toward mass denial).
    """
    locale = getattr(item, "locale", "en") or "en"
    slug = getattr(item, "slug", "") or ""
    if not slug:
        return []

    normalized = str(entity).lower().replace("_", "-")

    if normalized == "landing":
        if slug == "home":
            return []
        return [f"/{locale}/{slug}/"]
    if normalized == "profile":
        if slug == "index":
            return []
        return [f"/{locale}/about/{slug}/"]
    if normalized == "article":
        return [f"/{locale}/blog/{slug}/"]
    if normalized == "series":
        return [f"/{locale}/blog/series/{slug}/"]
    if normalized in ("research-topic", "researchtopic"):
        return [f"/{locale}/research/{slug}/"]
    if normalized in ("research-statement", "researchstatement"):
        return [f"/{locale}/research/statements/{slug}/"]
    if normalized == "project":
        return [f"/{locale}/projects/{slug}/"]
    if normalized == "publication":
        return [f"/{locale}/publications/{slug}/"]
    if normalized == "book":
        return [f"/{locale}/books/{slug}/"]
    if normalized == "talk":
        return [f"/{locale}/talks/{slug}/"]
    if normalized == "download":
        return [f"/{locale}/resources/{slug}/", f"/{locale}/resources/{slug}/file/"]
    if normalized == "course":
        return [f"/{locale}/education/{slug}/"]
    if normalized in ("creative-work", "creativework"):
        return [f"/{locale}/gallery/{slug}/"]
    if normalized == "collection":
        return [f"/{locale}/collections/{slug}/"]
    if normalized == "lesson":
        course = getattr(item, "course", None)
        course_slug = getattr(course, "slug", "") if course else ""
        if not course_slug:
            return []
        return [f"/{locale}/education/{course_slug}/lessons/{slug}/"]
    return []
def enqueue_content_invalidation(
    entity: str,
    item,
    *,
    action: str,
    revision: str = "",
    removal_state: str | None = None,
) -> PublicationJob:
    """Derive affected canonical paths and enqueue a publication invalidation job.

    A01: ``affected_paths`` is the rebuild set (detail + shared pages so fresh
    indexes/search drop the reference); ``revoked_paths`` is the deny set
    (own detail/file URLs only, never shared pages).
    """
    locale = getattr(item, "locale", None)
    paths = compute_affected_paths(entity, item)

    if removal_state is None:
        if action in ("archive", "unpublish", "delete", "restore_draft"):
            removal_state = RemovalState.PENDING
        else:
            removal_state = RemovalState.NOT_REQUESTED

    if removal_state == RemovalState.PENDING:
        revoked = compute_revoked_paths(entity, item)
    else:
        revoked = []

    rev_str = revision
    if not rev_str and hasattr(item, "updated_at") and item.updated_at:
        rev_str = item.updated_at.isoformat()

    return enqueue_publication_job(
        locale=locale,
        requested_revision=str(rev_str),
        affected_paths=paths,
        revoked_paths=revoked,
        removal_state=removal_state,
    )
