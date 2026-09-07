"""Content lifecycle transitions and bulk archive (ADM-4 / Wave 5 S1).

Routers stay thin: validate auth/CSRF/entity, then call these helpers inside
``transaction.atomic()``. Side effects (``published_at`` / ``scheduled_for``)
and audit rows are owned here so bulk and single-item paths stay consistent.
"""

from __future__ import annotations

from datetime import datetime

from django.db import IntegrityError
from django.utils import timezone

from apps.content.published import invalidate_published_snapshots
from apps.rebuild.services import (
    compute_affected_paths,
    compute_revoked_paths,
    enqueue_content_invalidation,
    enqueue_publication_job,
)
from apps.security.models import AuditLog

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"review", "scheduled", "published", "archived"},
    "review": {"draft", "scheduled", "published", "archived"},
    "scheduled": {"draft", "published", "archived"},
    "published": {"archived"},
    "archived": {"draft"},
}

TRANSITION_REASON_MAX = 500
BULK_ARCHIVE_MAX_IDS = 50


class LifecycleError(Exception):
    """Domain validation failure mapped to AdminError by the API layer."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def apply_lifecycle_side_effects(
    item, *, new_status: str, scheduled_for: datetime | None
) -> None:
    """Set published_at / scheduled_for consistent with the target status."""
    if new_status == "scheduled":
        if scheduled_for is None:
            raise LifecycleError(
                "VALIDATION",
                "scheduledFor is required when transitioning to scheduled.",
            )
        if timezone.is_naive(scheduled_for):
            scheduled_for = timezone.make_aware(
                scheduled_for, timezone.get_current_timezone()
            )
        if scheduled_for <= timezone.now():
            raise LifecycleError(
                "VALIDATION",
                "scheduledFor must be in the future.",
            )
        item.scheduled_for = scheduled_for
    else:
        item.scheduled_for = None
    if new_status == "published" and item.published_at is None:
        item.published_at = timezone.now()


def transition_item(
    item,
    *,
    entity: str,
    to_status: str,
    reason: str,
    scheduled_for: datetime | None,
    user,
    ip: str,
) -> str:
    """Apply one allowed lifecycle transition and write an audit row.

    Caller must hold a row lock (``select_for_update``) inside
    ``transaction.atomic()``. Returns the previous status.
    """
    old_status = item.status
    if to_status not in ALLOWED_TRANSITIONS.get(old_status, set()):
        raise LifecycleError(
            "VALIDATION",
            f"Invalid transition from {old_status} to {to_status}.",
        )
    apply_lifecycle_side_effects(
        item, new_status=to_status, scheduled_for=scheduled_for
    )
    item.status = to_status
    try:
        item.save()
    except IntegrityError as exc:
        raise LifecycleError(
            "DUPLICATE",
            "A record with this locale and slug already exists.",
        ) from exc

    clipped = (reason or "").strip()[:TRANSITION_REASON_MAX]
    detail = f"reason={clipped}"
    if to_status == "scheduled" and item.scheduled_for is not None:
        detail = f"{detail}; scheduledFor={item.scheduled_for.isoformat()}"
    AuditLog.objects.create(
        user=user,
        action=f"lifecycle.{old_status}->{to_status}",
        model_name=entity,
        object_id=str(item.pk),
        ip=ip,
        detail=detail,
    )
    if to_status in ("published", "archived"):
        enqueue_content_invalidation(
            entity=entity,
            item=item,
            action="publish" if to_status == "published" else "archive",
        )
    if to_status == "archived":
        # A04: explicit archive invalidates the published document so the
        # snapshot fallback can never resurrect revoked content.
        invalidate_published_snapshots(entity, item.pk)
    return old_status


def bulk_archive_items(
    model,
    *,
    entity: str,
    ids: list[int],
    reason: str,
    user,
    ip: str,
) -> dict:
    """Archive many rows that allow ``→ archived``; skip invalid transitions.

    Returns ``{ archived: int, skipped: int, ids: list[int] }`` for archived
    pks. Writes one audit row per archived item plus a summary row.
    """
    unique_ids = list(dict.fromkeys(ids))
    if not unique_ids:
        raise LifecycleError("VALIDATION", "ids must be a non-empty list.")
    if len(unique_ids) > BULK_ARCHIVE_MAX_IDS:
        raise LifecycleError(
            "VALIDATION",
            f"At most {BULK_ARCHIVE_MAX_IDS} ids may be archived at once.",
        )

    clipped = (reason or "").strip()[:TRANSITION_REASON_MAX]
    archived_ids: list[int] = []
    archived_items: list = []
    skipped = 0

    for pk in unique_ids:
        try:
            item = model.objects.select_for_update().get(pk=pk)
        except model.DoesNotExist:
            skipped += 1
            continue
        if "archived" not in ALLOWED_TRANSITIONS.get(item.status, set()):
            skipped += 1
            continue
        old_status = item.status
        apply_lifecycle_side_effects(
            item, new_status="archived", scheduled_for=None
        )
        item.status = "archived"
        try:
            item.save()
        except IntegrityError as exc:
            raise LifecycleError(
                "DUPLICATE",
                "A record with this locale and slug already exists.",
            ) from exc
        AuditLog.objects.create(
            user=user,
            action=f"lifecycle.{old_status}->archived",
            model_name=entity,
            object_id=str(pk),
            ip=ip,
            detail=f"reason={clipped}; bulk=1",
        )
        archived_ids.append(pk)
        archived_items.append(item)
        # A04: explicit archive invalidates the published document.
        invalidate_published_snapshots(entity, item.pk)

    if archived_items:
        # A08: one publication job per locale so paths are never attributed
        # to the wrong locale (previously a single job took the last item's
        # locale for all paths). Group deterministically by locale.
        paths_by_locale: dict[str | None, list[str]] = {}
        revoked_by_locale: dict[str | None, list[str]] = {}
        for it in archived_items:
            loc = getattr(it, "locale", None)
            paths_by_locale.setdefault(loc, []).extend(compute_affected_paths(entity, it))
            revoked_by_locale.setdefault(loc, []).extend(compute_revoked_paths(entity, it))
        for loc in sorted(paths_by_locale, key=lambda v: (v is None, v or "")):
            enqueue_publication_job(
                locale=loc,
                affected_paths=list(dict.fromkeys(paths_by_locale[loc])),
                revoked_paths=list(dict.fromkeys(revoked_by_locale.get(loc, []))),
                removal_state="pending",
            )

    AuditLog.objects.create(
        user=user,
        action="lifecycle.bulk_archive",
        model_name=entity,
        object_id="",
        ip=ip,
        detail=(
            f"reason={clipped}; count={len(archived_ids)}; "
            f"skipped={skipped}; ids={archived_ids}"
        ),
    )
    return {
        "archived": len(archived_ids),
        "skipped": skipped,
        "ids": archived_ids,
    }
