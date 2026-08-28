"""Custom admin overview API: translation queue + content health (ADR-0026, ADM-4).

Staff + OTP protected read endpoints summarizing cross-entity content state:
``/overview/translation-queue`` (per-slug fa/en completeness) and
``/overview/content-health`` (aggregate lifecycle / media-health counts).
Read-only; no writes. The registry of entities is shared with the content
admin API (``ENTITY_MODELS``) and media orphan counting reuses
``media_usage_count`` from the media admin API.
"""

from __future__ import annotations

from ninja import Field, Router, Schema

health_router = Router()

from apps.api.admin_common import _require_admin_otp  # noqa: E402
from apps.api.admin_content import ENTITY_MODELS  # noqa: E402
from apps.api.admin_media import media_usage_count  # noqa: E402
from apps.media.models import Media  # noqa: E402

# The body-ish field per entity used for translation completeness (ADM-4).
BODY_FIELDS = {
    "landing": "body",
    "profile": "body",
    "article": "body",
    "research-topic": "summary",
    "research-statement": "body",
    "project": "objective",
    "publication": "authors",
}

QUEUE_LIMIT = 100


class LocaleTranslationOut(Schema):
    """Completeness of one locale row within a translation group."""

    status: str  # "complete" | "incomplete" | "missing"
    missingFields: list[str] = Field(default_factory=list)


class TranslationQueueItemOut(Schema):
    """One cross-locale (slug) group with per-locale completeness."""

    entity: str
    slug: str
    fa: LocaleTranslationOut
    en: LocaleTranslationOut
    status: str  # "complete" | "incomplete" | "missing" | "partial"


class TranslationQueueOut(Schema):
    """Bounded translation queue listing."""

    items: list[TranslationQueueItemOut] = Field(default_factory=list)
    truncated: bool = False


class ContentHealthOut(Schema):
    """Aggregate lifecycle counts plus media-health indicators."""

    published: int
    drafts: int
    review: int
    scheduled: int = 0
    archived: int
    incompleteTranslations: int
    missingAltMedia: int
    orphanMedia: int


def _locale_row_status(row, entity: str) -> LocaleTranslationOut:
    """Completeness of one locale row: title + the entity's body-ish field."""
    if row is None:
        return LocaleTranslationOut(status="missing")
    missing: list[str] = []
    if not (row.title or "").strip():
        missing.append("title")
    body_attr = BODY_FIELDS[entity]
    if not (getattr(row, body_attr) or "").strip():
        missing.append(body_attr)
    if missing:
        return LocaleTranslationOut(status="incomplete", missingFields=missing)
    return LocaleTranslationOut(status="complete")


def _group_status(fa: LocaleTranslationOut, en: LocaleTranslationOut) -> str:
    """Aggregate a cross-locale group from its per-locale completeness."""
    if fa.status == "missing" or en.status == "missing":
        return "missing"
    if fa.status == "complete" and en.status == "complete":
        return "complete"
    if fa.status == "incomplete" and en.status == "incomplete":
        return "incomplete"
    return "partial"


def _translation_groups():
    """All (entity, slug, fa_row, en_row) groups ordered by slug ascending.

    Rows are grouped by ``slug`` across locales within each entity; a group is
    a locale pair (at most one row per locale given the unique constraints).
    """
    groups: list[tuple[str, str, object | None, object | None]] = []
    for entity, model in ENTITY_MODELS.items():
        by_slug: dict[str, dict[str, object]] = {}
        for row in model.objects.all():
            by_slug.setdefault(row.slug, {})[row.locale] = row
        for slug, locales in by_slug.items():
            groups.append((entity, slug, locales.get("fa"), locales.get("en")))
    groups.sort(key=lambda group: (group[1], group[0]))
    return groups


def _serialize_groups(groups) -> list[TranslationQueueItemOut]:
    items: list[TranslationQueueItemOut] = []
    for entity, slug, fa_row, en_row in groups:
        fa = _locale_row_status(fa_row, entity)
        en = _locale_row_status(en_row, entity)
        items.append(
            TranslationQueueItemOut(
                entity=entity,
                slug=slug,
                fa=fa,
                en=en,
                status=_group_status(fa, en),
            )
        )
    return items


@health_router.get(
    "/translation-queue",
    response=TranslationQueueOut,
    summary="Per-slug translation queue.",
)
def translation_queue(request):
    _require_admin_otp(request)
    items = _serialize_groups(_translation_groups())
    return TranslationQueueOut(
        items=items[:QUEUE_LIMIT],
        truncated=len(items) > QUEUE_LIMIT,
    )


@health_router.get(
    "/content-health",
    response=ContentHealthOut,
    summary="Content health counts.",
)
def content_health(request):
    _require_admin_otp(request)
    published = drafts = review = scheduled = archived = 0
    for model in ENTITY_MODELS.values():
        published += model.objects.filter(status="published").count()
        drafts += model.objects.filter(status="draft").count()
        review += model.objects.filter(status="review").count()
        scheduled += model.objects.filter(status="scheduled").count()
        archived += model.objects.filter(status="archived").count()
    missing_alt = Media.objects.filter(
        alt_text="",
        alt_text_fa="",
        alt_text_en="",
    ).count()
    orphans = 0
    for media in Media.objects.all().iterator():
        if media_usage_count(media) == 0:
            orphans += 1
    groups = _serialize_groups(_translation_groups())
    incomplete = sum(1 for item in groups if item.status != "complete")
    return ContentHealthOut(
        published=published,
        drafts=drafts,
        review=review,
        scheduled=scheduled,
        archived=archived,
        incompleteTranslations=incomplete,
        missingAltMedia=missing_alt,
        orphanMedia=orphans,
    )
