"""Immutable content snapshots and restore-as-draft (ADM-4 / DEBT-0005)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from django.db import models
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.content.models import ContentRevision, LifecycleStatus

# Attribute names snapshotted for every lifecycle entity (plus DETAIL_FIELD_MAPS).
_IDENTITY_ATTRS = ("locale", "slug", "title", "status", "published_at", "scheduled_for")


def _serialize_value(value: Any) -> Any:
    """JSON-safe encoding for snapshot payloads."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, models.Model):
        return value.pk
    return value


def build_snapshot(item, field_attrs: dict[str, str]) -> dict[str, Any]:
    """Build an immutable snapshot dict for ``item``.

    ``field_attrs`` maps model attribute → API key (DETAIL_FIELD_MAPS[entity]).
    Foreign keys are stored as primary keys under the model attribute name.
    """
    payload: dict[str, Any] = {}
    for attr in _IDENTITY_ATTRS:
        payload[attr] = _serialize_value(getattr(item, attr, None))
    fields: dict[str, Any] = {}
    for attr in field_attrs:
        field = item._meta.get_field(attr)
        if isinstance(field, models.ForeignKey):
            fields[attr] = getattr(item, field.attname)
        else:
            fields[attr] = _serialize_value(getattr(item, attr))
    payload["fields"] = fields
    return payload


def create_revision(
    *,
    entity_key: str,
    item,
    field_attrs: dict[str, str],
    user=None,
    note: str = "",
) -> ContentRevision:
    """Persist an immutable snapshot of the current row state."""
    return ContentRevision.objects.create(
        entity_key=entity_key,
        object_id=item.pk,
        snapshot=build_snapshot(item, field_attrs),
        note=(note or "")[:200],
        created_by=user if getattr(user, "is_authenticated", False) else None,
    )


def _parse_dt(raw: Any) -> datetime | None:
    if raw in (None, ""):
        return None
    if isinstance(raw, datetime):
        return raw
    parsed = parse_datetime(str(raw))
    if parsed is None:
        raise ValueError(f"Invalid datetime: {raw!r}")
    if timezone.is_naive(parsed):
        return timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _parse_date(raw: Any) -> date | None:
    if raw in (None, ""):
        return None
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    return date.fromisoformat(str(raw)[:10])


def apply_snapshot_as_draft(item, snapshot: dict[str, Any], field_attrs: dict[str, str]) -> None:
    """Apply snapshot fields onto ``item`` and force draft (never leave published).

    Mutates ``item`` in memory; caller is responsible for ``save()``.
    """
    if "locale" in snapshot:
        item.locale = snapshot["locale"]
    if "slug" in snapshot:
        item.slug = snapshot["slug"]
    if "title" in snapshot:
        item.title = snapshot["title"]
    item.published_at = _parse_dt(snapshot.get("published_at"))
    # Restore-as-draft: never re-activate scheduled/published from a restore.
    item.status = LifecycleStatus.DRAFT
    item.scheduled_for = None

    fields = snapshot.get("fields") or {}
    for attr in field_attrs:
        if attr not in fields:
            continue
        field = item._meta.get_field(attr)
        raw = fields[attr]
        if isinstance(field, models.ForeignKey):
            setattr(item, field.attname, raw)
        elif isinstance(field, models.DateTimeField):
            setattr(item, attr, _parse_dt(raw))
        elif isinstance(field, models.DateField):
            setattr(item, attr, _parse_date(raw))
        elif isinstance(field, models.BooleanField):
            setattr(item, attr, bool(raw) if raw is not None else False)
        elif isinstance(field, models.IntegerField):
            setattr(item, attr, None if raw in (None, "") else int(raw))
        else:
            setattr(item, attr, "" if raw is None else raw)


def restore_revision_as_draft(
    *,
    entity_key: str,
    item,
    revision: ContentRevision,
    field_attrs: dict[str, str],
    user=None,
) -> ContentRevision:
    """Snapshot the live row, then apply ``revision`` as draft.

    Returns the newly created pre-restore snapshot (immutable audit of live state).
    """
    if revision.entity_key != entity_key or revision.object_id != item.pk:
        raise ValueError("Revision does not belong to this content row.")
    pre = create_revision(
        entity_key=entity_key,
        item=item,
        field_attrs=field_attrs,
        user=user,
        note="pre-restore snapshot",
    )
    apply_snapshot_as_draft(item, revision.snapshot, field_attrs)
    item.save()
    return pre
