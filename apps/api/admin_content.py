"""Custom admin content API (ADR-0026, ADM-1).

Staff + OTP protected endpoints over the canonical content entities: read
(``GET /{entity}`` list, ``GET /{entity}/{id}`` detail) and the write path
(``GET /schema`` writable-field metadata, ``POST /{entity}`` create,
``PUT /{entity}/{id}`` update with optimistic locking via ``If-Match``).
Unsafe methods additionally enforce the same-origin CSRF baseline.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime

from django.db import IntegrityError, models, transaction
from django.db.models import Q
from django.http import JsonResponse
from django.utils import timezone
from ninja import Field, Router, Schema

from apps.api.admin_common import (
    AdminError,
    _check_csrf,
    _client_ip,
    _parse_positive_int,
    _require_admin_otp,
)
from apps.content.feature_flags import is_feature_enabled
from apps.content.models import (
    Article,
    Book,
    ContentRevision,
    Course,
    CreativeWork,
    Download,
    Landing,
    Profile,
    Project,
    Publication,
    ResearchStatement,
    ResearchTopic,
    Series,
    Talk,
)
from apps.content.preview_token import build_preview_share_path, preview_ttl_seconds
from apps.content.revisions import create_revision, restore_revision_as_draft
from apps.content.services.lifecycle import (
    LifecycleError,
    bulk_archive_items,
    transition_item,
)
from apps.rebuild.services import invoke_static_rebuild
from apps.security.models import AuditLog

content_router = Router()

ENTITY_MODELS = {
    "landing": Landing,
    "profile": Profile,
    "article": Article,
    "series": Series,
    "research-topic": ResearchTopic,
    "research-statement": ResearchStatement,
    "project": Project,
    "publication": Publication,
    "book": Book,
    "talk": Talk,
    "download": Download,
    "course": Course,
    "creative-work": CreativeWork,
}

PREVIEW_SHARE_ENTITIES = {
    "landing": "landing",
    "profile": "profile",
    "article": "article",
}

VALID_LOCALES = ("fa", "en")
VALID_STATUSES = ("draft", "review", "scheduled", "published", "archived")

TRANSITION_REASON_MAX = 500


def _lifecycle_admin_error(exc: LifecycleError) -> AdminError:
    status = 409 if exc.code == "DUPLICATE" else 400
    return AdminError(status, exc.code, exc.message)

# Sentinel returned by _coerce_field_value for blank numeric fields so the
# caller leaves the field unchanged instead of failing on an empty string.
_SKIP = object()


DETAIL_FIELD_MAPS: dict[str, dict[str, str]] = {
    "landing": {
        "body": "body",
        "seo_title": "seoTitle",
        "seo_description": "seoDescription",
    },
    "profile": {
        "short_bio": "shortBio",
        "long_bio": "longBio",
        "availability": "availability",
        "body": "body",
        "seo_title": "seoTitle",
        "seo_description": "seoDescription",
        "revision": "revision",
    },
    "article": {
        "excerpt": "excerpt",
        "body": "body",
        "license": "license",
        "reading_time_minutes": "readingTimeMinutes",
        "accessibility_notes": "accessibilityNotes",
        "featured_image": "featuredImageId",
        "story": "storyId",
    },
    "series": {
        "description": "description",
        "ordering": "ordering",
    },
    "research-topic": {
        "summary": "summary",
        "motivation": "motivation",
        "problems": "problems",
        "research_questions": "researchQuestions",
        "methods": "methods",
        "future_directions": "futureDirections",
        "story": "storyId",
    },
    "research-statement": {
        "body": "body",
        "statement_pdf": "statementPdfId",
        "story": "storyId",
    },
    "project": {
        "project_type": "projectType",
        "objective": "objective",
        "methods_summary": "methodsSummary",
        "role": "role",
        "start_date": "startDate",
        "end_date": "endDate",
        "license": "license",
        "code_availability": "codeAvailability",
        "data_availability": "dataAvailability",
        "demo_availability": "demoAvailability",
        "code_url": "codeUrl",
        "data_url": "dataUrl",
        "demo_url": "demoUrl",
        "show_on_projects": "showOnProjects",
        "story": "storyId",
    },
    "publication": {
        "authors": "authors",
        "venue": "venue",
        "date": "date",
        "doi": "doi",
        "url": "url",
        "pdf_url": "pdfUrl",
        "abstract": "abstract",
        "publication_type": "publicationType",
        "academic_stage": "academicStage",
        "isbn": "isbn",
        "preprint_url": "preprintUrl",
        "code_url": "codeUrl",
        "dataset_url": "datasetUrl",
        "access_state": "accessState",
        "accessibility_notes": "accessibilityNotes",
        "citation_text": "citationText",
        "pdf_media": "pdfMediaId",
        "license": "license",
        "citation_count": "citationCount",
        "citation_source": "citationSource",
        "citation_last_verified": "citationLastVerified",
        "citation_visibility": "citationVisibility",
    },
    "book": {
        "authors": "authors",
        "isbn": "isbn",
        "publisher": "publisher",
        "publication_date": "publicationDate",
        "description": "description",
        "url": "url",
        "license": "license",
        "access_state": "accessState",
        "accessibility_notes": "accessibilityNotes",
        "cover_media": "coverMediaId",
    },
    "talk": {
        "speakers": "speakers",
        "event_name": "eventName",
        "event_date": "eventDate",
        "location": "location",
        "abstract": "abstract",
        "video_url": "videoUrl",
        "slides_url": "slidesUrl",
        "license": "license",
        "access_state": "accessState",
        "accessibility_notes": "accessibilityNotes",
        "slides_media": "slidesMediaId",
    },
    "download": {
        "description": "description",
        "media": "mediaId",
        "download_type": "downloadType",
        "language": "language",
        "access_state": "accessState",
        "accessibility_notes": "accessibilityNotes",
        "license": "license",
    },
    "course": {
        "description": "description",
        "body": "body",
        "level": "level",
        "prerequisites": "prerequisites",
        "outcomes": "outcomes",
        "course_format": "courseFormat",
        "course_language": "courseLanguage",
        "availability": "availability",
        "license": "license",
        "last_updated": "lastUpdated",
        "accessibility_notes": "accessibilityNotes",
        "cover_media": "coverMediaId",
    },
    "creative-work": {
        "description": "description",
        "body": "body",
        "work_type": "workType",
        "creator_name": "creatorName",
        "creator_role": "creatorRole",
        "creation_date": "creationDate",
        "license": "license",
        "access_state": "accessState",
        "rights_statement": "rightsStatement",
        "consent_verified": "consentVerified",
        "accessibility_notes": "accessibilityNotes",
        "cover_media": "coverMediaId",
    },
}

# Writable fields = DETAIL_FIELD_MAPS minus server-managed fields (profile.revision),
# preserving the map insertion order.
WRITABLE_FIELD_MAPS: dict[str, dict[str, str]] = {
    entity: {
        attr: key
        for attr, key in DETAIL_FIELD_MAPS[entity].items()
        if attr != "revision"
    }
    for entity in ENTITY_MODELS
}

# Reversed writable lookup: camelCase field key -> model attribute name.
_FIELD_ATTRS: dict[str, dict[str, str]] = {
    entity: {key: attr for attr, key in field_map.items()}
    for entity, field_map in WRITABLE_FIELD_MAPS.items()
}


def _label_for_key(key: str) -> str:
    """camelCase key -> human label, e.g. "readingTimeMinutes" -> "Reading Time Minutes"."""
    words = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", key).split()
    return " ".join(word[:1].upper() + word[1:] for word in words)


def _field_type(field) -> str:
    """Generic form widget type for a Django model field."""
    if isinstance(field, models.BooleanField):
        return "boolean"
    if isinstance(field, models.ForeignKey):
        related = field.related_model
        if related is not None and getattr(related._meta, "label", "") == "media.Media":
            return "media"
        return "number"
    if isinstance(field, models.DateField):
        return "date"
    if isinstance(field, models.IntegerField):
        return "number"
    if isinstance(field, models.TextField):
        return "textarea"
    return "text"


def _coerce_field_value(field, attr: str, key: str, value) -> object:
    """Coerce a raw JSON value to the Django field's python type.

    ``key`` is the camelCase API key used for error reporting (the SPA maps
    field errors by schema key). Returns ``_SKIP`` for blank numeric values so
    callers can leave the field unchanged instead of failing on an empty string.
    """
    if isinstance(field, models.BooleanField):
        if value in (None, ""):
            return _SKIP
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in {"true", "1", "yes", "on"}:
            return True
        if isinstance(value, str) and value.lower() in {"false", "0", "no", "off"}:
            return False
        raise AdminError(
            400,
            "VALIDATION",
            f"Invalid boolean for '{key}'.",
            fields={"fields": [key]},
        )
    if isinstance(field, models.IntegerField):
        if value in (None, ""):
            return _SKIP
        try:
            return int(value)
        except (TypeError, ValueError):
            raise AdminError(
                400,
                "VALIDATION",
                f"Invalid integer for '{key}'.",
                fields={"fields": [key]},
            )             from None
    if isinstance(field, models.ForeignKey):
        if value in (None, ""):
            return None
        try:
            pk = int(value)
        except (TypeError, ValueError):
            raise AdminError(
                400,
                "VALIDATION",
                f"Invalid integer for '{key}'.",
                fields={"fields": [key]},
            ) from None
        related = field.related_model.objects.filter(pk=pk).first()
        if related is None:
            raise AdminError(
                400,
                "VALIDATION",
                f"Invalid reference for '{key}'.",
                fields={"fields": [key]},
            )
        if attr == "story":
            from apps.composition.blocks import KIND_STORY

            if getattr(related, "kind", None) != KIND_STORY:
                raise AdminError(
                    400,
                    "VALIDATION",
                    "storyId must reference a story composition.",
                    fields={"fields": [key]},
                )
        if attr in {"featured_image", "diagram_image", "screenshot_image", "cover_media"}:
            mime = getattr(related, "mime", "") or ""
            if mime and not str(mime).startswith("image/"):
                raise AdminError(
                    400,
                    "VALIDATION",
                    f"{key} must reference an image Media row.",
                    fields={"fields": [key]},
                )
        if attr == "statement_pdf":
            mime = getattr(related, "mime", "") or ""
            if mime and mime != "application/pdf":
                raise AdminError(
                    400,
                    "VALIDATION",
                    f"{key} must reference a PDF Media row.",
                    fields={"fields": [key]},
                )
        return related
    if isinstance(field, models.DateField):
        if value in (None, ""):
            return None
        try:
            return datetime.strptime(str(value), "%Y-%m-%d").date()
        except ValueError:
            raise AdminError(
                400,
                "VALIDATION",
                f"Invalid date for '{key}'. Expected YYYY-MM-DD.",
                fields={"fields": [key]},
            ) from None
    if isinstance(field, (models.CharField, models.TextField)):
        return "" if value is None else value
    return value


def _coerce_fields(entity: str, model, fields: dict[str, object]) -> dict[str, object]:
    """Validate + coerce a ``fields`` payload against the entity's writable map."""
    attr_map = _FIELD_ATTRS[entity]
    unknown = sorted(key for key in fields if key not in attr_map)
    if unknown:
        raise AdminError(
            400,
            "VALIDATION",
            f"Unknown field(s): {', '.join(unknown)}.",
            fields={"fields": unknown},
        )
    coerced: dict[str, object] = {}
    for key, value in fields.items():
        attr = attr_map[key]
        coerced_value = _coerce_field_value(model._meta.get_field(attr), attr, key, value)
        if coerced_value is not _SKIP:
            coerced[attr] = coerced_value
    return coerced


def _parse_if_match(header: str | None) -> datetime | None:
    """Normalize the If-Match header into an aware datetime, or None."""
    raw = (header or "").strip().strip('"')
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _serialize_updated_at(dt: datetime) -> str:
    """Format like DjangoJSONEncoder (ECMA-262, millisecond precision, Z for UTC)."""
    r = dt.isoformat()
    if dt.microsecond:
        r = r[:23] + r[26:]
    if r.endswith("+00:00"):
        r = r.removesuffix("+00:00") + "Z"
    return r


def _if_match_matches(header: str | None, item) -> bool:
    """True when the If-Match timestamp equals the row's current updated_at.

    The admin API serializes ``updatedAt`` at millisecond precision (Django's
    ECMA-262 encoder), so a round-tripped If-Match carries at most millisecond
    precision; compare both sides at that precision to tolerate the truncation.
    """
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


class AdminConflictError(AdminError):
    """409 optimistic-lock conflict carrying the server's current updated_at."""

    def __init__(self, current_updated_at: str):
        super().__init__(
            409,
            "CONFLICT",
            "The record was modified by someone else.",
            fields=None,
        )
        self.current_updated_at = current_updated_at


def _admin_conflict_handler(request, exc: AdminConflictError):
    return JsonResponse(
        {
            "code": exc.code,
            "message": exc.message,
            "currentUpdatedAt": exc.current_updated_at,
        },
        status=exc.status,
    )


class ContentListItemOut(Schema):
    """Compact admin row for a content entity (list view)."""

    id: int
    locale: str
    slug: str
    title: str
    status: str
    publishedAt: datetime | None
    scheduledFor: datetime | None = None
    updatedAt: datetime


class ContentListOut(Schema):
    """Paginated content list envelope."""

    items: list[ContentListItemOut]
    page: int
    pageSize: int
    total: int


class ContentDetailOut(Schema):
    """Content detail with entity-specific fields (camelCase keys)."""

    id: int
    locale: str
    slug: str
    title: str
    status: str
    publishedAt: datetime | None
    scheduledFor: datetime | None = None
    createdAt: datetime
    updatedAt: datetime
    fields: dict[str, object]


class PreviewLinkOut(Schema):
    """Short-lived public preview share URL."""

    url: str
    path: str
    expiresAt: str
    ttlSeconds: int


class ContentFieldSpecOut(Schema):
    """Writable-field metadata for generic SPA form rendering."""

    key: str
    label: str
    type: str


class ContentEntitySchemaOut(Schema):
    """Writable-field schema for one content entity."""

    entity: str
    fields: list[ContentFieldSpecOut]


class ContentSchemaOut(Schema):
    """All entity schemas keyed by entity name."""

    entities: dict[str, ContentEntitySchemaOut]


class ContentCreateIn(Schema):
    """Create payload for any content entity."""

    locale: str
    slug: str
    title: str
    status: str = "draft"
    fields: dict[str, object] = Field(default_factory=dict)


class ContentUpdateIn(Schema):
    """Optimistically-locked partial update payload."""

    title: str | None = None
    slug: str | None = None
    status: str | None = None
    fields: dict[str, object] | None = None


class ContentTransitionIn(Schema):
    """Lifecycle transition request (ADM-4 / DEBT-0005)."""

    to: str
    reason: str | None = None
    scheduledFor: datetime | None = None


class ContentBulkArchiveIn(Schema):
    """Bulk archive request (feature-flagged; Wave 5 / DEFER-0032)."""

    ids: list[int]
    reason: str | None = None


class ContentBulkArchiveOut(Schema):
    """Bulk archive result with archived count for confirm UX."""

    archived: int
    skipped: int
    ids: list[int]


class ContentRevisionCreateIn(Schema):
    """Optional note when creating an immutable snapshot."""

    note: str | None = None


class ContentRevisionOut(Schema):
    """Immutable content revision metadata (snapshot omitted from list)."""

    id: int
    entityKey: str
    objectId: int
    note: str
    createdAt: datetime
    createdById: int | None = None
    snapshot: dict[str, object] | None = None


class ContentRevisionListOut(Schema):
    """Revision history for one content row."""

    items: list[ContentRevisionOut]


def _detail_response(item, model, entity: str) -> ContentDetailOut:
    """Serialize an entity row into the shared detail envelope."""
    fields: dict[str, object] = {}
    for attr, key in DETAIL_FIELD_MAPS[entity].items():
        field = model._meta.get_field(attr)
        if isinstance(field, models.ForeignKey):
            fields[key] = getattr(item, field.attname)
        else:
            fields[key] = getattr(item, attr)
    return ContentDetailOut(
        id=item.pk,
        locale=item.locale,
        slug=item.slug,
        title=item.title,
        status=item.status,
        publishedAt=item.published_at,
        scheduledFor=getattr(item, "scheduled_for", None),
        createdAt=item.created_at,
        updatedAt=item.updated_at,
        fields=fields,
    )


def _revision_out(rev: ContentRevision, *, include_snapshot: bool = False) -> ContentRevisionOut:
    return ContentRevisionOut(
        id=rev.pk,
        entityKey=rev.entity_key,
        objectId=rev.object_id,
        note=rev.note,
        createdAt=rev.created_at,
        createdById=rev.created_by_id,
        snapshot=rev.snapshot if include_snapshot else None,
    )


@content_router.get("/schema", response=ContentSchemaOut, summary="Writable-field metadata.")
def content_schema(request):
    _require_admin_otp(request)
    entities: dict[str, ContentEntitySchemaOut] = {}
    for entity, field_map in WRITABLE_FIELD_MAPS.items():
        model = ENTITY_MODELS[entity]
        entities[entity] = ContentEntitySchemaOut(
            entity=entity,
            fields=[
                ContentFieldSpecOut(
                    key=key,
                    label=_label_for_key(key),
                    type=_field_type(model._meta.get_field(attr)),
                )
                for attr, key in field_map.items()
            ],
        )
    return ContentSchemaOut(entities=entities)


@content_router.get("/{entity}", response=ContentListOut, summary="List content.")
def content_list(
    request,
    entity: str,
    locale: str | None = None,
    status: str | None = None,
    q: str | None = None,
    page: str = "1",
    pageSize: str = "20",
):
    _require_admin_otp(request)
    model = ENTITY_MODELS.get(entity)
    if model is None:
        raise AdminError(404, "NOT_FOUND", "Unknown content entity.")
    if locale is not None and locale not in VALID_LOCALES:
        raise AdminError(
            400, "VALIDATION", f"Invalid locale. Expected one of: {', '.join(VALID_LOCALES)}."
        )
    if status is not None and status not in VALID_STATUSES:
        raise AdminError(
            400, "VALIDATION", f"Invalid status. Expected one of: {', '.join(VALID_STATUSES)}."
        )
    page_num = _parse_positive_int(request, "page", page, default=1, max_value=1_000_000)
    page_size = _parse_positive_int(request, "pageSize", pageSize, default=20, max_value=100)

    qs = model.objects.all().order_by("-updated_at", "-id")
    if locale is not None:
        qs = qs.filter(locale=locale)
    if status is not None:
        qs = qs.filter(status=status)
    if q is not None:
        qs = qs.filter(Q(title__icontains=q) | Q(slug__icontains=q))

    total = qs.count()
    items = qs[(page_num - 1) * page_size : page_num * page_size]
    return ContentListOut(
        items=[
            ContentListItemOut(
                id=item.pk,
                locale=item.locale,
                slug=item.slug,
                title=item.title,
                status=item.status,
                publishedAt=item.published_at,
                scheduledFor=getattr(item, "scheduled_for", None),
                updatedAt=item.updated_at,
            )
            for item in items
        ],
        page=page_num,
        pageSize=page_size,
        total=total,
    )


@content_router.post(
    "/{entity}/bulk-archive",
    response=ContentBulkArchiveOut,
    summary="Bulk-archive content rows (feature-flagged).",
)
def content_bulk_archive(request, entity: str, payload: ContentBulkArchiveIn):
    """Archive many rows when ``FEATURE_ADMIN_BULK_ARCHIVE`` is on (default off)."""
    _require_admin_otp(request)
    _check_csrf(request)
    if not is_feature_enabled("admin_bulk_archive"):
        raise AdminError(
            404,
            "FEATURE_DISABLED",
            "Bulk archive is disabled (FEATURE_ADMIN_BULK_ARCHIVE).",
        )
    model = ENTITY_MODELS.get(entity)
    if model is None:
        raise AdminError(404, "NOT_FOUND", "Unknown content entity.")
    reason = (payload.reason or "").strip()[:TRANSITION_REASON_MAX]
    try:
        with transaction.atomic():
            result = bulk_archive_items(
                model,
                entity=entity,
                ids=list(payload.ids or []),
                reason=reason,
                user=request.user,
                ip=_client_ip(request),
            )
    except LifecycleError as exc:
        raise _lifecycle_admin_error(exc) from None
    return ContentBulkArchiveOut(**result)


@content_router.get(
    "/{entity}/{id}",
    response=ContentDetailOut,
    summary="Content detail with entity-specific fields.",
)
def content_detail(request, entity: str, id: int):
    _require_admin_otp(request)
    model = ENTITY_MODELS.get(entity)
    if model is None:
        raise AdminError(404, "NOT_FOUND", "Unknown content entity.")
    item = model.objects.filter(pk=id).first()
    if item is None:
        raise AdminError(404, "NOT_FOUND", "Content not found.")
    return _detail_response(item, model, entity)


@content_router.post(
    "/{entity}",
    response={201: ContentDetailOut},
    summary="Create content.",
)
def content_create(request, entity: str, payload: ContentCreateIn):
    _require_admin_otp(request)
    _check_csrf(request)
    model = ENTITY_MODELS.get(entity)
    if model is None:
        raise AdminError(404, "NOT_FOUND", "Unknown content entity.")
    if payload.locale not in VALID_LOCALES:
        raise AdminError(
            400, "VALIDATION", f"Invalid locale. Expected one of: {', '.join(VALID_LOCALES)}."
        )
    if payload.status not in VALID_STATUSES:
        raise AdminError(
            400, "VALIDATION", f"Invalid status. Expected one of: {', '.join(VALID_STATUSES)}."
        )
    if payload.status == "scheduled":
        raise AdminError(
            400,
            "VALIDATION",
            "Use POST .../transition with scheduledFor to schedule publishing.",
        )
    slug = payload.slug.strip()
    title = payload.title.strip()
    if not slug:
        raise AdminError(400, "VALIDATION", "slug must not be empty.")
    if not title:
        raise AdminError(400, "VALIDATION", "title must not be empty.")
    set_fields = _coerce_fields(entity, model, payload.fields)
    if model.objects.filter(locale=payload.locale, slug=slug).exists():
        raise AdminError(409, "DUPLICATE", "A record with this locale and slug already exists.")
    if payload.status == "published" and hasattr(model, "published_at"):
        set_fields["published_at"] = timezone.now()
    try:
        with transaction.atomic():
            item = model.objects.create(
                locale=payload.locale,
                slug=slug,
                title=title,
                status=payload.status,
                **set_fields,
            )
    except IntegrityError:
        raise AdminError(
            409, "DUPLICATE", "A record with this locale and slug already exists."
        ) from None
    return _detail_response(item, model, entity)


@content_router.put(
    "/{entity}/{id}",
    response=ContentDetailOut,
    summary="Update content (optimistic locking).",
)
def content_update(request, entity: str, id: int, payload: ContentUpdateIn):
    _require_admin_otp(request)
    _check_csrf(request)
    model = ENTITY_MODELS.get(entity)
    if model is None:
        raise AdminError(404, "NOT_FOUND", "Unknown content entity.")
    try:
        with transaction.atomic():
            # Row lock (Postgres) so two concurrent PUTs cannot both pass
            # If-Match; the compare happens under the lock.
            item = model.objects.select_for_update().get(pk=id)
            if not _if_match_matches(request.headers.get("If-Match"), item):
                raise AdminConflictError(_serialize_updated_at(item.updated_at))
            if payload.title is not None:
                title = payload.title.strip()
                if not title:
                    raise AdminError(400, "VALIDATION", "title must not be empty.")
                item.title = title
            if payload.slug is not None:
                slug = payload.slug.strip()
                if not slug:
                    raise AdminError(400, "VALIDATION", "slug must not be empty.")
                if model.objects.filter(locale=item.locale, slug=slug).exclude(pk=item.pk).exists():
                    raise AdminError(
                        409, "DUPLICATE", "A record with this locale and slug already exists."
                    )
                item.slug = slug
            if payload.status is not None:
                if payload.status not in VALID_STATUSES:
                    raise AdminError(
                        400,
                        "VALIDATION",
                        f"Invalid status. Expected one of: {', '.join(VALID_STATUSES)}.",
                    )
                if payload.status == "scheduled":
                    raise AdminError(
                        400,
                        "VALIDATION",
                        "Use POST .../transition with scheduledFor to schedule publishing.",
                    )
                item.status = payload.status
                if payload.status != "scheduled":
                    item.scheduled_for = None
            if payload.fields is not None:
                for attr, value in _coerce_fields(entity, model, payload.fields).items():
                    setattr(item, attr, value)
            if hasattr(item, "story"):
                story = getattr(item, "story", None)
                if story is not None and story.locale != item.locale:
                    raise AdminError(
                        400,
                        "VALIDATION",
                        "storyId locale must match the content locale.",
                        fields={"fields": ["storyId"]},
                    )
            if (
                payload.status == "published"
                and hasattr(model, "published_at")
                and item.published_at is None
            ):
                item.published_at = timezone.now()
            try:
                item.save()
            except IntegrityError:
                raise AdminError(
                    409, "DUPLICATE", "A record with this locale and slug already exists."
                ) from None
    except model.DoesNotExist:
        raise AdminError(404, "NOT_FOUND", "Content not found.") from None
    return _detail_response(item, model, entity)


@content_router.post(
    "/{entity}/{id}/transition",
    response=ContentDetailOut,
    summary="Transition content lifecycle state.",
)
def content_transition(request, entity: str, id: int, payload: ContentTransitionIn):
    _require_admin_otp(request)
    _check_csrf(request)
    model = ENTITY_MODELS.get(entity)
    if model is None:
        raise AdminError(404, "NOT_FOUND", "Unknown content entity.")
    if payload.to not in VALID_STATUSES:
        raise AdminError(
            400,
            "VALIDATION",
            f"Invalid status. Expected one of: {', '.join(VALID_STATUSES)}.",
        )
    reason = (payload.reason or "").strip()[:TRANSITION_REASON_MAX]
    try:
        with transaction.atomic():
            # Row lock (Postgres) so concurrent transitions cannot both
            # validate against the same stale status; audit is written in the
            # same transaction as the status change.
            item = model.objects.select_for_update().get(pk=id)
            try:
                transition_item(
                    item,
                    entity=entity,
                    to_status=payload.to,
                    reason=reason,
                    scheduled_for=payload.scheduledFor,
                    user=request.user,
                    ip=_client_ip(request),
                )
            except LifecycleError as exc:
                raise _lifecycle_admin_error(exc) from None
    except model.DoesNotExist:
        raise AdminError(404, "NOT_FOUND", "Content not found.") from None
    if payload.to == "published":
        invoke_static_rebuild()
    return _detail_response(item, model, entity)


@content_router.get(
    "/{entity}/{id}/revisions",
    response=ContentRevisionListOut,
    summary="List immutable content revisions.",
)
def content_revisions_list(request, entity: str, id: int):
    _require_admin_otp(request)
    model = ENTITY_MODELS.get(entity)
    if model is None:
        raise AdminError(404, "NOT_FOUND", "Unknown content entity.")
    if not model.objects.filter(pk=id).exists():
        raise AdminError(404, "NOT_FOUND", "Content not found.")
    revs = ContentRevision.objects.filter(entity_key=entity, object_id=id)[:100]
    return ContentRevisionListOut(items=[_revision_out(rev) for rev in revs])


@content_router.post(
    "/{entity}/{id}/revisions",
    response={201: ContentRevisionOut},
    summary="Create an immutable content snapshot.",
)
def content_revisions_create(request, entity: str, id: int, payload: ContentRevisionCreateIn):
    _require_admin_otp(request)
    _check_csrf(request)
    model = ENTITY_MODELS.get(entity)
    if model is None:
        raise AdminError(404, "NOT_FOUND", "Unknown content entity.")
    item = model.objects.filter(pk=id).first()
    if item is None:
        raise AdminError(404, "NOT_FOUND", "Content not found.")
    rev = create_revision(
        entity_key=entity,
        item=item,
        field_attrs=DETAIL_FIELD_MAPS[entity],
        user=request.user,
        note=payload.note or "",
    )
    AuditLog.objects.create(
        user=request.user,
        action="revision.create",
        model_name=entity,
        object_id=str(id),
        ip=_client_ip(request),
        detail=f"revision_id={rev.pk}; note={rev.note}",
    )
    return _revision_out(rev, include_snapshot=True)


@content_router.post(
    "/{entity}/{id}/revisions/{revision_id}/restore",
    response=ContentDetailOut,
    summary="Restore a revision as draft (never overwrites live published).",
)
def content_revisions_restore(request, entity: str, id: int, revision_id: int):
    _require_admin_otp(request)
    _check_csrf(request)
    model = ENTITY_MODELS.get(entity)
    if model is None:
        raise AdminError(404, "NOT_FOUND", "Unknown content entity.")
    try:
        with transaction.atomic():
            item = model.objects.select_for_update().get(pk=id)
            revision = ContentRevision.objects.filter(
                pk=revision_id, entity_key=entity, object_id=id
            ).first()
            if revision is None:
                raise AdminError(404, "NOT_FOUND", "Revision not found.")
            pre = restore_revision_as_draft(
                entity_key=entity,
                item=item,
                revision=revision,
                field_attrs=DETAIL_FIELD_MAPS[entity],
                user=request.user,
            )
            AuditLog.objects.create(
                user=request.user,
                action="revision.restore_as_draft",
                model_name=entity,
                object_id=str(id),
                ip=_client_ip(request),
                detail=f"restored_revision_id={revision_id}; pre_restore_revision_id={pre.pk}",
            )
    except model.DoesNotExist:
        raise AdminError(404, "NOT_FOUND", "Content not found.") from None
    except ValueError as exc:
        raise AdminError(400, "VALIDATION", str(exc)) from None
    return _detail_response(item, model, entity)


class ProjectDiagramOut(Schema):
    """Admin projection of a project diagram row."""

    id: int
    title: str
    version: str
    diagramDate: date
    altText: str
    longDescription: str
    visibility: str
    diagramImageId: int | None


class ProjectScreenshotOut(Schema):
    """Admin projection of a project screenshot row."""

    id: int
    caption: str
    altText: str
    externalUrl: str
    visibility: str
    screenshotImageId: int | None


class ProjectCaseMediaOut(Schema):
    """Nested diagram + screenshot rows for a project (Media library FKs)."""

    projectId: int
    diagrams: list[ProjectDiagramOut]
    screenshots: list[ProjectScreenshotOut]


class ProjectDiagramImageIn(Schema):
    """Assign or clear a diagram Media FK."""

    diagramImageId: int | None = None


class ProjectScreenshotImageIn(Schema):
    """Assign or clear a screenshot Media FK."""

    screenshotImageId: int | None = None


def _require_project(project_id: int) -> Project:
    project = Project.objects.filter(pk=project_id).first()
    if project is None:
        raise AdminError(404, "NOT_FOUND", "Project not found.")
    return project


def _serialize_diagram(row) -> ProjectDiagramOut:
    return ProjectDiagramOut(
        id=row.pk,
        title=row.title,
        version=row.version,
        diagramDate=row.diagram_date,
        altText=row.alt_text,
        longDescription=row.long_description,
        visibility=row.visibility,
        diagramImageId=row.diagram_image_id,
    )


def _serialize_screenshot(row) -> ProjectScreenshotOut:
    return ProjectScreenshotOut(
        id=row.pk,
        caption=row.caption,
        altText=row.alt_text,
        externalUrl=row.external_url,
        visibility=row.visibility,
        screenshotImageId=row.screenshot_image_id,
    )


def _resolve_image_media(media_id: int | None, key: str):
    """Resolve a Media pk for diagram/screenshot assignment (image MIME only)."""
    if media_id is None:
        return None
    from apps.media.models import Media

    media = Media.objects.filter(pk=media_id).first()
    if media is None:
        raise AdminError(
            400,
            "VALIDATION",
            f"Invalid reference for '{key}'.",
            fields={key: ["not found"]},
        )
    mime = media.mime or ""
    if mime and not mime.startswith("image/"):
        raise AdminError(
            400,
            "VALIDATION",
            f"{key} must reference an image Media row.",
            fields={key: ["not an image"]},
        )
    return media


@content_router.get(
    "/project/{id}/case-media",
    response=ProjectCaseMediaOut,
    summary="List project diagrams and screenshots (Media FKs).",
)
def project_case_media_list(request, id: int):
    _require_admin_otp(request)
    project = _require_project(id)
    diagrams = [
        _serialize_diagram(row)
        for row in project.diagrams.select_related("diagram_image").order_by("id")
    ]
    screenshots = [
        _serialize_screenshot(row)
        for row in project.screenshots.select_related("screenshot_image").order_by("id")
    ]
    return ProjectCaseMediaOut(
        projectId=project.pk,
        diagrams=diagrams,
        screenshots=screenshots,
    )


@content_router.put(
    "/project/{id}/diagrams/{diagram_id}",
    response=ProjectDiagramOut,
    summary="Set diagram Media FK.",
)
def project_diagram_set_image(
    request, id: int, diagram_id: int, payload: ProjectDiagramImageIn
):
    _require_admin_otp(request)
    _check_csrf(request)
    project = _require_project(id)
    from apps.content.models import ProjectDiagram

    row = ProjectDiagram.objects.filter(pk=diagram_id, project=project).first()
    if row is None:
        raise AdminError(404, "NOT_FOUND", "Diagram not found.")
    row.diagram_image = _resolve_image_media(payload.diagramImageId, "diagramImageId")
    row.save(update_fields=["diagram_image"])
    return _serialize_diagram(row)


@content_router.put(
    "/project/{id}/screenshots/{screenshot_id}",
    response=ProjectScreenshotOut,
    summary="Set screenshot Media FK.",
)
def project_screenshot_set_image(
    request, id: int, screenshot_id: int, payload: ProjectScreenshotImageIn
):
    _require_admin_otp(request)
    _check_csrf(request)
    project = _require_project(id)
    from apps.content.models import ProjectScreenshot

    row = ProjectScreenshot.objects.filter(pk=screenshot_id, project=project).first()
    if row is None:
        raise AdminError(404, "NOT_FOUND", "Screenshot not found.")
    row.screenshot_image = _resolve_image_media(
        payload.screenshotImageId, "screenshotImageId"
    )
    row.save(update_fields=["screenshot_image"])
    return _serialize_screenshot(row)


@content_router.post(
    "/{entity}/{id}/preview-link",
    response=PreviewLinkOut,
    summary="Generate a short-lived public preview share link.",
)
def content_preview_link(request, entity: str, id: int):
    _require_admin_otp(request)
    _check_csrf(request)
    kind = PREVIEW_SHARE_ENTITIES.get(entity)
    if kind is None:
        raise AdminError(
            404,
            "NOT_FOUND",
            "Preview links are not supported for this entity.",
        )
    model = ENTITY_MODELS.get(entity)
    if model is None:
        raise AdminError(404, "NOT_FOUND", "Unknown content entity.")
    item = model.objects.filter(pk=id).first()
    if item is None:
        raise AdminError(404, "NOT_FOUND", "Content not found.")
    path = build_preview_share_path(kind, item.pk)
    ttl = preview_ttl_seconds()
    expires_at = datetime.fromtimestamp(
        int(timezone.now().timestamp()) + ttl, tz=UTC
    ).isoformat()
    AuditLog.objects.create(
        user=request.user,
        action="preview.share_link",
        model_name=entity,
        object_id=str(item.pk),
        ip=_client_ip(request),
        detail=f"preview link created ttl={ttl}s",
    )
    return PreviewLinkOut(
        url=request.build_absolute_uri(path),
        path=path,
        expiresAt=expires_at,
        ttlSeconds=ttl,
    )


from apps.api.admin_api import admin_api  # noqa: E402

admin_api.exception_handler(AdminConflictError)(_admin_conflict_handler)
