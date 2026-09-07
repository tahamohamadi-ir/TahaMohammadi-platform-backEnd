"""Published-document resolution for public reads (A04, PRODUCT-INTERFACES-V2 §I03).

Contract rule: "A draft edit must not change the currently published document
until explicit publication." Publication snapshots (``PublicationSnapshot``,
recorded on every publish) are the published document. While the live row is a
non-public draft workspace (after a draft edit or a restore-as-draft), public
reads fall back to the latest publication snapshot for the same
``(entity, locale, slug)`` instead of 404ing.

Explicit archive invalidates snapshots via :func:`invalidate_published_snapshots`,
so archived content stays gone. As a belt-and-braces guard, the fallback also
refuses to serve when the live row is ``archived``.
"""

from __future__ import annotations

import datetime
from typing import Any

from django.db import models
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from apps.content.models import PublicationSnapshot

ARCHIVED_STATUS = "archived"
PUBLISHED_STATUS = "published"

# Resolver/graph family (lowercase model name) -> admin entity key used in snapshots.
FAMILY_TO_ENTITY_KEY: dict[str, str] = {
    "landing": "landing",
    "profile": "profile",
    "article": "article",
    "series": "series",
    "researchtopic": "research-topic",
    "researchstatement": "research-statement",
    "project": "project",
    "publication": "publication",
    "book": "book",
    "talk": "talk",
    "download": "download",
    "course": "course",
    "creativework": "creative-work",
    "lesson": "lesson",
    "collection": "collection",
}


def invalidate_published_snapshots(entity_key: str, object_id: int) -> int:
    """Delete publication snapshots for an explicitly archived object.

    Returns the number of deleted rows. Called on archive/unpublish so the
    fallback in this module can never resurrect revoked content.
    """
    deleted, _ = PublicationSnapshot.objects.filter(
        entity_key=entity_key, object_id=object_id
    ).delete()
    return int(deleted)


def latest_published_snapshot(
    entity_key: str,
    *,
    object_id: int | None = None,
    locale: str | None = None,
    slug: str | None = None,
) -> PublicationSnapshot | None:
    """Return the newest publication snapshot matching the given coordinates."""
    qs = PublicationSnapshot.objects.filter(entity_key=entity_key)
    if object_id is not None:
        qs = qs.filter(object_id=object_id)
    if locale is not None:
        qs = qs.filter(locale=locale)
    if slug is not None:
        qs = qs.filter(slug=slug)
    return qs.order_by("-published_at", "-id").first()


def _parse_dt(raw: Any) -> datetime.datetime | None:
    if raw in (None, ""):
        return None
    if isinstance(raw, datetime.datetime):
        parsed = raw
    else:
        parsed = parse_datetime(str(raw))
    if parsed is None:
        return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, datetime.UTC)
    return parsed


def _parse_d(raw: Any) -> datetime.date | None:
    if raw in (None, ""):
        return None
    if isinstance(raw, datetime.datetime):
        return raw.date()
    if isinstance(raw, datetime.date):
        return raw
    try:
        return parse_date(str(raw)[:10])
    except (ValueError, TypeError):
        return None


def materialize_published_item(model: type[models.Model], snapshot: PublicationSnapshot):
    """Build an in-memory instance carrying the published field values.

    Snapshot scalars/FKs overwrite the draft values in memory only. Reverse
    project relations (evidence/collaborators/funding/case-study) are attached
    as frozen snapshot data so public resolvers serve the published version
    instead of the live draft managers. Returns ``None`` when the live row no
    longer exists. Never saves.
    """
    try:
        item = model.objects.get(pk=snapshot.object_id)
    except model.DoesNotExist:
        return None
    data = snapshot.snapshot if isinstance(snapshot.snapshot, dict) else {}

    for attr in ("locale", "slug", "title"):
        value = data.get(attr)
        if value is not None:
            try:
                setattr(item, attr, value)
            except (ValueError, TypeError):
                continue
    published_at = _parse_dt(data.get("published_at"))
    if published_at is not None:
        item.published_at = published_at

    fields = data.get("fields")
    if isinstance(fields, dict):
        for attr, raw in fields.items():
            try:
                field = model._meta.get_field(attr)
            except Exception:  # noqa: BLE001 — unknown snapshot keys are skipped
                continue
            try:
                if isinstance(field, models.ForeignKey):
                    setattr(item, field.attname, raw)
                elif isinstance(field, models.DateTimeField):
                    setattr(item, attr, _parse_dt(raw))
                elif isinstance(field, models.DateField):
                    setattr(item, attr, _parse_d(raw))
                elif isinstance(field, models.BooleanField):
                    setattr(item, attr, bool(raw) if raw is not None else False)
                elif isinstance(field, models.IntegerField):
                    setattr(item, attr, None if raw in (None, "") else int(raw))
                else:
                    setattr(item, attr, "" if raw is None else raw)
            except (ValueError, TypeError):
                continue

    # In-memory marker only: serializers treat this as the published document.
    item.status = PUBLISHED_STATUS
    item._published_snapshot_id = snapshot.pk  # type: ignore[attr-defined]
    # Frozen project relations for snapshot-aware public resolvers (A04).
    pcs = data.get("project_case_study")
    item._published_project_case_study = pcs if isinstance(pcs, dict) else None  # type: ignore[attr-defined]
    item._published_snapshot_data = data if isinstance(data, dict) else {}  # type: ignore[attr-defined]
    return item


def _live_row_allows_fallback(model: type[models.Model], object_id: int) -> bool:
    """Fallback serves only when the live row exists and is not archived."""
    try:
        live_row = model.objects.get(pk=object_id)
    except model.DoesNotExist:
        return False
    return live_row.status != ARCHIVED_STATUS


def resolve_published_detail(
    model: type[models.Model],
    entity_key: str,
    locale: str,
    slug: str,
    base_qs=None,
):
    """Resolve one published record for public detail reads.

    Prefers the live ``public()`` row (with the caller's optimizations when
    ``base_qs`` is given); falls back to the latest publication snapshot for
    the same ``(entity, locale, slug)`` while the live row is a draft.
    """
    qs = base_qs if base_qs is not None else model.objects.public()
    live = qs.filter(locale=locale, slug=slug).first()
    if live is not None:
        return live
    snapshot = latest_published_snapshot(entity_key, locale=locale, slug=slug)
    if snapshot is None:
        return None
    if not _live_row_allows_fallback(model, snapshot.object_id):
        return None
    return materialize_published_item(model, snapshot)


def resolve_published_target(
    model: type[models.Model], entity_key: str, pk: int, locale: str
):
    """Resolve one published record by PK for relations/neighbors/resolver."""
    public_mgr = getattr(model.objects, "public", None)
    if public_mgr is not None:
        live = public_mgr().filter(pk=pk, locale=locale).first()
        if live is not None:
            return live
    snapshot = latest_published_snapshot(entity_key, object_id=pk, locale=locale)
    if snapshot is None:
        return None
    if not _live_row_allows_fallback(model, snapshot.object_id):
        return None
    return materialize_published_item(model, snapshot)


def published_list_extras(
    model: type[models.Model],
    entity_key: str,
    locale: str,
    exclude_ids: set[int],
) -> list:
    """Materialize still-published records missing from a live public list.

    Returns in-memory published instances for snapshots in ``locale`` whose
    object is absent from ``exclude_ids`` and whose live row is not archived.
    Callers merge, filter (e.g. ``show_on_projects``) and sort the result.
    """
    snapshots = (
        PublicationSnapshot.objects.filter(entity_key=entity_key, locale=locale)
        .order_by("object_id", "-published_at", "-id")
        .values_list("object_id", flat=True)
        .distinct()
    )
    extras = []
    for object_id in snapshots:
        if object_id in exclude_ids:
            continue
        snapshot = latest_published_snapshot(entity_key, object_id=object_id, locale=locale)
        if snapshot is None:
            continue
        if not _live_row_allows_fallback(model, snapshot.object_id):
            continue
        item = materialize_published_item(model, snapshot)
        if item is not None:
            extras.append(item)
    return extras


def order_like(items: list, *specs: str) -> list:
    """Sort in memory like Django ``order_by(*specs)`` (None sorts smallest)."""

    def normalize(value: Any) -> Any:
        if isinstance(value, datetime.datetime):
            if timezone.is_naive(value):
                value = timezone.make_aware(value, datetime.UTC)
            return (1, value.timestamp())
        if isinstance(value, datetime.date):
            return (1, value.toordinal())
        if value is None:
            return (0, 0)
        if isinstance(value, (int, float)):
            return (1, value)
        return (1, str(value))

    ordered = list(items)
    for spec in reversed(specs):
        reverse = spec.startswith("-")
        field = spec[1:] if reverse else spec
        ordered.sort(key=lambda o: normalize(getattr(o, field, None)), reverse=reverse)
    return ordered
