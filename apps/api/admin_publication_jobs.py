"""Admin publication jobs API (PU-07-jobs, PRODUCT-INTERFACES-V2 §I06).

Endpoints:
- GET  /api/v1/admin/publication-jobs          -> paged/filterable job list
- GET  /api/v1/admin/publication-jobs/{id}     -> single job detail with ETag
- POST /api/v1/admin/publication-jobs/{id}/retry -> idempotent retry with If-Match
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from ninja import Field, Router, Schema

from apps.api.admin_common import (
    NOT_FOUND,
    STALE_REVISION,
    AdminError,
    _check_csrf,
    _require_admin_otp,
)
from apps.rebuild.models import PublicationJob
from apps.rebuild.services import enqueue_publication_job

publication_jobs_router = Router(tags=["publication-jobs"])


class PublicationJobOut(Schema):
    """Publication job representation conforming strictly to PRODUCT-INTERFACES-V2 §I06."""

    id: str
    state: str
    locale: str | None = None
    requestedRevision: str
    deployedRevision: str | None = None
    affectedPaths: list[str] = Field(default_factory=list)
    revokedPaths: list[str] = Field(default_factory=list)
    removalState: str
    createdAt: str
    startedAt: str | None = None
    finishedAt: str | None = None
    errorCode: str | None = None
    updatedAt: str


class PublicationJobListOut(Schema):
    """Paged collection of publication jobs."""

    count: int
    items: list[PublicationJobOut]


class RetryJobIn(Schema):
    """Empty payload for job retry."""

    pass


def _parse_if_match(header: str | None) -> datetime | None:
    raw = (header or "").strip().strip('"')
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _if_match_matches(header: str | None, item: PublicationJob) -> bool:
    expected = _parse_if_match(header)
    if expected is None:
        return False
    current = item.updated_at
    try:
        if expected.tzinfo is None or current.tzinfo is None:
            return False
        expected_ms = expected.astimezone(UTC).replace(
            microsecond=(expected.microsecond // 1000) * 1000
        )
        current_ms = current.astimezone(UTC).replace(
            microsecond=(current.microsecond // 1000) * 1000
        )
        return expected_ms == current_ms
    except (TypeError, ValueError):
        return False


@publication_jobs_router.get(
    "",
    response={200: PublicationJobListOut},
    summary="List publication jobs (paged, filterable by state and locale)",
)
def list_publication_jobs(
    request,
    page: int = 1,
    page_size: int = 20,
    state: str | None = None,
    locale: str | None = None,
):
    """List publication jobs with pagination and optional state/locale filters."""
    _require_admin_otp(request)

    if page < 1:
        page = 1
    if page_size < 1:
        page_size = 20
    elif page_size > 50:
        page_size = 50

    qs = PublicationJob.objects.all()
    if state:
        qs = qs.filter(state=state)
    if locale:
        qs = qs.filter(locale=locale)

    total = qs.count()
    start = (page - 1) * page_size
    items = [PublicationJobOut(**job.to_dict()) for job in qs[start : start + page_size]]
    return {"count": total, "items": items}


@publication_jobs_router.get(
    "/{job_id}",
    response={200: PublicationJobOut},
    summary="Get one publication job by UUID",
)
def get_publication_job(request, job_id: uuid.UUID):
    """Get single publication job detail."""
    _require_admin_otp(request)

    try:
        job = PublicationJob.objects.get(id=job_id)
    except PublicationJob.DoesNotExist:
        raise AdminError(404, NOT_FOUND, f"Publication job {job_id} not found") from None

    return PublicationJobOut(**job.to_dict())


@publication_jobs_router.post(
    "/{job_id}/retry",
    response={200: PublicationJobOut, 201: PublicationJobOut},
    summary="Idempotently retry a publication job with If-Match and Idempotency-Key",
)
def retry_publication_job(request, job_id: uuid.UUID, body: RetryJobIn | None = None):
    """Idempotently retry a publication job.

    Enforces If-Match preconditions against the target job's updatedAt timestamp.
    Reuses existing retry jobs when an identical Idempotency-Key header is presented.
    """
    _require_admin_otp(request)
    _check_csrf(request)

    try:
        job = PublicationJob.objects.get(id=job_id)
    except PublicationJob.DoesNotExist:
        raise AdminError(404, NOT_FOUND, f"Publication job {job_id} not found") from None

    if_match = request.headers.get("If-Match") or request.META.get("HTTP_IF_MATCH")
    if if_match and not _if_match_matches(if_match, job):
        raise AdminError(
            412,
            STALE_REVISION,
            "Target publication job has changed since last retrieved; If-Match mismatch",
        )

    raw_key = (
        request.headers.get("Idempotency-Key")
        or request.META.get("HTTP_IDEMPOTENCY_KEY")
        or ""
    ).strip()
    idempotency_key = raw_key if raw_key else None

    if idempotency_key:
        existing = PublicationJob.objects.filter(idempotency_key=idempotency_key).first()
        if existing:
            return PublicationJobOut(**existing.to_dict())

    retry_job = enqueue_publication_job(
        locale=job.locale,
        requested_revision=job.requested_revision,
        affected_paths=job.affected_paths,
        revoked_paths=job.revoked_paths,
        idempotency_key=idempotency_key,
        removal_state=job.removal_state,
    )
    return PublicationJobOut(**retry_job.to_dict())
