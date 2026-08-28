"""Custom admin site-configuration API (ADR-0026, ADM-5).

Staff + OTP protected endpoints over site settings, topic tags, and featured
items: ``GET/PUT /site`` (singleton settings, optimistically locked),
``GET/POST /tags`` + ``PUT/DELETE /tags/{id}`` (topic tag CRUD), and
``GET/POST /featured`` + ``PUT/DELETE /featured/{id}`` (time-window
spotlights). Unsafe methods additionally enforce the same-origin CSRF baseline.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.http import JsonResponse
from django.utils import timezone
from django.utils.text import slugify
from ninja import Router, Schema
from ninja.responses import Status

from apps.api.admin_common import (
    AdminError,
    _check_csrf,
    _parse_positive_int,
    _require_admin_otp,
)
from apps.api.admin_content import ENTITY_MODELS
from apps.content.models import Article, TopicTag
from apps.media.models import Media
from apps.siteconfig.models import FeaturedItem, SiteSettings

siteconfig_router = Router()

VALID_LOCALES = ("fa", "en")
HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
SLUG_RE = re.compile(r"^[a-z0-9-]+$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
URLISH_RE = re.compile(r"^https?://[^\s]+$")
# Relative path must be a single leading slash (no protocol-relative "//host"),
# or an absolute http(s) URL; bounded length (500).
HREF_RE = re.compile(r"^(https?://[^\s]+|/(?!/)[^\s]*)$")
NAV_HREF_MAX = 500
NAV_LINKS_MAX = 20
# CV/Resume current documents must be PDFs from the media library (§14 F5).
CV_DOCUMENT_MIME = "application/pdf"

# TopicTag model bounds (model max_length wins over any larger spec default).
TAG_NAME_MAX = 100
TAG_SLUG_MAX = 100


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


def _parse_datetime(value: str | None, key: str) -> datetime | None:
    """Parse an ISO-8601 datetime; malformed or naive values are a 400 VALIDATION."""
    if value is None or value == "":
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        raise AdminError(
            400,
            "VALIDATION",
            f"Invalid datetime for '{key}'. Expected ISO-8601.",
            fields={key: [f"Invalid datetime for '{key}'. Expected ISO-8601."]},
        ) from None
    if dt.tzinfo is None:
        raise AdminError(
            400,
            "VALIDATION",
            f"Invalid datetime for '{key}'. Must include a timezone.",
            fields={key: [f"Invalid datetime for '{key}'. Must include a timezone."]},
        )
    return dt


class NavLinkOut(Schema):
    """One navigation link entry stored in the settings singleton."""

    label: str
    href: str
    locale: str


class CurrentDocumentOut(Schema):
    """Admin projection of one current CV/resume media slot."""

    id: int
    title: str
    mime: str
    size: int
    isActive: bool
    url: str
    updatedAt: datetime


class SiteSettingsOut(Schema):
    """Site settings projection (camelCase)."""

    brandName: str
    tagline: str
    footerText: str
    primaryColor: str
    navLinks: list[NavLinkOut]
    seoDefaultTitle: str
    seoDefaultDescription: str
    contactEmail: str
    contactPhone: str
    contactPhoneIntl: str
    contactLocation: str
    contactLinkedin: str
    contactOrcid: str
    contactEmployer: str
    contactEmployerUrl: str
    contactFormEnabled: bool
    currentCvMediaId: int | None
    currentResumeMediaId: int | None
    currentCv: CurrentDocumentOut | None
    currentResume: CurrentDocumentOut | None
    updatedAt: datetime


class SiteSettingsUpdateIn(Schema):
    """Optimistically-locked partial settings update (unset = unchanged).

    ``currentCvMediaId`` / ``currentResumeMediaId``: omit to leave unchanged;
    send ``null`` to clear; send a positive media id to set (must be PDF).
    """

    brandName: str | None = None
    tagline: str | None = None
    footerText: str | None = None
    primaryColor: str | None = None
    navLinks: list[dict] | None = None
    seoDefaultTitle: str | None = None
    seoDefaultDescription: str | None = None
    contactEmail: str | None = None
    contactPhone: str | None = None
    contactPhoneIntl: str | None = None
    contactLocation: str | None = None
    contactLinkedin: str | None = None
    contactOrcid: str | None = None
    contactEmployer: str | None = None
    contactEmployerUrl: str | None = None
    contactFormEnabled: bool | None = None
    currentCvMediaId: int | None = None
    currentResumeMediaId: int | None = None


class TagOut(Schema):
    """Topic tag row with its article reference count."""

    id: int
    name: str
    slug: str
    locale: str
    articleCount: int


class TagListOut(Schema):
    """Paginated tag list envelope."""

    items: list[TagOut]
    page: int
    pageSize: int
    total: int


class TagCreateIn(Schema):
    """Create payload for a topic tag."""

    name: str
    slug: str | None = None
    locale: str


class TagUpdateIn(Schema):
    """Optimistically-locked partial tag update (None = unchanged)."""

    name: str | None = None
    slug: str | None = None
    locale: str | None = None


class FeaturedItemOut(Schema):
    """Featured spotlight row."""

    id: int
    title: str
    targetEntity: str
    targetSlug: str
    locale: str
    startAt: datetime | None
    endAt: datetime | None
    isActive: bool
    updatedAt: datetime


class FeaturedItemListOut(Schema):
    """Paginated featured list envelope."""

    items: list[FeaturedItemOut]
    page: int
    pageSize: int
    total: int


class FeaturedItemCreateIn(Schema):
    """Create payload for a featured spotlight."""

    title: str
    targetEntity: str
    targetSlug: str
    locale: str
    startAt: str
    endAt: str | None = None
    isActive: bool | None = None


class FeaturedItemUpdateIn(Schema):
    """Optimistically-locked partial featured update (None = unchanged)."""

    title: str | None = None
    targetEntity: str | None = None
    targetSlug: str | None = None
    locale: str | None = None
    startAt: str | None = None
    endAt: str | None = None
    isActive: bool | None = None


class OkOut(Schema):
    """Empty success body used by DELETE endpoints."""

    ok: bool


def _media_public_path(media: Media) -> str:
    """Relative public URL for an active media file (``/media/<stored-name>``)."""
    name = (media.file.name or "").lstrip("/")
    return f"/media/{name}"


def _serialize_current_document(media: Media | None) -> CurrentDocumentOut | None:
    if media is None:
        return None
    return CurrentDocumentOut(
        id=media.pk,
        title=media.title,
        mime=media.mime,
        size=media.size,
        isActive=media.is_active,
        url=_media_public_path(media),
        updatedAt=media.updated_at,
    )


def _resolve_cv_media(media_id: int | None, field: str) -> Media | None:
    """Resolve a current-document media id; ``None`` clears the slot."""
    if media_id is None:
        return None
    if not isinstance(media_id, int) or media_id < 1:
        raise AdminError(
            400,
            "VALIDATION",
            f"{field} must be a positive media id or null.",
            fields={field: [f"{field} must be a positive media id or null."]},
        )
    media = Media.objects.filter(pk=media_id).first()
    if media is None:
        raise AdminError(
            400,
            "VALIDATION",
            f"{field} media not found.",
            fields={field: [f"{field} media not found."]},
        )
    if media.mime != CV_DOCUMENT_MIME:
        raise AdminError(
            400,
            "VALIDATION",
            f"{field} must reference an application/pdf media row.",
            fields={
                field: [f"{field} must reference an application/pdf media row."]
            },
        )
    return media


def _serialize_site_settings(item: SiteSettings) -> SiteSettingsOut:
    cv = item.current_cv_media
    resume = item.current_resume_media
    return SiteSettingsOut(
        brandName=item.brand_name,
        tagline=item.tagline,
        footerText=item.footer_text,
        primaryColor=item.primary_color,
        navLinks=[NavLinkOut(**link) for link in item.nav_links],
        seoDefaultTitle=item.seo_default_title,
        seoDefaultDescription=item.seo_default_description,
        contactEmail=item.contact_email,
        contactPhone=item.contact_phone,
        contactPhoneIntl=item.contact_phone_intl,
        contactLocation=item.contact_location,
        contactLinkedin=item.contact_linkedin,
        contactOrcid=item.contact_orcid,
        contactEmployer=item.contact_employer,
        contactEmployerUrl=item.contact_employer_url,
        contactFormEnabled=item.contact_form_enabled,
        currentCvMediaId=cv.pk if cv is not None else None,
        currentResumeMediaId=resume.pk if resume is not None else None,
        currentCv=_serialize_current_document(cv),
        currentResume=_serialize_current_document(resume),
        updatedAt=item.updated_at,
    )


# ("payload key", "model attr", max length) for the public contact block.
_CONTACT_TEXT_FIELDS = [
    ("contactEmail", "contact_email", 254),
    ("contactPhone", "contact_phone", 32),
    ("contactPhoneIntl", "contact_phone_intl", 32),
    ("contactLocation", "contact_location", 200),
    ("contactLinkedin", "contact_linkedin", 300),
    ("contactOrcid", "contact_orcid", 300),
    ("contactEmployer", "contact_employer", 200),
    ("contactEmployerUrl", "contact_employer_url", 300),
]


def _apply_contact_fields(payload, item: SiteSettings) -> None:
    """Apply optional contact fields with strip + length validation."""
    for key, attr, max_len in _CONTACT_TEXT_FIELDS:
        value = getattr(payload, key)
        if value is None:
            continue
        stripped = value.strip()
        if len(stripped) > max_len:
            raise AdminError(
                400,
                "VALIDATION",
                f"{key} must not exceed {max_len} characters.",
                fields={key: [f"{key} must not exceed {max_len} characters."]},
            )
        url_fields = ("contact_linkedin", "contact_orcid", "contact_employer_url")
        if stripped and (key == "contactEmail" or attr in url_fields):
            if key == "contactEmail" and not EMAIL_RE.fullmatch(stripped):
                raise AdminError(
                    400,
                    "VALIDATION",
                    "contactEmail must be a valid email address.",
                    fields={"contactEmail": ["contactEmail must be a valid email address."]},
                )
            if key != "contactEmail" and not URLISH_RE.fullmatch(stripped):
                raise AdminError(
                    400,
                    "VALIDATION",
                    f"{key} must be an http(s) URL.",
                    fields={key: [f"{key} must be an http(s) URL."]},
                )
        setattr(item, attr, stripped)
    if payload.contactFormEnabled is not None:
        item.contact_form_enabled = payload.contactFormEnabled


def _validate_nav_links(value) -> list[dict]:
    """Validate a navLinks payload; raises 400 with a ``navLinks[i]`` field path."""
    if not isinstance(value, list):
        raise AdminError(
            400,
            "VALIDATION",
            "navLinks must be a list.",
            fields={"navLinks": ["navLinks must be a list."]},
        )
    if len(value) > NAV_LINKS_MAX:
        raise AdminError(
            400,
            "VALIDATION",
            f"navLinks must not exceed {NAV_LINKS_MAX} items.",
            fields={"navLinks": [f"navLinks must not exceed {NAV_LINKS_MAX} items."]},
        )
    for index, link in enumerate(value):
        if not isinstance(link, dict):
            raise AdminError(
                400,
                "VALIDATION",
                "Invalid navLink entry.",
                fields={f"navLinks[{index}]": ["Invalid navLink entry."]},
            )
        label = link.get("label")
        href = link.get("href")
        locale = link.get("locale")
        if not isinstance(label, str) or not 1 <= len(label.strip()) <= 200:
            raise AdminError(
                400,
                "VALIDATION",
                "Invalid navLink label.",
                fields={f"navLinks[{index}].label": ["Invalid navLink label."]},
            )
        if not isinstance(href, str) or len(href) > NAV_HREF_MAX or not HREF_RE.match(href):
            raise AdminError(
                400,
                "VALIDATION",
                "Invalid navLink href.",
                fields={f"navLinks[{index}].href": ["Invalid navLink href."]},
            )
        if locale not in VALID_LOCALES:
            raise AdminError(
                400,
                "VALIDATION",
                "Invalid navLink locale.",
                fields={f"navLinks[{index}].locale": ["Invalid navLink locale."]},
            )
    return value


def _validate_tag_slug(slug: str) -> str:
    """Validate a (possibly auto-derived) tag slug."""
    if not slug:
        raise AdminError(400, "VALIDATION", "slug must not be empty.")
    if len(slug) > TAG_SLUG_MAX or not SLUG_RE.fullmatch(slug):
        raise AdminError(
            400,
            "VALIDATION",
            "slug must match ^[a-z0-9-]+$.",
            fields={"slug": ["slug must match ^[a-z0-9-]+$."]},
        )
    return slug


def _validate_tag_name(name: str) -> str:
    stripped = name.strip()
    if not stripped:
        raise AdminError(400, "VALIDATION", "name must not be empty.")
    if len(stripped) > TAG_NAME_MAX:
        raise AdminError(
            400,
            "VALIDATION",
            f"name must not exceed {TAG_NAME_MAX} characters.",
            fields={"name": [f"name must not exceed {TAG_NAME_MAX} characters."]},
        )
    return stripped


def _serialize_tag(tag: TopicTag) -> TagOut:
    return TagOut(
        id=tag.pk,
        name=tag.name,
        slug=tag.slug,
        locale=tag.locale,
        articleCount=getattr(tag, "article_count", tag.articles.count()),
    )


def _check_featured_target(entity: str, locale: str, target_slug: str) -> None:
    """Validate a featured target entity + row reference."""
    if entity not in ENTITY_MODELS:
        raise AdminError(
            400,
            "VALIDATION",
            "Invalid targetEntity.",
            fields={"targetEntity": ["Invalid targetEntity."]},
        )
    model = ENTITY_MODELS[entity]
    if not model.objects.filter(locale=locale, slug=target_slug).exists():
        raise AdminError(
            400,
            "VALIDATION",
            "Target not found.",
            fields={"targetSlug": ["Target not found."]},
        )


def _coerce_featured(payload, *, partial: bool) -> dict:
    """Validate + coerce a featured create/update payload into model attrs."""
    values: dict = {}
    if not partial or payload.title is not None:
        title = (payload.title or "").strip()
        if not title:
            raise AdminError(400, "VALIDATION", "title must not be empty.")
        if len(title) > 200:
            raise AdminError(
                400,
                "VALIDATION",
                "title must not exceed 200 characters.",
                fields={"title": ["title must not exceed 200 characters."]},
            )
        values["title"] = title
    if not partial or payload.locale is not None:
        if payload.locale not in VALID_LOCALES:
            raise AdminError(
                400,
                "VALIDATION",
                f"Invalid locale. Expected one of: {', '.join(VALID_LOCALES)}.",
            )
        values["locale"] = payload.locale
    if not partial or payload.targetSlug is not None:
        target_slug = (payload.targetSlug or "").strip()
        if not target_slug:
            raise AdminError(400, "VALIDATION", "targetSlug must not be empty.")
        if len(target_slug) > 200:
            raise AdminError(
                400,
                "VALIDATION",
                "targetSlug must not exceed 200 characters.",
                fields={"targetSlug": ["targetSlug must not exceed 200 characters."]},
            )
        values["target_slug"] = target_slug
    if not partial or payload.startAt is not None:
        start_at = _parse_datetime(payload.startAt, "startAt")
        if start_at is None:
            raise AdminError(400, "VALIDATION", "startAt is required.")
        values["start_at"] = start_at
    if not partial or payload.endAt is not None:
        values["end_at"] = _parse_datetime(payload.endAt, "endAt")
    if payload.isActive is not None:
        values["is_active"] = payload.isActive
    if (
        "start_at" in values
        and values.get("end_at") is not None
        and values["end_at"] < values["start_at"]
    ):
        raise AdminError(
            400,
            "VALIDATION",
            "endAt must be after startAt.",
            fields={"endAt": ["endAt must be after startAt."]},
        )
    if not partial or payload.targetEntity is not None:
        values["target_entity"] = payload.targetEntity
    if "target_entity" in values and "locale" in values and "target_slug" in values:
        _check_featured_target(
            values["target_entity"], values["locale"], values["target_slug"]
        )
    return values


def _serialize_featured(item: FeaturedItem) -> FeaturedItemOut:
    return FeaturedItemOut(
        id=item.pk,
        title=item.title,
        targetEntity=item.target_entity,
        targetSlug=item.target_slug,
        locale=item.locale,
        startAt=item.start_at,
        endAt=item.end_at,
        isActive=item.is_active,
        updatedAt=item.updated_at,
    )


@siteconfig_router.get("/site", response=SiteSettingsOut, summary="Get site settings.")
def site_settings_get(request):
    _require_admin_otp(request)
    item = SiteSettings.objects.select_related(
        "current_cv_media", "current_resume_media"
    ).first()
    if item is None:
        item = SiteSettings.get_singleton()
    return _serialize_site_settings(item)


@siteconfig_router.put(
    "/site",
    response=SiteSettingsOut,
    summary="Update site settings (optimistic locking).",
)
def site_settings_put(request, payload: SiteSettingsUpdateIn):
    _require_admin_otp(request)
    _check_csrf(request)
    # Distinguish omitted fields from explicit null (clear CV/resume slots).
    provided = (
        payload.model_dump(exclude_unset=True)
        if hasattr(payload, "model_dump")
        else payload.dict(exclude_unset=True)
    )
    with transaction.atomic():
        item = (
            SiteSettings.objects.select_for_update()
            .select_related("current_cv_media", "current_resume_media")
            .first()
        )
        if item is None:
            item = SiteSettings.objects.create()
        if not _if_match_matches(request.headers.get("If-Match"), item):
            raise AdminConflictError(_serialize_updated_at(item.updated_at))
        if payload.brandName is not None:
            brand_name = payload.brandName.strip()
            if len(brand_name) > 200:
                raise AdminError(
                    400,
                    "VALIDATION",
                    "brandName must not exceed 200 characters.",
                    fields={"brandName": ["brandName must not exceed 200 characters."]},
                )
            item.brand_name = brand_name
        if payload.tagline is not None:
            tagline = payload.tagline.strip()
            if len(tagline) > 500:
                raise AdminError(
                    400,
                    "VALIDATION",
                    "tagline must not exceed 500 characters.",
                    fields={"tagline": ["tagline must not exceed 500 characters."]},
                )
            item.tagline = tagline
        if payload.footerText is not None:
            footer_text = payload.footerText.strip()
            if len(footer_text) > 5000:
                raise AdminError(
                    400,
                    "VALIDATION",
                    "footerText must not exceed 5000 characters.",
                    fields={"footerText": ["footerText must not exceed 5000 characters."]},
                )
            item.footer_text = footer_text
        if payload.primaryColor is not None:
            if not HEX_COLOR_RE.fullmatch(payload.primaryColor.strip()):
                raise AdminError(
                    400,
                    "VALIDATION",
                    "primaryColor must match ^#[0-9a-fA-F]{6}$.",
                    fields={
                        "primaryColor": ["primaryColor must match ^#[0-9a-fA-F]{6}$."]
                    },
                )
            item.primary_color = payload.primaryColor.strip()
        if payload.navLinks is not None:
            item.nav_links = _validate_nav_links(payload.navLinks)
        if payload.seoDefaultTitle is not None:
            seo_title = payload.seoDefaultTitle.strip()
            if len(seo_title) > 200:
                raise AdminError(
                    400,
                    "VALIDATION",
                    "seoDefaultTitle must not exceed 200 characters.",
                    fields={
                        "seoDefaultTitle": ["seoDefaultTitle must not exceed 200 characters."]
                    },
                )
            item.seo_default_title = seo_title
        if payload.seoDefaultDescription is not None:
            seo_description = payload.seoDefaultDescription.strip()
            if len(seo_description) > 1000:
                raise AdminError(
                    400,
                    "VALIDATION",
                    "seoDefaultDescription must not exceed 1000 characters.",
                    fields={
                        "seoDefaultDescription": [
                            "seoDefaultDescription must not exceed 1000 characters."
                        ]
                    },
                )
            item.seo_default_description = seo_description
        _apply_contact_fields(payload, item)
        if "currentCvMediaId" in provided:
            item.current_cv_media = _resolve_cv_media(
                provided["currentCvMediaId"], "currentCvMediaId"
            )
        if "currentResumeMediaId" in provided:
            item.current_resume_media = _resolve_cv_media(
                provided["currentResumeMediaId"], "currentResumeMediaId"
            )
        item.save()
    item = (
        SiteSettings.objects.select_related("current_cv_media", "current_resume_media")
        .filter(pk=item.pk)
        .first()
    )
    return _serialize_site_settings(item)


@siteconfig_router.get("/tags", response=TagListOut, summary="List topic tags.")
def tag_list(
    request,
    q: str | None = None,
    locale: str | None = None,
    page: str = "1",
    pageSize: str = "20",
):
    _require_admin_otp(request)
    if locale is not None and locale not in VALID_LOCALES:
        raise AdminError(
            400, "VALIDATION", f"Invalid locale. Expected one of: {', '.join(VALID_LOCALES)}."
        )
    page_num = _parse_positive_int(request, "page", page, default=1, max_value=1_000_000)
    page_size = _parse_positive_int(request, "pageSize", pageSize, default=20, max_value=100)

    qs = TopicTag.objects.all()
    if locale is not None:
        qs = qs.filter(locale=locale)
    if q is not None:
        qs = qs.filter(Q(name__icontains=q) | Q(slug__icontains=q))

    total = qs.count()
    rows = list(
        qs.annotate(article_count=Count("articles"))
        .order_by("locale", "name")[(page_num - 1) * page_size : page_num * page_size]
    )
    return TagListOut(
        items=[_serialize_tag(tag) for tag in rows],
        page=page_num,
        pageSize=page_size,
        total=total,
    )


@siteconfig_router.post("/tags", response={201: TagOut}, summary="Create a topic tag.")
def tag_create(request, payload: TagCreateIn):
    _require_admin_otp(request)
    _check_csrf(request)
    if payload.locale not in VALID_LOCALES:
        raise AdminError(
            400, "VALIDATION", f"Invalid locale. Expected one of: {', '.join(VALID_LOCALES)}."
        )
    name = _validate_tag_name(payload.name)
    slug = payload.slug
    if slug is None or not slug.strip():
        slug = slugify(name)
    slug = _validate_tag_slug(slug.strip())
    if TopicTag.objects.filter(locale=payload.locale, slug=slug).exists():
        raise AdminError(409, "DUPLICATE", "A tag with this locale and slug already exists.")
    try:
        tag = TopicTag.objects.create(name=name, slug=slug, locale=payload.locale)
    except IntegrityError:
        raise AdminError(
            409, "DUPLICATE", "A tag with this locale and slug already exists."
        ) from None
    return Status(201, _serialize_tag(tag))


@siteconfig_router.put("/tags/{id}", response=TagOut, summary="Update a topic tag.")
def tag_update(request, id: int, payload: TagUpdateIn):
    _require_admin_otp(request)
    _check_csrf(request)
    tag = TopicTag.objects.filter(pk=id).first()
    if tag is None:
        raise AdminError(404, "NOT_FOUND", "Tag not found.")
    # TopicTag has no timestamp, so If-Match cannot be compared; the header is
    # accepted for API symmetry with the other admin PUT endpoints.
    if payload.name is not None:
        tag.name = _validate_tag_name(payload.name)
    if payload.slug is not None:
        slug = payload.slug.strip()
        if not slug:
            raise AdminError(400, "VALIDATION", "slug must not be empty.")
        slug = _validate_tag_slug(slug)
        if TopicTag.objects.filter(locale=tag.locale, slug=slug).exclude(pk=tag.pk).exists():
            raise AdminError(409, "DUPLICATE", "A tag with this locale and slug already exists.")
        tag.slug = slug
    if payload.locale is not None:
        if payload.locale not in VALID_LOCALES:
            raise AdminError(
                400,
                "VALIDATION",
                f"Invalid locale. Expected one of: {', '.join(VALID_LOCALES)}.",
            )
        tag.locale = payload.locale
    try:
        tag.save()
    except IntegrityError:
        raise AdminError(
            409, "DUPLICATE", "A tag with this locale and slug already exists."
        ) from None
    return _serialize_tag(tag)


@siteconfig_router.delete("/tags/{id}", response={204: OkOut}, summary="Delete a topic tag.")
def tag_delete(request, id: int):
    _require_admin_otp(request)
    _check_csrf(request)
    tag = TopicTag.objects.filter(pk=id).first()
    if tag is None:
        raise AdminError(404, "NOT_FOUND", "Tag not found.")
    if Article.objects.filter(topic_tags__pk=id).exists():
        raise AdminError(409, "IN_USE", "Tag is referenced by articles.")
    tag.delete()
    return Status(204, OkOut(ok=True))


@siteconfig_router.get("/featured", response=FeaturedItemListOut, summary="List featured items.")
def featured_list(
    request,
    active: str | None = None,
    current: str | None = None,
    page: str = "1",
    pageSize: str = "20",
):
    _require_admin_otp(request)
    if active is not None and active not in ("true", "false"):
        raise AdminError(400, "VALIDATION", "Invalid active. Expected true or false.")
    if current is not None and current not in ("true", "false"):
        raise AdminError(400, "VALIDATION", "Invalid current. Expected true or false.")
    page_num = _parse_positive_int(request, "page", page, default=1, max_value=1_000_000)
    page_size = _parse_positive_int(request, "pageSize", pageSize, default=20, max_value=100)

    qs = FeaturedItem.objects.all()
    if active == "true":
        qs = qs.filter(is_active=True)
    elif active == "false":
        qs = qs.filter(is_active=False)
    if current == "true":
        now = timezone.now()
        qs = qs.filter(start_at__lte=now).filter(Q(end_at__isnull=True) | Q(end_at__gte=now))

    total = qs.count()
    rows = list(qs[(page_num - 1) * page_size : page_num * page_size])
    return FeaturedItemListOut(
        items=[_serialize_featured(item) for item in rows],
        page=page_num,
        pageSize=page_size,
        total=total,
    )


@siteconfig_router.post(
    "/featured", response={201: FeaturedItemOut}, summary="Create a featured item."
)
def featured_create(request, payload: FeaturedItemCreateIn):
    _require_admin_otp(request)
    _check_csrf(request)
    values = _coerce_featured(payload, partial=False)
    with transaction.atomic():
        item = FeaturedItem.objects.create(**values)
        if item.is_active:
            FeaturedItem.objects.filter(is_active=True).exclude(pk=item.pk).update(
                is_active=False
            )
    return Status(201, _serialize_featured(item))


@siteconfig_router.put(
    "/featured/{id}",
    response=FeaturedItemOut,
    summary="Update a featured item (optimistic locking).",
)
def featured_update(request, id: int, payload: FeaturedItemUpdateIn):
    _require_admin_otp(request)
    _check_csrf(request)
    try:
        with transaction.atomic():
            item = FeaturedItem.objects.select_for_update().get(pk=id)
            if not _if_match_matches(request.headers.get("If-Match"), item):
                raise AdminConflictError(_serialize_updated_at(item.updated_at))
            values = _coerce_featured(payload, partial=True)
            merged = {
                "target_entity": values.get("target_entity", item.target_entity),
                "target_slug": values.get("target_slug", item.target_slug),
                "locale": values.get("locale", item.locale),
                "start_at": values.get("start_at", item.start_at),
                "end_at": values.get("end_at", item.end_at),
            }
            if (
                merged["end_at"] is not None
                and merged["end_at"] < merged["start_at"]
            ):
                raise AdminError(
                    400,
                    "VALIDATION",
                    "endAt must be after startAt.",
                    fields={"endAt": ["endAt must be after startAt."]},
                )
            if any(key in values for key in ("target_entity", "target_slug", "locale")):
                _check_featured_target(
                    merged["target_entity"],
                    merged["locale"],
                    merged["target_slug"],
                )
            for attr, value in values.items():
                setattr(item, attr, value)
            if values.get("is_active") is True:
                # exactly one active spotlight: activating this one turns others off
                FeaturedItem.objects.filter(is_active=True).exclude(pk=item.pk).update(
                    is_active=False
                )
            item.save()
    except FeaturedItem.DoesNotExist:
        raise AdminError(404, "NOT_FOUND", "Featured item not found.") from None
    return _serialize_featured(item)


@siteconfig_router.delete(
    "/featured/{id}", response={204: OkOut}, summary="Delete a featured item."
)
def featured_delete(request, id: int):
    _require_admin_otp(request)
    _check_csrf(request)
    item = FeaturedItem.objects.filter(pk=id).first()
    if item is None:
        raise AdminError(404, "NOT_FOUND", "Featured item not found.")
    item.delete()
    return Status(204, OkOut(ok=True))


from apps.api.admin_api import admin_api  # noqa: E402

admin_api.exception_handler(AdminConflictError)(_admin_conflict_handler)
