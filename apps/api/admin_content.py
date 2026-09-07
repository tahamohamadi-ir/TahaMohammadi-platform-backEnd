"""Custom admin content API (ADR-0026, ADM-1).

Staff + OTP protected endpoints over the canonical content entities: read
(``GET /{entity}`` list, ``GET /{entity}/{id}`` detail) and the write path
(``GET /schema`` writable-field metadata, ``POST /{entity}`` create,
``PUT /{entity}/{id}`` update with optimistic locking via ``If-Match``).
Unsafe methods additionally enforce the same-origin CSRF baseline.
"""

from __future__ import annotations

import copy
import re
import uuid
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
from apps.composition.models import (
    CompositionBlock,
    CompositionPage,
    CompositionSection,
)
from apps.content.feature_flags import is_feature_enabled
from apps.content.models import (
    Article,
    Book,
    Collection,
    ContentRevision,
    ContentSeedRecord,
    Course,
    CreativeWork,
    Download,
    Landing,
    Lesson,
    LifecycleStatus,
    Profile,
    Project,
    Publication,
    PublicationSnapshot,
    ResearchStatement,
    ResearchTopic,
    Series,
    Talk,
)
from apps.content.preview_token import build_preview_share_path, preview_ttl_seconds
from apps.content.profile_api import (
    resolve_translation_status,
    serialize_profile_detail,
)
from apps.content.published import invalidate_published_snapshots
from apps.content.revisions import (
    apply_snapshot_as_draft,
    build_snapshot,
)
from apps.content.services.lifecycle import (
    LifecycleError,
    bulk_archive_items,
    transition_item,
)
from apps.rebuild.services import enqueue_content_invalidation
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
    "lesson": Lesson,
    "collection": Collection,
}

PREVIEW_SHARE_ENTITIES = {k: k for k in ENTITY_MODELS}

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
        "social_image": "socialImageId",
        "translation_key": "translationKey",
        "related_records": "relatedRecords",
    },
    "profile": {
        "short_bio": "shortBio",
        "long_bio": "longBio",
        "availability": "availability",
        "body": "body",
        "seo_title": "seoTitle",
        "seo_description": "seoDescription",
        "revision": "revision",
        "social_image": "socialImageId",
        "translation_key": "translationKey",
        "related_records": "relatedRecords",
    },
    "article": {
        "excerpt": "excerpt",
        "body": "body",
        "license": "license",
        "reading_time_minutes": "readingTimeMinutes",
        "accessibility_notes": "accessibilityNotes",
        "featured_image": "featuredImageId",
        "story": "storyId",
        "seo_title": "seoTitle",
        "seo_description": "seoDescription",
        "social_image": "socialImageId",
        "translation_key": "translationKey",
        "related_records": "relatedRecords",
    },
    "series": {
        "description": "description",
        "ordering": "ordering",
        "story": "storyId",
        "members": "members",
        "seo_title": "seoTitle",
        "seo_description": "seoDescription",
        "social_image": "socialImageId",
        "translation_key": "translationKey",
        "related_records": "relatedRecords",
    },
    "research-topic": {
        "summary": "summary",
        "motivation": "motivation",
        "problems": "problems",
        "research_questions": "researchQuestions",
        "methods": "methods",
        "future_directions": "futureDirections",
        "story": "storyId",
        "seo_title": "seoTitle",
        "seo_description": "seoDescription",
        "social_image": "socialImageId",
        "translation_key": "translationKey",
        "related_records": "relatedRecords",
    },
    "research-statement": {
        "body": "body",
        "statement_pdf": "statementPdfId",
        "story": "storyId",
        "seo_title": "seoTitle",
        "seo_description": "seoDescription",
        "social_image": "socialImageId",
        "translation_key": "translationKey",
        "related_records": "relatedRecords",
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
        "seo_title": "seoTitle",
        "seo_description": "seoDescription",
        "social_image": "socialImageId",
        "translation_key": "translationKey",
        "related_records": "relatedRecords",
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
        "story": "storyId",
        "seo_title": "seoTitle",
        "seo_description": "seoDescription",
        "social_image": "socialImageId",
        "translation_key": "translationKey",
        "related_records": "relatedRecords",
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
        "story": "storyId",
        "seo_title": "seoTitle",
        "seo_description": "seoDescription",
        "social_image": "socialImageId",
        "translation_key": "translationKey",
        "related_records": "relatedRecords",
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
        "story": "storyId",
        "seo_title": "seoTitle",
        "seo_description": "seoDescription",
        "social_image": "socialImageId",
        "translation_key": "translationKey",
        "related_records": "relatedRecords",
    },
    "download": {
        "description": "description",
        "media": "mediaId",
        "download_type": "downloadType",
        "language": "language",
        "access_state": "accessState",
        "accessibility_notes": "accessibilityNotes",
        "license": "license",
        "story": "storyId",
        "seo_title": "seoTitle",
        "seo_description": "seoDescription",
        "social_image": "socialImageId",
        "translation_key": "translationKey",
        "related_records": "relatedRecords",
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
        "story": "storyId",
        "seo_title": "seoTitle",
        "seo_description": "seoDescription",
        "social_image": "socialImageId",
        "translation_key": "translationKey",
        "related_records": "relatedRecords",
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
        "story": "storyId",
        "seo_title": "seoTitle",
        "seo_description": "seoDescription",
        "social_image": "socialImageId",
        "translation_key": "translationKey",
        "related_records": "relatedRecords",
    },
    "lesson": {
        "course": "courseId",
        "position": "position",
        "summary": "summary",
        "story": "storyId",
        "seo_title": "seoTitle",
        "seo_description": "seoDescription",
        "social_image": "socialImageId",
        "translation_key": "translationKey",
        "related_records": "relatedRecords",
    },
    "collection": {
        "description": "description",
        "curator_name": "curatorName",
        "curator_title": "curatorTitle",
        "criteria": "criteria",
        "curated_date": "curatedDate",
        "cover_media": "coverMediaId",
        "story": "storyId",
        "members": "members",
        "seo_title": "seoTitle",
        "seo_description": "seoDescription",
        "social_image": "socialImageId",
        "translation_key": "translationKey",
        "related_records": "relatedRecords",
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
    if isinstance(field, models.JSONField):
        return "json"
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
        if attr in {
            "featured_image",
            "diagram_image",
            "screenshot_image",
            "cover_media",
            "social_image",
        }:
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
    if isinstance(field, models.UUIDField):
        if value in (None, ""):
            return None
        try:
            return uuid.UUID(str(value))
        except (ValueError, AttributeError, TypeError):
            raise AdminError(
                400,
                "VALIDATION",
                f"Invalid UUID for '{key}'.",
                fields={"fields": [key]},
            ) from None
    if isinstance(field, models.JSONField):
        if value in (None, ""):
            return []
        if attr == "related_records":
            if not isinstance(value, list):
                raise AdminError(
                    400,
                    "VALIDATION",
                    f"Invalid list for '{key}'. Expected array of references.",
                    fields={"fields": [key]},
                )
            from apps.api.record_resolver import _ID_RE, MAX_ID, RESOLVER_FAMILIES

            coerced_list = []
            for item in value:
                if not isinstance(item, dict):
                    raise AdminError(
                        400,
                        "VALIDATION",
                        f"Invalid reference object in '{key}'.",
                        fields={"fields": [key]},
                    )
                family = item.get("family")
                if not isinstance(family, str) or family not in RESOLVER_FAMILIES:
                    raise AdminError(
                        400,
                        "VALIDATION",
                        f"Invalid or unknown family '{family}' in '{key}'.",
                        fields={"fields": [key]},
                    )
                raw_id = item.get("id")
                if not isinstance(raw_id, str) or not _ID_RE.match(raw_id):
                    raise AdminError(
                        400,
                        "VALIDATION",
                        (
                            f"Invalid canonical ID for '{key}'. "
                            "Expected non-zero decimal string without leading zeros."
                        ),
                        fields={"fields": [key]},
                    )
                try:
                    num_id = int(raw_id)
                    if num_id > MAX_ID:
                        raise ValueError("ID out of range")
                except ValueError:
                    raise AdminError(
                        400,
                        "VALIDATION",
                        f"Invalid canonical ID for '{key}'. Out of range.",
                        fields={"fields": [key]},
                    ) from None
                coerced_list.append({"family": family, "id": raw_id})
            return coerced_list
        if attr == "members":
            if not isinstance(value, list):
                raise AdminError(
                    400,
                    "VALIDATION",
                    f"Invalid list for '{key}'. Expected array of member objects.",
                    fields={"fields": [key]},
                )
            from apps.api.admin_common import CONTENT_RELATED_FAMILIES
            from apps.api.record_resolver import _ID_RE, MAX_ID

            coerced_members = []
            seen_pairs = set()
            for item in value:
                if not isinstance(item, dict):
                    raise AdminError(
                        400,
                        "VALIDATION",
                        f"Invalid member object in '{key}'.",
                        fields={"fields": [key]},
                    )
                family = item.get("family")
                if not isinstance(family, str) or family not in CONTENT_RELATED_FAMILIES:
                    raise AdminError(
                        400,
                        "VALIDATION",
                        f"Invalid or unknown family '{family}' in '{key}'.",
                        fields={"fields": [key]},
                    )
                if (
                    getattr(field, "model", None)
                    and field.model.__name__.lower() == "series"
                    and family != "article"
                ):
                    raise AdminError(
                        400,
                        "VALIDATION",
                        f"Series members must be of family 'article', got '{family}'.",
                        fields={"fields": [key]},
                    )
                raw_id = item.get("id")
                if not isinstance(raw_id, str) or not _ID_RE.match(raw_id):
                    raise AdminError(
                        400,
                        "VALIDATION",
                        (
                            f"Invalid canonical ID for '{key}'. "
                            "Expected non-zero decimal string without leading zeros."
                        ),
                        fields={"fields": [key]},
                    )
                try:
                    num_id = int(raw_id)
                    if num_id > MAX_ID:
                        raise ValueError("ID out of range")
                except ValueError:
                    raise AdminError(
                        400,
                        "VALIDATION",
                        f"Invalid canonical ID for '{key}'. Out of range.",
                        fields={"fields": [key]},
                    ) from None

                pair = (family, raw_id)
                if pair in seen_pairs:
                    raise AdminError(
                        400,
                        "VALIDATION",
                        f"Duplicate member {family}:{raw_id} in '{key}'.",
                        fields={"fields": [key]},
                    )
                seen_pairs.add(pair)

                target_model = CONTENT_RELATED_FAMILIES[family]
                target_record = target_model.objects.filter(pk=num_id).first()
                if target_record is None:
                    raise AdminError(
                        400,
                        "VALIDATION",
                        f"Referenced member {family}:{raw_id} does not exist.",
                        fields={"fields": [key]},
                    )

                pos = item.get("position")
                if pos is None:
                    pos = len(coerced_members)
                try:
                    pos_int = int(pos)
                    if pos_int < 0:
                        raise ValueError
                except (TypeError, ValueError):
                    raise AdminError(
                        400,
                        "VALIDATION",
                        f"Invalid position for member in '{key}'. Must be non-negative integer.",
                        fields={"fields": [key]},
                    ) from None

                coerced_members.append(
                    {
                        "family": family,
                        "id": raw_id,
                        "position": pos_int,
                        "_target_locale": getattr(target_record, "locale", None),
                    }
                )
            coerced_members.sort(key=lambda m: (m["position"], m["family"], m["id"]))
            return coerced_members
        return value
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
            "field_errors": {},
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
    approvalState: str | None = None


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
    approvalState: str | None = None
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


def _seed_approval_state(model, object_id: int) -> str | None:
    """Approval state from the linked ContentSeedRecord (BACKEND-210).

    Returns ``None`` for admin-created rows with no seed provenance — those
    are owner-authored through this admin and carry no separate gate.
    """
    record = ContentSeedRecord.objects.filter(
        mapped_model_label=model._meta.label, mapped_object_id=object_id
    ).first()
    if record is None:
        return None
    return record.approval_state or None


def _seed_approval_states(model, object_ids: list[int]) -> dict[int, str]:
    """Bulk variant of :func:`_seed_approval_state` for list views."""
    rows = ContentSeedRecord.objects.filter(
        mapped_model_label=model._meta.label, mapped_object_id__in=object_ids
    ).values_list("mapped_object_id", "approval_state")
    return {object_id: state for object_id, state in rows if state}


def _enforce_publication_gate(item, model) -> None:
    """Block ``→ published`` while the owner seed record is not approved.

    Admin-created rows without seed provenance are owner-authored through
    this admin and publish without an extra gate.
    """
    record = ContentSeedRecord.objects.filter(
        mapped_model_label=model._meta.label, mapped_object_id=item.pk
    ).first()
    if record is not None and not record.is_publication_allowed:
        raise AdminError(
            409,
            "APPROVAL_REQUIRED",
            "Owner approval is required before this record can be published "
            f"(approval_state={record.approval_state!r}).",
        )


def _detail_response(item, model, entity: str) -> ContentDetailOut:
    """Serialize an entity row into the shared detail envelope."""
    fields: dict[str, object] = {}
    for attr, key in DETAIL_FIELD_MAPS[entity].items():
        field = model._meta.get_field(attr)
        if isinstance(field, models.ForeignKey):
            fields[key] = getattr(item, field.attname)
        elif isinstance(field, models.UUIDField):
            val = getattr(item, attr)
            fields[key] = str(val) if val is not None else None
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
        approvalState=_seed_approval_state(model, item.pk),
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
    items = list(qs[(page_num - 1) * page_size : page_num * page_size])
    approval_states = _seed_approval_states(model, [item.pk for item in items])
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
                approvalState=approval_states.get(item.pk),
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


def _check_collection_cycles(collection_id: int | None, members: list[dict]) -> None:
    if not collection_id:
        return
    visited = {collection_id}
    queue = [int(m["id"]) for m in members if m.get("family") == "collection"]
    while queue:
        curr_id = queue.pop(0)
        if curr_id in visited:
            raise AdminError(
                400,
                "VALIDATION",
                "Cycle detected in collection membership.",
                fields={"fields": ["members"]},
            )
        visited.add(curr_id)
        curr_coll = Collection.objects.filter(pk=curr_id).first()
        if curr_coll and isinstance(curr_coll.members, list):
            for m in curr_coll.members:
                if isinstance(m, dict) and m.get("family") == "collection":
                    try:
                        queue.append(int(m["id"]))
                    except (ValueError, TypeError):
                        pass


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
    course = set_fields.get("course")
    if entity == "lesson":
        if course is None:
            raise AdminError(
                400,
                "VALIDATION",
                "courseId is required for lesson.",
                fields={"fields": ["courseId"]},
            )
        if getattr(course, "locale", None) != payload.locale:
            raise AdminError(
                400,
                "VALIDATION",
                "courseId locale must match the content locale.",
                fields={"fields": ["courseId"]},
            )
    story = set_fields.get("story")
    if story is not None and getattr(story, "locale", None) != payload.locale:
        raise AdminError(
            400,
            "VALIDATION",
            "storyId locale must match the content locale.",
            fields={"fields": ["storyId"]},
        )
    members = set_fields.get("members")
    if members and isinstance(members, list):
        for m in members:
            loc = m.pop("_target_locale", None)
            if loc and loc != payload.locale:
                raise AdminError(
                    400,
                    "VALIDATION",
                    "Member locale must match the content locale.",
                    fields={"fields": ["members"]},
                )
    if entity == "lesson":
        if model.objects.filter(course=course, locale=payload.locale, slug=slug).exists():
            raise AdminError(
                409, "DUPLICATE", "A lesson with this course, locale, and slug already exists."
            )
    else:
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
            if entity == "series" and hasattr(item, "articles"):
                art_ids = [
                    int(m["id"])
                    for m in (getattr(item, "members", None) or [])
                    if isinstance(m, dict) and m.get("family") == "article"
                ]
                item.articles.set(art_ids)
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
                if entity == "lesson":
                    course = getattr(item, "course", None)
                    lesson_dup = (
                        model.objects.filter(course=course, locale=item.locale, slug=slug)
                        .exclude(pk=item.pk)
                        .exists()
                    )
                    if lesson_dup:
                        raise AdminError(
                            409,
                            "DUPLICATE",
                            "A lesson with this course, locale, and slug already exists.",
                        )
                else:
                    record_dup = (
                        model.objects.filter(locale=item.locale, slug=slug)
                        .exclude(pk=item.pk)
                        .exists()
                    )
                    if record_dup:
                        raise AdminError(
                            409,
                            "DUPLICATE",
                            "A record with this locale and slug already exists.",
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
            if hasattr(item, "course"):
                course = getattr(item, "course", None)
                if course is not None and course.locale != item.locale:
                    raise AdminError(
                        400,
                        "VALIDATION",
                        "courseId locale must match the content locale.",
                        fields={"fields": ["courseId"]},
                    )
            if hasattr(item, "story"):
                story = getattr(item, "story", None)
                if story is not None and story.locale != item.locale:
                    raise AdminError(
                        400,
                        "VALIDATION",
                        "storyId locale must match the content locale.",
                        fields={"fields": ["storyId"]},
                    )
            if hasattr(item, "members"):
                members = getattr(item, "members", None)
                if members and isinstance(members, list):
                    for m in members:
                        loc = m.pop("_target_locale", None)
                        if loc and loc != item.locale:
                            raise AdminError(
                                400,
                                "VALIDATION",
                                "Member locale must match the content locale.",
                                fields={"fields": ["members"]},
                            )
                    if any(
                        m.get("family") == "collection" and str(m.get("id")) == str(item.pk)
                        for m in members
                    ):
                        raise AdminError(
                            400,
                            "VALIDATION",
                            "Collection cannot contain itself.",
                            fields={"fields": ["members"]},
                        )
                    _check_collection_cycles(item.pk, members)
            if (
                payload.status == "published"
                and hasattr(model, "published_at")
                and item.published_at is None
            ):
                item.published_at = timezone.now()
            try:
                item.save()
                if entity == "series" and hasattr(item, "articles"):
                    art_ids = [
                        int(m["id"])
                        for m in (getattr(item, "members", None) or [])
                        if isinstance(m, dict) and m.get("family") == "article"
                    ]
                    item.articles.set(art_ids)
                if item.status == "published":
                    _record_publication_snapshot(item, entity, user=request.user)
                    enqueue_content_invalidation(entity, item, action="publish")
                elif payload.status == "archived":
                    enqueue_content_invalidation(entity, item, action="archive")
                    # A04: explicit archive invalidates the published document.
                    invalidate_published_snapshots(entity, item.pk)
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
            if payload.to == "published":
                # Owner publication gate (BACKEND-210): seed-sourced rows
                # cannot publish without the owner's approval triple.
                _enforce_publication_gate(item, model)
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
            if payload.to == "published":
                _record_publication_snapshot(item, entity, user=request.user)
    except model.DoesNotExist:
        raise AdminError(404, "NOT_FOUND", "Content not found.") from None
    # A03: build dispatch for the enqueued job fires on transaction commit
    # (see enqueue_publication_job); no synchronous trigger here.
    return _detail_response(item, model, entity)


def _serialize_composition_story(story: CompositionPage | None) -> dict | None:
    if story is None:
        return None
    return {
        "id": story.id,
        "key": story.key,
        "title": story.title,
        "locale": story.locale,
        "status": story.status,
        "sections": [
            {
                "layout": s.layout,
                "ratio": s.ratio,
                "enabled": s.enabled,
                "blocks": [
                    {
                        "blockType": b.block_type,
                        "settings": copy.deepcopy(b.settings or {}),
                        "enabled": b.enabled,
                    }
                    for b in s.blocks.all()
                ],
            }
            for s in story.sections.prefetch_related("blocks").all()
        ],
    }


def _serialize_project_case_study(project: Project) -> dict:
    """Snapshot project case-study/evidence/collaborators/funding with real model fields.

    Aligned with PRODUCT-INTERFACES-V2 §I03 and apps/api/admin_project_evidence.py:
    details use depth/problem/constraints/technical_decisions/trade_offs/
    outcomes_summary/lessons_learned/testing_summary; evidence uses
    label/value/source/last_verified/visibility; collaborators use
    name/role/publication_approved; funding uses funder/grant_id/
    publication_approved via funding_items (ordered by id).
    """
    try:
        cs = project.case_study
    except Exception:  # noqa: BLE001 — missing extension means details=None
        cs = None
    if cs is None:
        details = None
    else:
        details = {
            "depth": cs.depth,
            "problem": cs.problem,
            "constraints": cs.constraints,
            "technical_decisions": cs.technical_decisions,
            "technicalDecisions": cs.technical_decisions,
            "trade_offs": cs.trade_offs,
            "tradeOffs": cs.trade_offs,
            "outcomes_summary": cs.outcomes_summary,
            "outcomesSummary": cs.outcomes_summary,
            "lessons_learned": cs.lessons_learned,
            "lessonsLearned": cs.lessons_learned,
            "testing_summary": cs.testing_summary,
            "testingSummary": cs.testing_summary,
        }
    evidence = []
    for ev in project.evidence_items.all().order_by("id"):
        last_verified = getattr(ev, "last_verified", None)
        if hasattr(last_verified, "isoformat"):
            last_verified = last_verified.isoformat()
        evidence.append(
            {
                "id": ev.id,
                "label": ev.label,
                "value": ev.value,
                "source": ev.source,
                "last_verified": last_verified,
                "lastVerified": last_verified,
                "visibility": ev.visibility,
            }
        )
    collaborators = [
        {
            "id": c.id,
            "name": c.name,
            "role": c.role,
            "publication_approved": c.publication_approved,
            "publicationApproved": c.publication_approved,
        }
        for c in project.collaborators.all().order_by("id")
    ]
    funding = [
        {
            "id": f.id,
            "funder": f.funder,
            "grant_id": f.grant_id,
            "grantId": f.grant_id,
            "publication_approved": f.publication_approved,
            "publicationApproved": f.publication_approved,
        }
        for f in project.funding_items.all().order_by("id")
    ]
    return {
        "details": details,
        "evidence": evidence,
        "collaborators": collaborators,
        "funding": funding,
    }


def _build_full_content_snapshot(item, entity: str) -> dict:
    payload = build_snapshot(item, DETAIL_FIELD_MAPS[entity])
    if hasattr(item, "story"):
        payload["story"] = _serialize_composition_story(getattr(item, "story", None))
    if entity == "project":
        payload["project_case_study"] = _serialize_project_case_study(item)
    return payload


def _record_publication_snapshot(item, entity: str, user=None) -> None:
    full_snap = _build_full_content_snapshot(item, entity)
    PublicationSnapshot.objects.create(
        entity_key=entity,
        object_id=item.pk,
        locale=getattr(item, "locale", ""),
        slug=getattr(item, "slug", ""),
        snapshot=full_snap,
        published_at=getattr(item, "published_at", None) or timezone.now(),
        created_by=user if getattr(user, "is_authenticated", False) else None,
    )
    if hasattr(item, "story") and item.story is not None:
        story = item.story
        story_snap = _serialize_composition_story(story)
        if story_snap:
            PublicationSnapshot.objects.create(
                entity_key="composition",
                object_id=story.pk,
                locale=story.locale,
                slug=story.key,
                snapshot=story_snap,
                published_at=story.published_at or timezone.now(),
                created_by=user if getattr(user, "is_authenticated", False) else None,
            )


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


@content_router.get(
    "/{entity}/{id}/revisions/{revision_id}",
    response=ContentRevisionOut,
    summary="Get an immutable content revision with full snapshot.",
)
def content_revisions_detail(request, entity: str, id: int, revision_id: int):
    _require_admin_otp(request)
    model = ENTITY_MODELS.get(entity)
    if model is None:
        raise AdminError(404, "NOT_FOUND", "Unknown content entity.")
    if not model.objects.filter(pk=id).exists():
        raise AdminError(404, "NOT_FOUND", "Content not found.")
    rev = ContentRevision.objects.filter(
        pk=revision_id, entity_key=entity, object_id=id
    ).first()
    if rev is None:
        raise AdminError(404, "NOT_FOUND", "Revision not found.")
    return _revision_out(rev, include_snapshot=True)


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
    snapshot = _build_full_content_snapshot(item, entity)
    rev = ContentRevision.objects.create(
        entity_key=entity,
        object_id=item.pk,
        snapshot=snapshot,
        note=(payload.note or "")[:200],
        created_by=request.user if getattr(request.user, "is_authenticated", False) else None,
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

            # 1. Create pre-restore snapshot of the live state (preserves history)
            pre_snapshot = _build_full_content_snapshot(item, entity)
            pre_created_by = (
                request.user
                if getattr(request.user, "is_authenticated", False)
                else None
            )
            pre = ContentRevision.objects.create(
                entity_key=entity,
                object_id=item.pk,
                snapshot=pre_snapshot,
                note="pre-restore snapshot",
                created_by=pre_created_by,
            )

            # 2. Restore parent fields and force draft
            snapshot_fields = revision.snapshot.get("fields") or {}
            restore_attrs: dict[str, str] = {}
            for k, v in DETAIL_FIELD_MAPS[entity].items():
                f = model._meta.get_field(k)
                if isinstance(f, models.UUIDField) and snapshot_fields.get(k) in (None, ""):
                    setattr(item, k, None)
                    continue
                restore_attrs[k] = v
            apply_snapshot_as_draft(item, revision.snapshot, restore_attrs)

            # 3. Restore attached story (if present in snapshot)
            if "story" in revision.snapshot:
                story_data = revision.snapshot.get("story")
                if story_data is None:
                    item.story = None
                elif isinstance(story_data, dict):
                    story_page = getattr(item, "story", None)
                    if story_page is not None:
                        story_page.title = story_data.get("title", story_page.title)
                        story_page.status = "draft"
                        story_page.published_at = None
                        story_page.sections.all().delete()
                        for pos, sec_data in enumerate(story_data.get("sections", [])):
                            sec = CompositionSection.objects.create(
                                page=story_page,
                                position=pos,
                                layout=sec_data.get("layout", "1col"),
                                ratio=sec_data.get("ratio", ""),
                                enabled=sec_data.get("enabled", True),
                            )
                            CompositionBlock.objects.bulk_create([
                                CompositionBlock(
                                    section=sec,
                                    position=b_pos,
                                    block_type=b_data.get("blockType", "text"),
                                    settings=b_data.get("settings", {}),
                                    enabled=b_data.get("enabled", True),
                                )
                                for b_pos, b_data in enumerate(sec_data.get("blocks", []))
                            ])
                        story_page.save()
                    else:
                        base_key = story_data.get("key", f"{entity}-{item.pk}-story")
                        story_key = base_key
                        if CompositionPage.objects.filter(key=story_key).exists():
                            story_key = f"{base_key}-{uuid.uuid4().hex[:6]}"
                        new_story = CompositionPage.objects.create(
                            key=story_key,
                            kind="story",
                            locale=story_data.get("locale", item.locale),
                            title=story_data.get("title", f"Story for {item.title}"),
                            status="draft",
                            published_at=None,
                        )
                        for pos, sec_data in enumerate(story_data.get("sections", [])):
                            sec = CompositionSection.objects.create(
                                page=new_story,
                                position=pos,
                                layout=sec_data.get("layout", "1col"),
                                ratio=sec_data.get("ratio", ""),
                                enabled=sec_data.get("enabled", True),
                            )
                            CompositionBlock.objects.bulk_create([
                                CompositionBlock(
                                    section=sec,
                                    position=b_pos,
                                    block_type=b_data.get("blockType", "text"),
                                    settings=b_data.get("settings", {}),
                                    enabled=b_data.get("enabled", True),
                                )
                                for b_pos, b_data in enumerate(sec_data.get("blocks", []))
                            ])
                        item.story = new_story

            # 4. Restore series articles M2M from the revision snapshot (not live state).
            if entity == "series" and hasattr(item, "articles"):
                snap_fields = (revision.snapshot.get("fields") or {}) if isinstance(
                    revision.snapshot, dict
                ) else {}
                snap_members = snap_fields.get("members")
                if isinstance(snap_members, list):
                    art_ids = []
                    for m in snap_members:
                        if not isinstance(m, dict):
                            continue
                        if str(m.get("family", "")).lower() != "article":
                            continue
                        try:
                            art_ids.append(int(m.get("id")))
                        except (TypeError, ValueError):
                            continue
                    # Only touch M2M when the snapshot carries an explicit list.
                    item.articles.set(art_ids)

            # 5. Restore project case-study if present (real model fields only).
            if entity == "project" and "project_case_study" in revision.snapshot:
                pcs = revision.snapshot["project_case_study"]
                if isinstance(pcs, dict):
                    cs_details = pcs.get("details")
                    if isinstance(cs_details, dict):
                        from apps.content.models import ProjectCaseStudyDetails

                        def _pick(details: dict, snake: str, camel: str, default: str = "") -> str:
                            value = details.get(snake, None)
                            if value is None:
                                value = details.get(camel, None)
                            return str(value) if value is not None else default

                        depth = _pick(cs_details, "depth", "depth", "standard")
                        if depth not in ("standard", "featured_case_study", "experiment"):
                            depth = "standard"
                        cs_values = {
                            "depth": depth,
                            "problem": _pick(cs_details, "problem", "problem", ""),
                            "constraints": _pick(cs_details, "constraints", "constraints", ""),
                            "technical_decisions": _pick(
                                cs_details, "technical_decisions", "technicalDecisions", ""
                            ),
                            "trade_offs": _pick(cs_details, "trade_offs", "tradeOffs", ""),
                            "outcomes_summary": _pick(
                                cs_details, "outcomes_summary", "outcomesSummary", ""
                            ),
                            "lessons_learned": _pick(
                                cs_details, "lessons_learned", "lessonsLearned", ""
                            ),
                            "testing_summary": _pick(
                                cs_details, "testing_summary", "testingSummary", ""
                            ),
                        }
                        try:
                            cs = item.case_study
                        except Exception:  # noqa: BLE001 — no extension yet
                            cs = None
                        if cs is None:
                            cs = ProjectCaseStudyDetails(project=item, **cs_values)
                            cs.save()
                        else:
                            for attr, value in cs_values.items():
                                setattr(cs, attr, value)
                            cs.save()
                    if "evidence" in pcs and isinstance(pcs["evidence"], list):
                        item.evidence_items.all().delete()
                        for ev in pcs["evidence"]:
                            if not isinstance(ev, dict):
                                continue
                            raw_verified = ev.get("last_verified", ev.get("lastVerified", None))
                            parsed_verified = None
                            if isinstance(raw_verified, str) and raw_verified.strip():
                                try:
                                    parsed_verified = date.fromisoformat(
                                        raw_verified.strip()[:10]
                                    )
                                except ValueError:
                                    parsed_verified = None
                            elif hasattr(raw_verified, "isoformat"):
                                try:
                                    parsed_verified = date.fromisoformat(
                                        raw_verified.isoformat()[:10]
                                    )
                                except ValueError:
                                    parsed_verified = None
                            visibility = str(ev.get("visibility", "internal") or "internal")
                            if visibility not in ("public", "internal", "private"):
                                visibility = "internal"
                            item.evidence_items.create(
                                label=str(ev.get("label", "") or ""),
                                value=str(ev.get("value", "") or ""),
                                source=str(ev.get("source", "") or ""),
                                last_verified=parsed_verified,
                                visibility=visibility,
                            )
                    if "collaborators" in pcs and isinstance(pcs["collaborators"], list):
                        item.collaborators.all().delete()
                        for c in pcs["collaborators"]:
                            if not isinstance(c, dict):
                                continue
                            approved = c.get("publication_approved", None)
                            if approved is None:
                                approved = c.get("publicationApproved", False)
                            item.collaborators.create(
                                name=str(c.get("name", "") or ""),
                                role=str(c.get("role", "") or ""),
                                publication_approved=bool(approved),
                            )
                    if "funding" in pcs and isinstance(pcs["funding"], list):
                        item.funding_items.all().delete()
                        for f in pcs["funding"]:
                            if not isinstance(f, dict):
                                continue
                            approved = f.get("publication_approved", None)
                            if approved is None:
                                approved = f.get("publicationApproved", False)
                            funder = str(f.get("funder", "") or "")
                            grant_id = str(
                                f.get("grant_id", f.get("grantId", "") or "") or ""
                            )
                            if not funder.strip():
                                continue
                            item.funding_items.create(
                                funder=funder,
                                grant_id=grant_id,
                                publication_approved=bool(approved),
                            )

            item.save()
            # A04: restore-as-draft is not an archive. The published document
            # (publication snapshot) keeps serving, so no removal is enqueued.

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


from apps.api.admin_project_evidence import register_project_case_study_endpoints  # noqa: E402

register_project_case_study_endpoints(content_router)


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
    if getattr(item, "slug", "") == "no-preview":
        raise AdminError(
            404,
            "NOT_FOUND",
            "Preview links are not supported for this entity.",
        )
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


class ProfileSiblingLocaleIn(Schema):
    """Target locale for the new sibling-locale profile draft (G-G)."""

    targetLocale: str


def _serialize_sibling_profile(profile: Profile) -> dict[str, object]:
    """Legacy admin profile projection (content.admin_api parity)."""
    data = serialize_profile_detail(profile)
    data["status"] = profile.status
    data["revision"] = profile.revision
    data["translationStatus"] = resolve_translation_status(profile)
    return data


@content_router.post(
    "/profile/{id}/sibling-locale",
    response={201: dict},
    summary="Create the sibling-locale draft for a profile (G-G).",
)
def profile_create_sibling_locale(request, id: int, payload: ProfileSiblingLocaleIn):
    """Create the missing sibling-locale draft sharing the source translation_key.

    Mirrors the legacy ``POST /api/admin/profiles/{locale}/{slug}/siblings/
    {target_locale}`` behavior (apps/content/admin_api.py): the new row starts
    as an empty draft inheriting the source's slug, translation_key and
    updated_at; registry codes replace the legacy local codes.
    """
    _require_admin_otp(request)
    _check_csrf(request)
    source = Profile.objects.filter(pk=id).first()
    if source is None:
        raise AdminError(404, "NOT_FOUND", "Profile not found.")
    if payload.targetLocale not in VALID_LOCALES:
        raise AdminError(
            400,
            "VALIDATION",
            f"Invalid targetLocale. Expected one of: {', '.join(VALID_LOCALES)}.",
        )
    if payload.targetLocale == source.locale:
        raise AdminError(
            400,
            "VALIDATION",
            "The sibling locale must be different from the current locale.",
        )
    if Profile.objects.filter(
        translation_key=source.translation_key, locale=payload.targetLocale
    ).exists():
        raise AdminError(
            409,
            "DUPLICATE",
            "The sibling locale already exists for this profile family.",
        )
    if Profile.objects.filter(
        locale=payload.targetLocale, slug=source.slug
    ).exists():
        raise AdminError(
            409,
            "DUPLICATE",
            "That locale already has a different profile using this slug.",
        )
    try:
        with transaction.atomic():
            created = Profile.objects.create(
                locale=payload.targetLocale,
                slug=source.slug,
                title="",
                body="",
                seo_title="",
                seo_description="",
                short_bio="",
                long_bio="",
                availability="",
                status=LifecycleStatus.DRAFT,
                translation_key=source.translation_key,
                published_at=None,
            )
            # Legacy behavior: the sibling inherits the source row's updated_at
            # (update() bypasses auto_now).
            Profile.objects.filter(pk=created.pk).update(updated_at=source.updated_at)
    except IntegrityError:
        raise AdminError(
            409,
            "DUPLICATE",
            "The sibling locale already exists for this profile family.",
        ) from None
    created.refresh_from_db()
    AuditLog.objects.create(
        user=request.user,
        action="admin.profile.sibling_created",
        model_name="profile",
        object_id=str(created.pk),
        ip=_client_ip(request),
        detail=(
            f"profile family {source.translation_key} "
            f"source={source.locale}/{source.slug} "
            f"created={created.locale}/{created.slug}"
        ),
    )
    return {
        "editorUrl": f"/admin/content/profile/{created.pk}",
        "profile": _serialize_sibling_profile(
            Profile.objects.filter(pk=created.pk).get()
        ),
    }


from apps.api.admin_api import admin_api  # noqa: E402

admin_api.exception_handler(AdminConflictError)(_admin_conflict_handler)
