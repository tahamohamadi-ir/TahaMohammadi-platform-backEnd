"""Public read-only CMS API (django-ninja) — published content only, no auth.

Only fields safe for public projection are exposed: no status, no internal
notes. Unknown resources raise 404 with a JSON body and no stack trace.

Public edge exposure of ``/api/`` remains deferred (DEFER-0017); this module is
for in-process and optional build-time ``CMS_API_BASE`` consumers only.
"""

import re
from datetime import date, datetime
from pathlib import PurePosixPath

from django.http import FileResponse
from ninja import Field, NinjaAPI, Schema
from ninja.errors import HttpError
from ninja.errors import ValidationError as NinjaValidationError
from ninja.pagination import PageNumberPagination, paginate

from apps.api.admin_siteconfig import (
    LocalizedAudienceLinkOut,
    LocalizedFeaturedRecordOut,
    LocalizedNavLinkOut,
    LocalizedSceneOut,
    LocalizedSiteSeoOut,
)
from apps.api.record_resolver import (
    _ID_RE,
    MAX_ID,
    RESOLVER_FAMILIES,
    ROUTE_FAMILY_MAP,
    ErrorEnvelopeOut,
    WorkRefOut,
    _error_response,
    _extract_summary,
)
from apps.composition.projection import public_story_document
from apps.content.models import (
    AccessState,
    Article,
    ArticleSlugRedirect,
    Book,
    Collection,
    Course,
    CreativeWork,
    Download,
    GraphNode,
    GraphNodeRelated,
    GraphVersion,
    HomeModule,
    Landing,
    Lesson,
    Locale,
    Profile,
    Project,
    Publication,
    ResearchStatement,
    ResearchTopic,
    Series,
    Talk,
    TopicTag,
)
from apps.content.published import (
    FAMILY_TO_ENTITY_KEY,
    order_like,
    published_list_extras,
    resolve_published_detail,
    resolve_published_target,
)
from apps.content.services.public_projection import (
    published_for_locale,
    sanitize_public_richtext,
)
from apps.media.models import Media
from apps.media.public_urls import public_media_ref
from apps.siteconfig.models import LocalizedSiteSettings, SiteSettings

api = NinjaAPI(title="Taha CMS Public API", version="0.4.0")


_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


class PublicDownloadOut(Schema):
    """One current CV/resume download from the media library (active only)."""

    kind: str
    title: str
    note: str
    href: str
    mime: str
    size_bytes: int
    updated_at: datetime | None


class PublicContactBlockOut(Schema):
    """Public contact details (owner-published; empty strings are omitted upstream).

    Phone numbers are deliberately NOT projected — they stay private in the
    admin settings (owner decision 2026-08-23).
    """

    email: str = ""
    location: str = ""
    linkedin: str = ""
    orcid: str = ""
    employer: str = ""
    employerUrl: str = ""
    formEnabled: bool = False


class PublicSiteSettingsOut(Schema):
    """Public site presentation used by Astro at build time.

    Only ``primaryColor``, active current-document downloads and the public
    contact block are projected. Inactive media slots are omitted
    (private-by-default).
    """

    primaryColor: str
    downloads: list[PublicDownloadOut] = Field(default_factory=list)
    contact: PublicContactBlockOut = Field(default_factory=PublicContactBlockOut)
    brandName: str = ""
    tagline: str = ""
    footerText: str = ""
    seoDefaultTitle: str = ""
    seoDefaultDescription: str = ""


def _public_media_href(media: Media) -> str:
    name = (media.file.name or "").lstrip("/")
    return f"/media/{name}"


def _public_download(kind: str, media: Media | None) -> PublicDownloadOut | None:
    if media is None or not media.is_active:
        return None
    return PublicDownloadOut(
        kind=kind,
        title=media.title,
        note=(media.alt_text or "").strip(),
        href=_public_media_href(media),
        mime=media.mime,
        size_bytes=media.size,
        updated_at=media.updated_at,
    )


class LandingOut(Schema):
    """Public projection of a published landing page."""

    locale: str
    slug: str
    title: str
    body: str
    seo_title: str
    seo_description: str
    published_at: datetime | None


class ProfileOut(Schema):
    """Public projection of a published profile page."""

    locale: str
    slug: str
    title: str
    body: str
    seo_title: str
    seo_description: str
    published_at: datetime | None


class TopicTagOut(Schema):
    """Public topic tag projection (P10-01 glossary)."""

    name: str
    slug: str
    locale: str
    description: str = ""
    synonyms: str = ""


class SeriesOut(Schema):
    """Public series projection (published series only)."""

    locale: str
    slug: str
    title: str
    description: str
    ordering: int
    published_at: datetime | None


class PublicMediaOut(Schema):
    """Active Media library projection (URL only when is_active)."""

    url: str
    alt: str
    mime: str = ""
    title: str = ""
    size: int = 0


class ArticleListOut(Schema):
    """Public article list card (no full body)."""

    locale: str
    slug: str
    title: str
    excerpt: str
    license: str
    reading_time_minutes: int
    published_at: datetime | None
    updated_at: datetime | None
    topic_tags: list[TopicTagOut] = Field(default_factory=list)
    series: list[SeriesOut] = Field(default_factory=list)
    featured_image: PublicMediaOut | None = None

    @staticmethod
    def resolve_topic_tags(obj: Article) -> list[TopicTag]:
        return list(obj.topic_tags.filter(locale=obj.locale).order_by("name"))

    @staticmethod
    def resolve_series(obj: Article) -> list[Series]:
        return list(obj.series.public().order_by("ordering", "slug"))

    @staticmethod
    def resolve_featured_image(obj: Article, context) -> dict | None:
        request = context.get("request") if context else None
        return public_media_ref(
            getattr(obj, "featured_image", None),
            request,
            locale=obj.locale,
        )


class StoryBlockOut(Schema):
    blockType: str
    settings: dict = Field(default_factory=dict)


class StorySectionOut(Schema):
    layout: str
    ratio: str
    blocks: list[StoryBlockOut] = Field(default_factory=list)


class StoryDocumentOut(Schema):
    locale: str
    title: str
    sections: list[StorySectionOut] = Field(default_factory=list)


class PublicSeoOut(Schema):
    """Publication metadata SEO projection with title/description/image."""

    title: str
    description: str
    image: str | None = None


class AlternateLocaleOut(Schema):
    """Alternate locale route projection resolved via explicit translation key."""

    locale: str
    slug: str
    routeFamily: str
    courseSlug: str | None = None


def _resolve_public_seo(obj, context=None) -> PublicSeoOut:
    title = (getattr(obj, "seo_title", "") or "").strip()
    if not title:
        title = (getattr(obj, "title", "") or "").strip()

    description = (getattr(obj, "seo_description", "") or "").strip()
    if not description:
        family = obj.__class__.__name__.lower()
        if family == "collection":
            summary_raw = getattr(obj, "description", "") or getattr(obj, "title", "") or ""
            description = summary_raw.strip()
        else:
            description = _extract_summary(family, obj)

    image_url = None
    social_img = getattr(obj, "social_image", None)
    if not social_img:
        social_img = getattr(obj, "featured_image", None) or getattr(obj, "cover_media", None)
    if social_img:
        request = context.get("request") if context else None
        ref = public_media_ref(social_img, request, locale=getattr(obj, "locale", None))
        if isinstance(ref, dict):
            image_url = ref.get("url")
        elif hasattr(ref, "url"):
            image_url = ref.url

    return PublicSeoOut(
        title=title,
        description=description,
        image=image_url,
    )


PUBLIC_RESOLVER_FAMILIES: dict[str, type] = {
    **RESOLVER_FAMILIES,
    "lesson": Lesson,
    "collection": Collection,
}

PUBLIC_ROUTE_FAMILY_MAP: dict[str, str] = {
    **ROUTE_FAMILY_MAP,
    "lesson": "education",
    "collection": "collections",
}


def _entity_key_for_family(family_str: str) -> str:
    """Map a public family name to the snapshot entity key (A04)."""
    return FAMILY_TO_ENTITY_KEY.get(family_str, family_str)


def _resolve_public_alternates(obj) -> list[AlternateLocaleOut]:
    translation_key = getattr(obj, "translation_key", None)
    if not translation_key:
        return []

    model = obj.__class__
    public_mgr = getattr(model.objects, "public", None)
    if public_mgr is None:
        return []

    siblings = list(
        public_mgr()
        .filter(translation_key=translation_key)
        .exclude(pk=obj.pk)
        .order_by("locale")
    )
    family = model.__name__.lower()
    route_family = PUBLIC_ROUTE_FAMILY_MAP.get(family, family)
    entity_key = _entity_key_for_family(family)

    alternates: list[AlternateLocaleOut] = []
    seen_locales: set[str] = set()

    def _append_alternate(sibling) -> None:
        if sibling.locale in seen_locales:
            return
        seen_locales.add(sibling.locale)
        course_slug = None
        if hasattr(sibling, "course"):
            course_slug = getattr(getattr(sibling, "course", None), "slug", None)
        alternates.append(
            AlternateLocaleOut(
                locale=sibling.locale,
                slug=sibling.slug,
                routeFamily=route_family,
                courseSlug=course_slug,
            )
        )

    for sibling in siblings:
        _append_alternate(sibling)
    # A04: still-published (snapshot-backed) translations stay linked.
    live_ids = {s.pk for s in siblings} | {obj.pk}
    for other_locale in ("fa", "en"):
        if other_locale == getattr(obj, "locale", None):
            continue
        for extra in published_list_extras(model, entity_key, other_locale, live_ids):
            if getattr(extra, "translation_key", None) == translation_key:
                _append_alternate(extra)
                live_ids.add(extra.pk)
    alternates.sort(key=lambda a: a.locale)
    return alternates


def _resolve_public_related_records(obj) -> list[WorkRefOut]:
    raw_refs = getattr(obj, "related_records", None)
    if not raw_refs or not isinstance(raw_refs, list):
        return []

    locale = getattr(obj, "locale", None)
    if not locale:
        return []

    results: list[WorkRefOut] = []
    for ref in raw_refs:
        if not isinstance(ref, dict):
            continue
        family = ref.get("family")
        raw_id = ref.get("id")
        if not family or not raw_id:
            continue
        family_str = str(family).lower()
        model = PUBLIC_RESOLVER_FAMILIES.get(family_str)
        if model is None:
            continue
        raw_id_str = str(raw_id)
        if not _ID_RE.match(raw_id_str):
            continue
        try:
            pk = int(raw_id_str)
            if pk > MAX_ID:
                continue
        except (TypeError, ValueError):
            continue

        public_mgr = getattr(model.objects, "public", None)
        if public_mgr is None:
            continue
        target = resolve_published_target(
            model, _entity_key_for_family(family_str), pk, locale
        )
        if target is None:
            continue

        route_family = PUBLIC_ROUTE_FAMILY_MAP.get(family_str, family_str)
        if family_str == "lesson":
            summary = (getattr(target, "summary", "") or getattr(target, "title", "") or "").strip()
            course_slug = getattr(getattr(target, "course", None), "slug", None)
        elif family_str == "collection":
            summary_raw = getattr(target, "description", "") or getattr(target, "title", "") or ""
            summary = summary_raw.strip()
            course_slug = None
        else:
            summary = _extract_summary(family_str, target)
            course_slug = None
        results.append(
            WorkRefOut(
                family=family_str,
                id=str(target.pk),
                locale=target.locale,
                slug=target.slug,
                title=target.title,
                summary=summary,
                routeFamily=route_family,
                courseSlug=course_slug,
            )
        )
    return results


class PublicPublicationMetadataMixinOut(Schema):
    """Shared publication metadata projection: SEO, translation alternates, related records."""

    seo: PublicSeoOut | None = None
    alternates: list[AlternateLocaleOut] = Field(default_factory=list)
    relatedRecords: list[WorkRefOut] = Field(default_factory=list)

    @staticmethod
    def resolve_seo(obj, context) -> PublicSeoOut:
        return _resolve_public_seo(obj, context)

    @staticmethod
    def resolve_alternates(obj) -> list[AlternateLocaleOut]:
        return _resolve_public_alternates(obj)

    @staticmethod
    def resolve_relatedRecords(obj) -> list[WorkRefOut]:
        return _resolve_public_related_records(obj)


class ArticleDetailOut(ArticleListOut, PublicPublicationMetadataMixinOut):
    """Public article detail including sanitized rich-text body and optional story."""

    body: str
    accessibility_notes: str
    story: StoryDocumentOut | None = None

    @staticmethod
    def resolve_body(obj: Article) -> str:
        return sanitize_public_richtext(str(obj.body or ""))

    @staticmethod
    def resolve_story(obj: Article) -> dict | None:
        return public_story_document(getattr(obj, "story", None), obj.locale)


class ArticleSlugRedirectOut(Schema):
    """Public slug-redirect mapping for stable URLs after slug changes."""

    locale: str
    old_slug: str
    new_slug: str


@api.get(
    "/site",
    response=PublicSiteSettingsOut,
    summary="Public site settings (primaryColor + current CV/resume downloads)",
)
def get_public_site_settings(request) -> PublicSiteSettingsOut:
    settings = (
        SiteSettings.objects.select_related("current_cv_media", "current_resume_media")
        .filter(site_key="default")
        .first()
    )
    if settings is None:
        settings = SiteSettings.get_singleton()
    color = (settings.primary_color or "").strip()
    if not _HEX_COLOR_RE.fullmatch(color):
        color = "#1f2937"
    downloads: list[PublicDownloadOut] = []
    for kind, media in (
        ("academic_cv", settings.current_cv_media),
        ("industry_resume", settings.current_resume_media),
    ):
        item = _public_download(kind, media)
        if item is not None:
            downloads.append(item)
    return PublicSiteSettingsOut(
        primaryColor=color,
        downloads=downloads,
        contact=PublicContactBlockOut(
            email=(settings.contact_email or "").strip(),
            location=(settings.contact_location or "").strip(),
            linkedin=(settings.contact_linkedin or "").strip(),
            orcid=(settings.contact_orcid or "").strip(),
            employer=(settings.contact_employer or "").strip(),
            employerUrl=(settings.contact_employer_url or "").strip(),
            formEnabled=bool(settings.contact_form_enabled),
        ),
        brandName=(settings.brand_name or "").strip(),
        tagline=(settings.tagline or "").strip(),
        footerText=(settings.footer_text or "").strip(),
        seoDefaultTitle=(settings.seo_default_title or "").strip(),
        seoDefaultDescription=(settings.seo_default_description or "").strip(),
    )


class LocalizedSiteSettingsPublicOut(Schema):
    """Published localized site settings (PRODUCT-V2 §I04)."""

    locale: str
    revision: str
    brandName: str
    contentCopy: dict[str, str] = Field(default_factory=dict)
    featuredRecords: list[LocalizedFeaturedRecordOut] = Field(default_factory=list)
    brandMedia: PublicMediaOut | None = None
    tagline: str
    footerText: str
    seo: LocalizedSiteSeoOut
    navLinks: list[LocalizedNavLinkOut] = Field(default_factory=list)
    audienceLinks: list[LocalizedAudienceLinkOut] = Field(default_factory=list)
    scene: LocalizedSceneOut
    updatedAt: str


@api.get(
    "/v1/site/{locale}",
    response={200: LocalizedSiteSettingsPublicOut, 404: ErrorEnvelopeOut},
    summary="Published localized site settings for a locale (fail-closed, no fallback).",
)
def get_localized_site_settings(request, locale: str):
    """Serve published localized site settings for a locale (fail-closed, no fallback)."""
    if locale not in ("fa", "en"):
        return _error_response(
            request, 404, "NOT_FOUND", f"Locale '{locale}' not supported."
        )
    item = LocalizedSiteSettings.objects.filter(locale=locale).first()
    if item is None or item.status != "published" or not item.published_payload:
        return _error_response(
            request,
            404,
            "NOT_FOUND",
            f"Published site settings not found for locale '{locale}'.",
        )
    payload = dict(item.published_payload)
    # Revocation and locale changes apply even to old settings snapshots. Use
    # current public eligibility, not draft settings or a stored descriptor.
    references = payload.get("featuredRecords")
    public_references = []
    # Home renders three selected projects plus three selected publications
    # from one shared list; keep the public bound identical to the admin
    # update bound below.
    for reference in references[:6] if isinstance(references, list) else []:
        if not isinstance(reference, dict):
            continue
        family, record_id = reference.get("family"), reference.get("id")
        model = RESOLVER_FAMILIES.get(family) if isinstance(family, str) else None
        if (
            model is None or not isinstance(record_id, str)
            or not _ID_RE.fullmatch(record_id) or int(record_id) > MAX_ID
        ):
            continue
        if model.objects.public().filter(pk=int(record_id), locale=locale).exists():
            public_references.append({"family": family, "id": record_id})
    payload["featuredRecords"] = public_references
    media_id = payload.pop("brandMediaId", None)
    media = (
        Media.objects.filter(pk=media_id, is_active=True).exclude(file="").first()
        if type(media_id) is int and 0 < media_id <= MAX_ID else None
    )
    payload["brandMedia"] = public_media_ref(media, request, locale=locale)
    return payload


class JourneyMilestoneOut(Schema):
    """One timeline milestone projected from a published profile's entries."""

    kind: str
    title: str = ""
    subtitle: str = ""
    period: str = ""


class ProfileJourneyOut(Schema):
    """Published career timeline for a locale (fail-closed, no fallback)."""

    locale: str
    milestones: list[JourneyMilestoneOut] = Field(default_factory=list)


@api.get(
    "/v1/site/{locale}/journey",
    response={200: ProfileJourneyOut, 404: ErrorEnvelopeOut},
    summary="Published career timeline for a locale (fail-closed, no fallback).",
)
def get_profile_journey(request, locale: str):
    """Project the published about profile's experience/education entries.

    Only the live-published ``about`` profile exposes its child entries;
    entry rows carry no independent publication state, so snapshot fallback
    is deliberately not applied here (it could leak draft child rows).
    Family order is explicit ``ordering``; experience precedes education.
    Degree and field are comma-joined from real record fields.
    """
    if locale not in ("fa", "en"):
        return _error_response(
            request, 404, "NOT_FOUND", f"Locale '{locale}' not supported."
        )
    profile = (
        Profile.objects.public()
        .filter(locale=locale, slug="about")
        .prefetch_related("experience_entries", "education_entries")
        .first()
    )
    if profile is None:
        return _error_response(
            request, 404, "NOT_FOUND", f"Published journey not found for locale '{locale}'."
        )
    milestones = [
        {
            "kind": "experience",
            "title": entry.role,
            "subtitle": entry.organization,
            "period": entry.period,
        }
        for entry in profile.experience_entries.all()
    ]
    milestones.extend(
        {
            "kind": "education",
            "title": f"{entry.degree}, {entry.field}" if entry.field else entry.degree,
            "subtitle": entry.institution,
            "period": entry.period,
        }
        for entry in profile.education_entries.all()
    )
    return {"locale": locale, "milestones": milestones}


@api.get(
    "/landings/{locale}",
    response=list[LandingOut],
    summary="List published landing pages for a locale",
)
def list_landings(request, locale: str) -> list[Landing]:
    items = list(published_for_locale(Landing.objects, locale))
    items.extend(
        published_list_extras(Landing, "landing", locale, {o.pk for o in items})
    )
    return order_like(items, "slug")


@api.get(
    "/landings/{locale}/{slug}",
    response=LandingOut,
    summary="Get one published landing page by slug",
)
def get_landing(request, locale: str, slug: str) -> Landing:
    # A04: fall back to the publication snapshot while the row is a draft.
    landing = resolve_published_detail(Landing, "landing", locale, slug)
    if landing is None:
        raise HttpError(404, "landing not found")
    return landing


@api.get(
    "/profiles/{locale}",
    response=list[ProfileOut],
    summary="List published profile pages for a locale",
)
def list_profiles(request, locale: str) -> list[Profile]:
    items = list(published_for_locale(Profile.objects, locale))
    items.extend(
        published_list_extras(Profile, "profile", locale, {o.pk for o in items})
    )
    return order_like(items, "slug")


@api.get(
    "/profiles/{locale}/{slug}",
    response=ProfileOut,
    summary="Get one published profile page by slug",
)
def get_profile(request, locale: str, slug: str) -> Profile:
    # A04: fall back to the publication snapshot while the row is a draft.
    profile = resolve_published_detail(Profile, "profile", locale, slug)
    if profile is None:
        raise HttpError(404, "profile not found")
    return profile


@api.get(
    "/articles/{locale}",
    response=list[ArticleListOut],
    summary="List published articles for a locale (paginated)",
)
@paginate(PageNumberPagination, page_size=10)
def list_articles(
    request,
    locale: str,
    tag: str | None = None,
    series: str | None = None,
):
    qs = (
        Article.objects.public()
        .filter(locale=locale)
        .select_related("featured_image")
        .prefetch_related("topic_tags", "series")
        .order_by("-published_at", "slug")
    )
    if tag:
        qs = qs.filter(topic_tags__slug=tag, topic_tags__locale=locale)
    if series:
        published_series = Series.objects.public().filter(locale=locale, slug=series)
        qs = qs.filter(series__in=published_series)
    items = list(qs.distinct())
    # A04: still-published (snapshot-backed) records stay listed.
    extras = published_list_extras(Article, "article", locale, {a.pk for a in items})
    if tag:
        extras = [
            e for e in extras if e.topic_tags.filter(locale=locale, slug=tag).exists()
        ]
    if series:
        series_ids = set(
            Series.objects.public()
            .filter(locale=locale, slug=series)
            .values_list("pk", flat=True)
        )
        extras = [
            e
            for e in extras
            if set(e.series.values_list("pk", flat=True)) & series_ids
        ]
    return order_like(items + extras, "-published_at", "slug")


@api.get(
    "/articles/{locale}/{slug}",
    response=ArticleDetailOut,
    summary="Get one published article by slug",
)
def get_article(request, locale: str, slug: str) -> Article:
    # A04: fall back to the publication snapshot while the row is a draft.
    article = resolve_published_detail(
        Article,
        "article",
        locale,
        slug,
        base_qs=Article.objects.public()
        .select_related("story", "featured_image")
        .prefetch_related("topic_tags", "series", "story__sections__blocks"),
    )
    if article is None:
        raise HttpError(404, "article not found")
    return article


@api.get(
    "/series/{locale}",
    response=list[SeriesOut],
    summary="List published series for a locale",
)
def list_series(request, locale: str) -> list[Series]:
    # A04: still-published (snapshot-backed) records stay listed.
    items = list(
        Series.objects.public().filter(locale=locale).order_by("ordering", "slug")
    )
    items.extend(
        published_list_extras(Series, "series", locale, {o.pk for o in items})
    )
    return order_like(items, "ordering", "slug")


@api.get(
    "/tags/{locale}",
    response=list[TopicTagOut],
    summary="List topic tags for a locale",
)
def list_tags(request, locale: str) -> list[TopicTag]:
    return list(TopicTag.objects.filter(locale=locale).order_by("name"))


@api.get(
    "/article-redirects/{locale}",
    response=list[ArticleSlugRedirectOut],
    summary="List article slug redirects for a locale",
)
def list_article_redirects(request, locale: str) -> list[ArticleSlugRedirect]:
    """Only expose redirects whose target slug is currently public."""
    public_slugs = set(
        Article.objects.public().filter(locale=locale).values_list("slug", flat=True)
    )
    return [
        row
        for row in ArticleSlugRedirect.objects.filter(locale=locale).order_by("old_slug")
        if row.new_slug in public_slugs
    ]


class RelatedSlugOut(Schema):
    """Minimal related entity pointer for list/tree navigation."""

    slug: str
    title: str


class EvidenceOut(Schema):
    """Public evidence row (source required; restricted/internal omitted upstream)."""

    label: str
    value: str
    source: str
    last_verified: date | None


class CollaboratorOut(Schema):
    """Approved collaborator credit only."""

    name: str
    role: str


class FundingOut(Schema):
    """Approved funding disclosure only."""

    funder: str
    grant_id: str


class CaseStudyOut(Schema):
    """Public case study fields when extension exists."""

    depth: str
    problem: str
    constraints: str
    technical_decisions: str
    trade_offs: str
    outcomes_summary: str
    lessons_learned: str
    testing_summary: str

    @staticmethod
    def resolve_technical_decisions(obj) -> str:
        return sanitize_public_richtext(str(obj.technical_decisions or ""))


class DiagramOut(Schema):
    """Public diagram metadata with optional active Media URL."""

    title: str
    version: str
    diagram_date: date
    alt_text: str
    long_description: str
    image: PublicMediaOut | None = None


class ScreenshotOut(Schema):
    """Public screenshot metadata with optional active Media URL."""

    caption: str
    alt_text: str
    external_url: str = ""
    image: PublicMediaOut | None = None


class ResearchTopicListOut(Schema):
    """Public research topic list card."""

    locale: str
    slug: str
    title: str
    summary: str
    published_at: datetime | None
    updated_at: datetime | None


class ResearchTopicDetailOut(ResearchTopicListOut, PublicPublicationMetadataMixinOut):
    """Public research topic detail with related public projects/publications."""

    motivation: str
    problems: str
    research_questions: str
    methods: str
    future_directions: str
    story: StoryDocumentOut | None = None
    projects: list[RelatedSlugOut] = Field(default_factory=list)
    publications: list[RelatedSlugOut] = Field(default_factory=list)

    @staticmethod
    def resolve_story(obj: ResearchTopic) -> dict | None:
        return public_story_document(getattr(obj, "story", None), obj.locale)

    @staticmethod
    def _published_topic_projects(obj: ResearchTopic) -> list:
        # A04: still-published (snapshot-backed) projects stay linked.
        live = list(obj.projects.public().filter(locale=obj.locale))
        seen = {p.pk for p in live}
        extras = []
        for rel in obj.projects.filter(locale=obj.locale).only("pk"):
            if rel.pk in seen:
                continue
            target = resolve_published_target(Project, "project", rel.pk, obj.locale)
            if target is not None:
                extras.append(target)
                seen.add(rel.pk)
        return order_like(live + extras, "slug")

    @staticmethod
    def resolve_projects(obj: ResearchTopic) -> list[RelatedSlugOut]:
        return [
            RelatedSlugOut(slug=p.slug, title=p.title)
            for p in ResearchTopicDetailOut._published_topic_projects(obj)
        ]

    @staticmethod
    def resolve_publications(obj: ResearchTopic) -> list[RelatedSlugOut]:
        projects = ResearchTopicDetailOut._published_topic_projects(obj)
        pubs = (
            Publication.objects.public()
            .filter(
                projects__in=[p.pk for p in projects] or [0],
                locale=obj.locale,
            )
            .distinct()
            .order_by("slug")
        )
        return [RelatedSlugOut(slug=p.slug, title=p.title) for p in pubs]


class ResearchStatementOut(PublicPublicationMetadataMixinOut):
    """Public research statement (sanitized rich text + optional active PDF)."""

    locale: str
    slug: str
    title: str
    body: str
    statement_pdf: PublicMediaOut | None = None
    story: StoryDocumentOut | None = None
    published_at: datetime | None
    updated_at: datetime | None

    @staticmethod
    def resolve_body(obj: ResearchStatement) -> str:
        return sanitize_public_richtext(str(obj.body or ""))

    @staticmethod
    def resolve_statement_pdf(obj: ResearchStatement, context) -> dict | None:
        request = context.get("request") if context else None
        return public_media_ref(
            getattr(obj, "statement_pdf", None),
            request,
            locale=obj.locale,
        )

    @staticmethod
    def resolve_story(obj: ResearchStatement) -> dict | None:
        return public_story_document(getattr(obj, "story", None), obj.locale)


class ProjectListOut(Schema):
    """Public project list card with explicit availability/license."""

    locale: str
    slug: str
    title: str
    project_type: str
    objective: str
    license: str
    code_availability: str
    data_availability: str
    demo_availability: str
    published_at: datetime | None
    updated_at: datetime | None
    has_case_study: bool = False
    case_study_depth: str | None = None

    @staticmethod
    def resolve_has_case_study(obj: Project) -> bool:
        return obj.has_case_study

    @staticmethod
    def resolve_case_study_depth(obj: Project) -> str | None:
        try:
            return obj.case_study.depth
        except Exception:
            return None


class ProjectDetailOut(ProjectListOut, PublicPublicationMetadataMixinOut):
    """Public project detail with redacted evidence/collaborators/funding/URLs."""

    methods_summary: str
    role: str
    start_date: date | None
    end_date: date | None
    code_url: str
    data_url: str
    demo_url: str
    story: StoryDocumentOut | None = None
    topics: list[RelatedSlugOut] = Field(default_factory=list)
    publications: list[RelatedSlugOut] = Field(default_factory=list)
    evidence: list[EvidenceOut] = Field(default_factory=list)
    collaborators: list[CollaboratorOut] = Field(default_factory=list)
    funding: list[FundingOut] = Field(default_factory=list)
    case_study: CaseStudyOut | None = None
    diagrams: list[DiagramOut] = Field(default_factory=list)
    screenshots: list[ScreenshotOut] = Field(default_factory=list)

    @staticmethod
    def resolve_story(obj: Project) -> dict | None:
        return public_story_document(getattr(obj, "story", None), obj.locale)

    @staticmethod
    def resolve_code_url(obj: Project) -> str:
        return obj.public_code_url()

    @staticmethod
    def resolve_data_url(obj: Project) -> str:
        return obj.public_data_url()

    @staticmethod
    def resolve_demo_url(obj: Project) -> str:
        return obj.public_demo_url()

    @staticmethod
    def resolve_topics(obj: Project) -> list[RelatedSlugOut]:
        # A04: still-published (snapshot-backed) topics stay linked.
        live = list(obj.topics.public().filter(locale=obj.locale))
        seen = {t.pk for t in live}
        extras = []
        for rel in obj.topics.filter(locale=obj.locale).only("pk"):
            if rel.pk in seen:
                continue
            target = resolve_published_target(
                ResearchTopic, "research-topic", rel.pk, obj.locale
            )
            if target is not None:
                extras.append(target)
                seen.add(rel.pk)
        return [
            RelatedSlugOut(slug=t.slug, title=t.title)
            for t in order_like(live + extras, "slug")
        ]

    @staticmethod
    def resolve_publications(obj: Project) -> list[RelatedSlugOut]:
        # A04: still-published (snapshot-backed) publications stay linked.
        live = list(obj.publications.public().filter(locale=obj.locale))
        seen = {p.pk for p in live}
        extras = []
        for rel in obj.publications.filter(locale=obj.locale).only("pk"):
            if rel.pk in seen:
                continue
            target = resolve_published_target(
                Publication, "publication", rel.pk, obj.locale
            )
            if target is not None:
                extras.append(target)
                seen.add(rel.pk)
        return [
            RelatedSlugOut(slug=p.slug, title=p.title)
            for p in order_like(live + extras, "slug")
        ]

    @staticmethod
    def resolve_evidence(obj: Project) -> list[EvidenceOut]:
        # A04: when materialized from a publication snapshot, serve frozen
        # approved relations instead of live draft managers.
        pcs = getattr(obj, "_published_project_case_study", None)
        if isinstance(pcs, dict):
            rows = pcs.get("evidence") or []
            result: list[EvidenceOut] = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                if str(row.get("visibility", "") or "") != "public":
                    continue
                if not str(row.get("source", "") or "").strip():
                    continue
                raw_verified = row.get("last_verified", row.get("lastVerified", None))
                parsed = None
                if isinstance(raw_verified, str) and raw_verified.strip():
                    try:
                        parsed = date.fromisoformat(raw_verified.strip()[:10])
                    except ValueError:
                        parsed = None
                elif isinstance(raw_verified, date) and not isinstance(
                    raw_verified, datetime
                ):
                    parsed = raw_verified
                result.append(
                    EvidenceOut(
                        label=str(row.get("label", "") or ""),
                        value=str(row.get("value", "") or ""),
                        source=str(row.get("source", "") or ""),
                        last_verified=parsed,
                    )
                )
            return result
        return [
            EvidenceOut(
                label=row.label,
                value=row.value,
                source=row.source,
                last_verified=row.last_verified,
            )
            for row in obj.evidence_items.all()
            if row.is_publicly_projectable()
        ]

    @staticmethod
    def resolve_collaborators(obj: Project) -> list[CollaboratorOut]:
        pcs = getattr(obj, "_published_project_case_study", None)
        if isinstance(pcs, dict):
            rows = pcs.get("collaborators") or []
            return [
                CollaboratorOut(
                    name=str(row.get("name", "") or ""),
                    role=str(row.get("role", "") or ""),
                )
                for row in rows
                if isinstance(row, dict)
                and bool(
                    row.get("publication_approved", row.get("publicationApproved", False))
                )
                and str(row.get("name", "") or "").strip()
            ]
        return [
            CollaboratorOut(name=row.name, role=row.role)
            for row in obj.collaborators.filter(publication_approved=True)
        ]

    @staticmethod
    def resolve_funding(obj: Project) -> list[FundingOut]:
        pcs = getattr(obj, "_published_project_case_study", None)
        if isinstance(pcs, dict):
            rows = pcs.get("funding") or []
            return [
                FundingOut(
                    funder=str(row.get("funder", "") or ""),
                    grant_id=str(
                        row.get("grant_id", row.get("grantId", "") or "") or ""
                    ),
                )
                for row in rows
                if isinstance(row, dict)
                and bool(
                    row.get("publication_approved", row.get("publicationApproved", False))
                )
                and str(row.get("funder", "") or "").strip()
            ]
        return [
            FundingOut(funder=row.funder, grant_id=row.grant_id)
            for row in obj.funding_items.filter(publication_approved=True)
        ]

    @staticmethod
    def resolve_case_study(obj: Project):
        pcs = getattr(obj, "_published_project_case_study", None)
        if isinstance(pcs, dict):
            details = pcs.get("details")
            if not isinstance(details, dict):
                return None
            from types import SimpleNamespace

            def _pick(snake: str, camel: str) -> str:
                value = details.get(snake, None)
                if value is None:
                    value = details.get(camel, None)
                return str(value) if value is not None else ""

            return SimpleNamespace(
                depth=str(details.get("depth", "") or "standard"),
                problem=_pick("problem", "problem"),
                constraints=_pick("constraints", "constraints"),
                technical_decisions=_pick("technical_decisions", "technicalDecisions"),
                trade_offs=_pick("trade_offs", "tradeOffs"),
                outcomes_summary=_pick("outcomes_summary", "outcomesSummary"),
                lessons_learned=_pick("lessons_learned", "lessonsLearned"),
                testing_summary=_pick("testing_summary", "testingSummary"),
            )
        try:
            return obj.case_study
        except Exception:
            return None

    @staticmethod
    def resolve_diagrams(obj: Project, context) -> list[DiagramOut]:
        request = context.get("request") if context else None
        return [
            DiagramOut(
                title=row.title,
                version=row.version,
                diagram_date=row.diagram_date,
                alt_text=row.alt_text,
                long_description=row.long_description,
                image=public_media_ref(
                    getattr(row, "diagram_image", None),
                    request,
                    locale=obj.locale,
                ),
            )
            for row in obj.diagrams.all()
            if row.is_publicly_projectable()
        ]

    @staticmethod
    def resolve_screenshots(obj: Project, context) -> list[ScreenshotOut]:
        request = context.get("request") if context else None
        return [
            ScreenshotOut(
                caption=row.caption,
                alt_text=row.alt_text,
                external_url=row.public_external_url(),
                image=public_media_ref(
                    getattr(row, "screenshot_image", None),
                    request,
                    locale=obj.locale,
                ),
            )
            for row in obj.screenshots.all()
            if row.is_publicly_projectable()
        ]


def _project_detail_queryset():
    return (
        Project.objects.public()
        .select_related("story")
        .prefetch_related(
            "topics",
            "publications",
            "evidence_items",
            "collaborators",
            "funding_items",
            "diagrams__diagram_image",
            "screenshots__screenshot_image",
            "case_study",
            "story__sections__blocks",
        )
    )


def _get_public_project(locale: str, slug: str) -> Project:
    # A04: fall back to the publication snapshot while the row is a draft.
    project = resolve_published_detail(
        Project, "project", locale, slug, base_qs=_project_detail_queryset()
    )
    if project is None:
        raise HttpError(404, "project not found")
    return project


class PublicationListOut(Schema):
    """Public publication list card (P5 core + P8 type/stage)."""

    locale: str
    slug: str
    title: str
    authors: str
    venue: str
    date: date | None
    doi: str
    license: str
    publication_type: str
    academic_stage: str
    access_state: str
    published_at: datetime | None
    updated_at: datetime | None


class PublicationDetailOut(PublicationListOut, PublicPublicationMetadataMixinOut):
    """Public publication detail with citation and access gates."""

    url: str
    pdf_url: str
    abstract: str
    isbn: str
    preprint_url: str
    code_url: str
    dataset_url: str
    accessibility_notes: str
    citation_count: int | None
    citation_text: str | None
    pdf: dict | None = None
    story: StoryDocumentOut | None = None

    @staticmethod
    def resolve_story(obj: Publication) -> dict | None:
        return public_story_document(getattr(obj, "story", None), obj.locale)

    @staticmethod
    def resolve_url(obj: Publication) -> str:
        return obj.public_external_url()

    @staticmethod
    def resolve_pdf_url(obj: Publication) -> str:
        return obj.public_pdf_url()

    @staticmethod
    def resolve_citation_count(obj: Publication) -> int | None:
        return obj.public_citation_count()

    @staticmethod
    def resolve_citation_text(obj: Publication) -> str | None:
        return obj.public_citation_text()

    @staticmethod
    def resolve_pdf(obj: Publication, context) -> dict | None:
        if not obj.allows_public_file():
            return None
        request = context.get("request") if context else None
        return public_media_ref(obj.pdf_media, request, locale=obj.locale)


class BookListOut(Schema):
    locale: str
    slug: str
    title: str
    authors: str
    isbn: str
    publisher: str
    publication_date: date | None
    license: str
    access_state: str
    published_at: datetime | None
    updated_at: datetime | None


class BookDetailOut(BookListOut, PublicPublicationMetadataMixinOut):
    description: str
    url: str
    accessibility_notes: str
    cover: dict | None = None
    story: StoryDocumentOut | None = None

    @staticmethod
    def resolve_story(obj: Book) -> dict | None:
        return public_story_document(getattr(obj, "story", None), obj.locale)

    @staticmethod
    def resolve_cover(obj: Book, context) -> dict | None:
        if not obj.allows_public_file():
            return None
        request = context.get("request") if context else None
        return public_media_ref(obj.cover_media, request, locale=obj.locale)


class TalkListOut(Schema):
    locale: str
    slug: str
    title: str
    speakers: str
    event_name: str
    event_date: date | None
    location: str
    license: str
    access_state: str
    published_at: datetime | None
    updated_at: datetime | None


class TalkDetailOut(TalkListOut, PublicPublicationMetadataMixinOut):
    abstract: str
    video_url: str
    slides_url: str
    accessibility_notes: str
    slides: dict | None = None
    story: StoryDocumentOut | None = None

    @staticmethod
    def resolve_story(obj: Talk) -> dict | None:
        return public_story_document(getattr(obj, "story", None), obj.locale)

    @staticmethod
    def resolve_video_url(obj: Talk) -> str:
        return obj.public_video_url()

    @staticmethod
    def resolve_slides_url(obj: Talk) -> str:
        return obj.public_slides_url()

    @staticmethod
    def resolve_slides(obj: Talk, context) -> dict | None:
        if not obj.allows_public_file():
            return None
        request = context.get("request") if context else None
        return public_media_ref(obj.slides_media, request, locale=obj.locale)


class DownloadListOut(Schema):
    locale: str
    slug: str
    title: str
    description: str
    download_type: str
    language: str
    license: str
    access_state: str
    published_at: datetime | None
    updated_at: datetime | None


class DownloadDetailOut(DownloadListOut, PublicPublicationMetadataMixinOut):
    accessibility_notes: str
    file: dict | None = None
    mime: str | None = None
    size_bytes: int | None = None
    story: StoryDocumentOut | None = None

    @staticmethod
    def resolve_story(obj: Download) -> dict | None:
        return public_story_document(getattr(obj, "story", None), obj.locale)

    @staticmethod
    def resolve_file(obj: Download, context) -> dict | None:
        if not obj.public_media_is_downloadable():
            return None
        request = context.get("request") if context else None
        return public_media_ref(obj.media, request, locale=obj.locale)

    @staticmethod
    def resolve_mime(obj: Download) -> str | None:
        if not obj.public_media_is_downloadable():
            return None
        return obj.media.mime or None

    @staticmethod
    def resolve_size_bytes(obj: Download) -> int | None:
        if not obj.public_media_is_downloadable():
            return None
        return obj.media.size


class CourseListOut(Schema):
    locale: str
    slug: str
    title: str
    description: str
    level: str
    course_format: str
    course_language: str
    availability: str
    license: str
    last_updated: date | None
    published_at: datetime | None
    updated_at: datetime | None


class CourseDetailOut(CourseListOut, PublicPublicationMetadataMixinOut):
    body: str
    prerequisites: str
    outcomes: str
    accessibility_notes: str
    cover: dict | None = None
    story: StoryDocumentOut | None = None

    @staticmethod
    def resolve_story(obj: Course) -> dict | None:
        return public_story_document(getattr(obj, "story", None), obj.locale)

    @staticmethod
    def resolve_body(obj: Course) -> str:
        return sanitize_public_richtext(str(obj.body or ""))

    @staticmethod
    def resolve_prerequisites(obj: Course) -> str:
        return obj.public_prerequisites_display()

    @staticmethod
    def resolve_cover(obj: Course, context) -> dict | None:
        request = context.get("request") if context else None
        return public_media_ref(getattr(obj, "cover_media", None), request, locale=obj.locale)


class LessonCardOut(Schema):
    """Public lesson card projection for course lesson list (§I05)."""

    locale: str
    slug: str
    title: str
    summary: str
    courseSlug: str
    position: int


class LessonListOut(Schema):
    """Paginated/counted lesson list for a course (§I05)."""

    count: int
    items: list[LessonCardOut]


class LessonDetailOut(Schema):
    """Independent course lesson detail projection with ordered neighbors (§I05)."""

    locale: str
    slug: str
    title: str
    summary: str
    courseSlug: str
    position: int
    story: StoryDocumentOut | None = None
    resources: list[WorkRefOut] = Field(default_factory=list)
    previous: WorkRefOut | None = None
    next: WorkRefOut | None = None
    seo: PublicSeoOut | None = None
    alternates: list[AlternateLocaleOut] = Field(default_factory=list)


class GalleryImageOut(Schema):
    url: str
    alt: str
    caption: str = ""
    mime: str = ""
    title: str = ""
    size: int = 0


class CreativeWorkListOut(Schema):
    locale: str
    slug: str
    title: str
    description: str
    work_type: str
    creator_name: str
    creator_role: str
    creation_date: date | None
    license: str
    access_state: str
    published_at: datetime | None
    updated_at: datetime | None


class CreativeWorkDetailOut(CreativeWorkListOut, PublicPublicationMetadataMixinOut):
    body: str
    rights_statement: str
    accessibility_notes: str
    cover: dict | None = None
    gallery: list[GalleryImageOut] = Field(default_factory=list)
    story: StoryDocumentOut | None = None

    @staticmethod
    def resolve_story(obj: CreativeWork) -> dict | None:
        return public_story_document(getattr(obj, "story", None), obj.locale)

    @staticmethod
    def resolve_body(obj: CreativeWork) -> str:
        return sanitize_public_richtext(str(obj.body or ""))

    @staticmethod
    def resolve_rights_statement(obj: CreativeWork) -> str:
        # No student PII — rights text is editor-curated; empty when not public.
        if obj.access_state != AccessState.PUBLIC and not (obj.rights_statement or "").strip():
            return ""
        return (obj.rights_statement or "").strip()

    @staticmethod
    def resolve_cover(obj: CreativeWork, context) -> dict | None:
        if not obj.allows_public_file():
            return None
        request = context.get("request") if context else None
        return public_media_ref(getattr(obj, "cover_media", None), request, locale=obj.locale)

    @staticmethod
    def resolve_gallery(obj: CreativeWork, context) -> list[GalleryImageOut]:
        if not obj.allows_public_file():
            return []
        request = context.get("request") if context else None
        images = getattr(obj, "gallery_images", None)
        if images is None:
            return []
        result: list[GalleryImageOut] = []
        for row in images.all().select_related("media"):
            if not row.is_publicly_projectable():
                continue
            ref = public_media_ref(row.media, request, locale=obj.locale)
            if ref is None:
                continue
            result.append(
                GalleryImageOut(
                    url=ref.get("url", ""),
                    alt=ref.get("alt", "") or (row.alt_text or "").strip(),
                    caption=(row.caption or "").strip(),
                    mime=ref.get("mime", "") or "",
                    title=ref.get("title", "") or "",
                    size=ref.get("size", 0) or 0,
                )
            )
        return result


@api.get(
    "/research/topics/{locale}",
    response=list[ResearchTopicListOut],
    summary="List published research topics for a locale (paginated)",
)
@paginate(PageNumberPagination, page_size=10)
def list_research_topics(request, locale: str):
    # A04: still-published (snapshot-backed) records stay listed.
    items = list(
        ResearchTopic.objects.public().filter(locale=locale).order_by("slug")
    )
    items.extend(
        published_list_extras(
            ResearchTopic, "research-topic", locale, {o.pk for o in items}
        )
    )
    return order_like(items, "slug")


@api.get(
    "/research/topics/{locale}/{slug}",
    response=ResearchTopicDetailOut,
    summary="Get one published research topic by slug",
)
def get_research_topic(request, locale: str, slug: str) -> ResearchTopic:
    # A04: fall back to the publication snapshot while the row is a draft.
    topic = resolve_published_detail(
        ResearchTopic,
        "research-topic",
        locale,
        slug,
        base_qs=ResearchTopic.objects.public()
        .select_related("story")
        .prefetch_related("projects", "story__sections__blocks"),
    )
    if topic is None:
        raise HttpError(404, "research topic not found")
    return topic


@api.get(
    "/research/statements/{locale}",
    response=list[ResearchStatementOut],
    summary="List published research statements for a locale",
)
def list_research_statements(request, locale: str) -> list[ResearchStatement]:
    # A04: still-published (snapshot-backed) records stay listed.
    items = list(
        ResearchStatement.objects.public()
        .filter(locale=locale)
        .select_related("story", "statement_pdf")
        .prefetch_related("story__sections__blocks")
        .order_by("slug")
    )
    items.extend(
        published_list_extras(
            ResearchStatement, "research-statement", locale, {o.pk for o in items}
        )
    )
    return order_like(items, "slug")


@api.get(
    "/research/statements/{locale}/{slug}",
    response=ResearchStatementOut,
    summary="Get one published research statement by slug",
)
def get_research_statement(request, locale: str, slug: str) -> ResearchStatement:
    # A04: fall back to the publication snapshot while the row is a draft.
    statement = resolve_published_detail(
        ResearchStatement,
        "research-statement",
        locale,
        slug,
        base_qs=ResearchStatement.objects.public()
        .select_related("story", "statement_pdf")
        .prefetch_related("story__sections__blocks"),
    )
    if statement is None:
        raise HttpError(404, "research statement not found")
    return statement


@api.get(
    "/projects/{locale}",
    response=list[ProjectListOut],
    summary="List published projects shown on /projects/ (paginated)",
)
@paginate(PageNumberPagination, page_size=10)
def list_projects(request, locale: str, has_case_study: bool = False):
    # A04: still-published (snapshot-backed) records stay listed.
    qs = (
        Project.objects.public()
        .filter(locale=locale, show_on_projects=True)
        .order_by("-published_at", "slug")
    )
    if has_case_study:
        qs = qs.filter(case_study__isnull=False).select_related("case_study")
    items = list(qs)
    for extra in published_list_extras(Project, "project", locale, {o.pk for o in items}):
        if not getattr(extra, "show_on_projects", False):
            continue
        if has_case_study:
            try:
                has_cs = extra.case_study is not None
            except Project.case_study.RelatedObjectDoesNotExist:
                has_cs = False
            if not has_cs:
                continue
        items.append(extra)
    return order_like(items, "-published_at", "slug")


@api.get(
    "/projects/{locale}/{slug}",
    response=ProjectDetailOut,
    summary="Get one published project listed on /projects/ by slug",
)
def get_project(request, locale: str, slug: str) -> Project:
    project = _get_public_project(locale, slug)
    if not project.show_on_projects:
        raise HttpError(404, "project not found")
    return project


@api.get(
    "/research/projects/{locale}",
    response=list[ProjectListOut],
    summary="List published projects for a locale (paginated)",
)
@paginate(PageNumberPagination, page_size=10)
def list_research_projects(request, locale: str):
    # A04: still-published (snapshot-backed) records stay listed.
    items = list(
        Project.objects.public()
        .filter(locale=locale)
        .select_related("case_study")
        .order_by("-published_at", "slug")
    )
    items.extend(
        published_list_extras(Project, "project", locale, {o.pk for o in items})
    )
    return order_like(items, "-published_at", "slug")


@api.get(
    "/research/projects/{locale}/{slug}",
    response=ProjectDetailOut,
    summary="Get one published project by slug",
)
def get_research_project(request, locale: str, slug: str) -> Project:
    return _get_public_project(locale, slug)


@api.get(
    "/research/publications/{locale}",
    response=list[PublicationListOut],
    summary="List published publications for a locale (paginated)",
)
@paginate(PageNumberPagination, page_size=10)
def list_research_publications(request, locale: str):
    # A04: still-published (snapshot-backed) records stay listed.
    items = list(
        Publication.objects.public().filter(locale=locale).order_by("-date", "slug")
    )
    items.extend(
        published_list_extras(Publication, "publication", locale, {o.pk for o in items})
    )
    return order_like(items, "-date", "slug")


@api.get(
    "/research/publications/{locale}/{slug}",
    response=PublicationDetailOut,
    summary="Get one published publication by slug",
)
def get_research_publication(request, locale: str, slug: str) -> Publication:
    # A04: fall back to the publication snapshot while the row is a draft.
    publication = resolve_published_detail(
        Publication,
        "publication",
        locale,
        slug,
        base_qs=Publication.objects.public().select_related("pdf_media"),
    )
    if publication is None:
        raise HttpError(404, "publication not found")
    return publication


@api.get(
    "/publications/{locale}",
    response=list[PublicationListOut],
    summary="List published publications for a locale (paginated)",
)
@paginate(PageNumberPagination, page_size=10)
def list_publications(request, locale: str):
    # A04: still-published (snapshot-backed) records stay listed.
    items = list(
        Publication.objects.public().filter(locale=locale).order_by("-date", "slug")
    )
    items.extend(
        published_list_extras(Publication, "publication", locale, {o.pk for o in items})
    )
    return order_like(items, "-date", "slug")


@api.get(
    "/publications/{locale}/{slug}",
    response=PublicationDetailOut,
    summary="Get one published publication by slug (canonical)",
)
def get_publication(request, locale: str, slug: str) -> Publication:
    return get_research_publication(request, locale, slug)


@api.get(
    "/books/{locale}",
    response=list[BookListOut],
    summary="List published books for a locale (paginated)",
)
@paginate(PageNumberPagination, page_size=10)
def list_books(request, locale: str):
    # A04: still-published (snapshot-backed) records stay listed.
    items = list(
        Book.objects.public()
        .filter(locale=locale)
        .order_by("-publication_date", "slug")
    )
    items.extend(published_list_extras(Book, "book", locale, {o.pk for o in items}))
    return order_like(items, "-publication_date", "slug")


@api.get(
    "/books/{locale}/{slug}",
    response=BookDetailOut,
    summary="Get one published book by slug",
)
def get_book(request, locale: str, slug: str) -> Book:
    # A04: fall back to the publication snapshot while the row is a draft.
    book = resolve_published_detail(
        Book,
        "book",
        locale,
        slug,
        base_qs=Book.objects.public().select_related(
            "cover_media", "story", "social_image"
        ),
    )
    if book is None:
        raise HttpError(404, "book not found")
    return book


@api.get(
    "/talks/{locale}",
    response=list[TalkListOut],
    summary="List published talks for a locale (paginated)",
)
@paginate(PageNumberPagination, page_size=10)
def list_talks(request, locale: str):
    # A04: still-published (snapshot-backed) records stay listed.
    items = list(
        Talk.objects.public().filter(locale=locale).order_by("-event_date", "slug")
    )
    items.extend(published_list_extras(Talk, "talk", locale, {o.pk for o in items}))
    return order_like(items, "-event_date", "slug")


@api.get(
    "/talks/{locale}/{slug}",
    response=TalkDetailOut,
    summary="Get one published talk by slug",
)
def get_talk(request, locale: str, slug: str) -> Talk:
    # A04: fall back to the publication snapshot while the row is a draft.
    talk = resolve_published_detail(
        Talk,
        "talk",
        locale,
        slug,
        base_qs=Talk.objects.public().select_related(
            "slides_media", "story", "social_image"
        ),
    )
    if talk is None:
        raise HttpError(404, "talk not found")
    return talk


@api.get(
    "/downloads/{locale}",
    response=list[DownloadListOut],
    summary="List published downloads for a locale (paginated)",
)
@paginate(PageNumberPagination, page_size=10)
def list_downloads(request, locale: str):
    # A04: still-published (snapshot-backed) records stay listed.
    items = list(
        Download.objects.public()
        .filter(locale=locale)
        .order_by("-published_at", "slug")
    )
    items.extend(
        published_list_extras(Download, "download", locale, {o.pk for o in items})
    )
    return order_like(items, "-published_at", "slug")


@api.get(
    "/downloads/{locale}/{slug}",
    response=DownloadDetailOut,
    summary="Get one published download by slug",
)
def get_download(request, locale: str, slug: str) -> Download:
    # A04: fall back to the publication snapshot while the row is a draft.
    download = resolve_published_detail(
        Download,
        "download",
        locale,
        slug,
        base_qs=Download.objects.public().select_related(
            "media", "story", "social_image"
        ),
    )
    if download is None:
        raise HttpError(404, "download not found")
    return download


@api.get(
    "/downloads/{locale}/{slug}/file",
    summary="Stream a published public download file (active media only)",
)
def download_file(request, locale: str, slug: str):
    # A04: the published file stays downloadable while the row is a draft.
    download = resolve_published_detail(
        Download,
        "download",
        locale,
        slug,
        base_qs=Download.objects.public().select_related("media"),
    )
    if download is None or not download.public_media_is_downloadable():
        raise HttpError(404, "download not found")
    media = download.media
    filename = PurePosixPath(media.file.name).name or "download"
    response = FileResponse(
        media.file.open("rb"),
        as_attachment=True,
        filename=filename,
        content_type=media.mime or "application/octet-stream",
    )
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response

@api.get(
    "/courses/{locale}",
    response=list[CourseListOut],
    summary="List published courses for a locale (paginated)",
)
@paginate(PageNumberPagination, page_size=10)
def list_courses(request, locale: str):
    # A04: still-published (snapshot-backed) records stay listed.
    items = list(Course.objects.public().filter(locale=locale).order_by("slug"))
    items.extend(
        published_list_extras(Course, "course", locale, {o.pk for o in items})
    )
    return order_like(items, "slug")


@api.get(
    "/courses/{locale}/{slug}",
    response=CourseDetailOut,
    summary="Get one published course by slug",
)
def get_course(request, locale: str, slug: str) -> Course:
    # A04: fall back to the publication snapshot while the row is a draft.
    course = resolve_published_detail(
        Course,
        "course",
        locale,
        slug,
        base_qs=Course.objects.public().select_related("cover_media"),
    )
    if course is None:
        raise HttpError(404, "course not found")
    return course


# Alias for IA canonical /{locale}/teaching/ — same queryset as /courses/.
@api.get(
    "/teaching/{locale}",
    response=list[CourseListOut],
    summary="List published teaching courses for a locale (alias of /courses/)",
)
@paginate(PageNumberPagination, page_size=10)
def list_teaching(request, locale: str):
    # A04: still-published (snapshot-backed) records stay listed.
    items = list(Course.objects.public().filter(locale=locale).order_by("slug"))
    items.extend(
        published_list_extras(Course, "course", locale, {o.pk for o in items})
    )
    return order_like(items, "slug")


@api.get(
    "/teaching/{locale}/{slug}",
    response=CourseDetailOut,
    summary="Get one published teaching course by slug (alias)",
)
def get_teaching_course(request, locale: str, slug: str) -> Course:
    return get_course(request, locale, slug)


@api.get(
    "/v1/lessons/{locale}",
    response=LessonListOut,
    summary="List published course lessons for a locale",
)
def list_lessons(request, locale: str, course: str | None = None) -> LessonListOut:
    """List published lessons for a course ordered by position, id.

    Guard: Both parent course and lesson must be published in the requested locale.
    If parent course is not published or does not exist, returns count 0.
    """
    if not course:
        return LessonListOut(count=0, items=[])

    # A04: the published parent keeps serving while it is a draft.
    parent_course = resolve_published_detail(Course, "course", locale, course)
    if parent_course is None:
        return LessonListOut(count=0, items=[])

    lessons = list(
        Lesson.objects.public()
        .filter(course_id=parent_course.pk, locale=locale)
        .order_by("position", "id")
    )
    # A04: still-published lessons stay listed under a published parent.
    for extra in published_list_extras(
        Lesson, "lesson", locale, {item.pk for item in lessons}
    ):
        if getattr(extra, "course_id", None) != parent_course.pk:
            continue
        lessons.append(extra)
    lessons = order_like(lessons, "position", "id")
    items = [
        LessonCardOut(
            locale=item.locale,
            slug=item.slug,
            title=item.title,
            summary=item.summary or "",
            courseSlug=parent_course.slug,
            position=item.position,
        )
        for item in lessons
    ]
    return LessonListOut(count=len(items), items=items)


@api.get(
    "/v1/lessons/{locale}/{courseSlug}/{lessonSlug}",
    response=LessonDetailOut,
    summary="Get one published course lesson with ordered neighbors",
)
def get_lesson_detail(
    request, locale: str, courseSlug: str, lessonSlug: str
) -> LessonDetailOut:
    """Get published lesson detail with previous/next neighbors.

    Guards:
    - Parent course must exist and be published in locale (or 404).
    - Lesson must exist and be published in locale and belong to course (or 404).
    - Draft lessons are excluded from neighbors.
    """
    parent_course = resolve_published_detail(Course, "course", locale, courseSlug)
    if parent_course is None:
        raise HttpError(404, "Course not found")

    # A04: the published lesson keeps serving while it is a draft.
    lesson = resolve_published_detail(
        Lesson,
        "lesson",
        locale,
        lessonSlug,
        base_qs=Lesson.objects.public()
        .filter(course_id=parent_course.pk)
        .select_related("course", "story", "social_image"),
    )
    if lesson is None or getattr(lesson, "course_id", None) != parent_course.pk:
        raise HttpError(404, "Lesson not found")

    published_siblings = list(
        Lesson.objects.public()
        .filter(course_id=parent_course.pk, locale=locale)
        .order_by("position", "id")
    )
    for extra in published_list_extras(
        Lesson, "lesson", locale, {sib.pk for sib in published_siblings}
    ):
        if getattr(extra, "course_id", None) != parent_course.pk:
            continue
        published_siblings.append(extra)
    published_siblings = order_like(published_siblings, "position", "id")
    current_idx = -1
    for idx, sib in enumerate(published_siblings):
        if sib.pk == lesson.pk:
            current_idx = idx
            break

    prev_ref = None
    next_ref = None
    if current_idx > 0:
        prev_item = published_siblings[current_idx - 1]
        prev_ref = WorkRefOut(
            family="lesson",
            id=str(prev_item.pk),
            locale=prev_item.locale,
            slug=prev_item.slug,
            title=prev_item.title,
            summary=(prev_item.summary or prev_item.title or "").strip(),
            routeFamily="education",
            courseSlug=parent_course.slug,
        )
    if current_idx >= 0 and current_idx < len(published_siblings) - 1:
        next_item = published_siblings[current_idx + 1]
        next_ref = WorkRefOut(
            family="lesson",
            id=str(next_item.pk),
            locale=next_item.locale,
            slug=next_item.slug,
            title=next_item.title,
            summary=(next_item.summary or next_item.title or "").strip(),
            routeFamily="education",
            courseSlug=parent_course.slug,
        )

    story_doc = public_story_document(getattr(lesson, "story", None), lesson.locale)
    resources = _resolve_public_related_records(lesson)
    seo = _resolve_public_seo(lesson, context={"request": request})
    alternates = _resolve_public_alternates(lesson)

    return LessonDetailOut(
        locale=lesson.locale,
        slug=lesson.slug,
        title=lesson.title,
        summary=lesson.summary or "",
        courseSlug=parent_course.slug,
        position=lesson.position,
        story=story_doc,
        resources=resources,
        previous=prev_ref,
        next=next_ref,
        seo=seo,
        alternates=alternates,
    )


def _resolve_collection_items(collection: Collection) -> list[WorkRefOut]:
    raw_members = getattr(collection, "members", None)
    if not raw_members or not isinstance(raw_members, list):
        return []
    locale = collection.locale
    sorted_members = sorted(
        [m for m in raw_members if isinstance(m, dict)],
        key=lambda m: (m.get("position", 0), str(m.get("id", ""))),
    )
    resolved: list[WorkRefOut] = []
    for m in sorted_members:
        family = m.get("family")
        raw_id = m.get("id")
        if not family or not raw_id:
            continue
        family_str = str(family).lower()
        model = PUBLIC_RESOLVER_FAMILIES.get(family_str)
        if model is None:
            continue
        try:
            pk = int(raw_id)
        except (TypeError, ValueError):
            continue
        public_mgr = getattr(model.objects, "public", None)
        if public_mgr is None:
            continue
        target = resolve_published_target(
            model, _entity_key_for_family(family_str), pk, locale
        )
        if target is None:
            continue
        route_family = PUBLIC_ROUTE_FAMILY_MAP.get(family_str, family_str)
        if family_str == "lesson":
            summary = (getattr(target, "summary", "") or getattr(target, "title", "") or "").strip()
            course_slug = getattr(getattr(target, "course", None), "slug", None)
        elif family_str == "collection":
            summary_raw = getattr(target, "description", "") or getattr(target, "title", "") or ""
            summary = summary_raw.strip()
            course_slug = None
        else:
            summary = _extract_summary(family_str, target)
            course_slug = None
        resolved.append(
            WorkRefOut(
                family=family_str,
                id=str(target.pk),
                locale=target.locale,
                slug=target.slug,
                title=target.title,
                summary=summary,
                routeFamily=route_family,
                courseSlug=course_slug,
            )
        )
    return resolved


class CollectionCardOut(Schema):
    locale: str
    slug: str
    title: str
    description: str
    curatorName: str
    criteria: str
    curatedDate: date | None = None
    curatorTitle: str | None = None
    cover: dict | None = None
    seo: PublicSeoOut | None = None
    alternates: list[AlternateLocaleOut] = Field(default_factory=list)


class CollectionListOut(Schema):
    count: int
    items: list[CollectionCardOut] = Field(default_factory=list)


class CollectionDetailOut(CollectionCardOut):
    story: StoryDocumentOut | None = None
    items: list[WorkRefOut] = Field(default_factory=list)


@api.get(
    "/v1/collections/{locale}",
    response=CollectionListOut,
    summary="List published collections for a locale (paginated)",
)
def list_collections(
    request, locale: str, page: int = 1, pageSize: int = 20
) -> CollectionListOut:
    """List published collections with pagination (1-based, default 20, max 50)."""
    if locale not in Locale.values:
        raise HttpError(404, "collection not found")
    page_num = max(1, page)
    page_size = max(1, min(50, pageSize))
    live = list(
        Collection.objects.public()
        .filter(locale=locale)
        .select_related("cover_media", "social_image")
        .order_by("-published_at", "slug")
    )
    # A04: still-published (snapshot-backed) records stay listed.
    live.extend(
        published_list_extras(Collection, "collection", locale, {c.pk for c in live})
    )
    merged = order_like(live, "-published_at", "slug")
    total = len(merged)
    collections = merged[(page_num - 1) * page_size : page_num * page_size]
    items = [
        CollectionCardOut(
            locale=c.locale,
            slug=c.slug,
            title=c.title,
            description=c.description or "",
            curatorName=(c.curator_name or "").strip(),
            curatorTitle=(c.curator_title or "").strip() or None,
            criteria=(c.criteria or "").strip(),
            curatedDate=c.curated_date,
            cover=public_media_ref(getattr(c, "cover_media", None), request, locale=c.locale),
            seo=_resolve_public_seo(c, context={"request": request}),
            alternates=_resolve_public_alternates(c),
        )
        for c in collections
    ]
    return CollectionListOut(count=total, items=items)


@api.get(
    "/v1/collections/{locale}/{slug}",
    response=CollectionDetailOut,
    summary="Get one published collection by slug with ordered items",
)
def get_collection_detail(request, locale: str, slug: str) -> CollectionDetailOut:
    """Get published collection detail with ordered members and story."""
    if locale not in Locale.values:
        raise HttpError(404, "collection not found")
    # A04: fall back to the publication snapshot while the row is a draft.
    collection = resolve_published_detail(
        Collection,
        "collection",
        locale,
        slug,
        base_qs=Collection.objects.public().select_related(
            "cover_media", "story", "social_image"
        ),
    )
    if collection is None:
        raise HttpError(404, "collection not found")

    story_doc = public_story_document(getattr(collection, "story", None), collection.locale)
    items = _resolve_collection_items(collection)
    seo = _resolve_public_seo(collection, context={"request": request})
    alternates = _resolve_public_alternates(collection)
    cover = public_media_ref(
        getattr(collection, "cover_media", None), request, locale=collection.locale
    )

    return CollectionDetailOut(
        locale=collection.locale,
        slug=collection.slug,
        title=collection.title,
        description=collection.description or "",
        curatorName=(collection.curator_name or "").strip(),
        curatorTitle=(collection.curator_title or "").strip() or None,
        criteria=(collection.criteria or "").strip(),
        curatedDate=collection.curated_date,
        cover=cover,
        story=story_doc,
        items=items,
        seo=seo,
        alternates=alternates,
    )


def _resolve_series_items(series: Series) -> list[WorkRefOut]:
    raw_members = getattr(series, "members", None)
    locale = series.locale
    resolved: list[WorkRefOut] = []

    if raw_members and isinstance(raw_members, list):
        sorted_members = sorted(
            [m for m in raw_members if isinstance(m, dict)],
            key=lambda m: (m.get("position", 0), str(m.get("id", ""))),
        )
        for m in sorted_members:
            family = m.get("family")
            raw_id = m.get("id")
            if not family or not raw_id or str(family).lower() != "article":
                continue
            try:
                pk = int(raw_id)
            except (TypeError, ValueError):
                continue
            art = resolve_published_target(Article, "article", pk, locale)
            if art is None:
                continue
            resolved.append(
                WorkRefOut(
                    family="article",
                    id=str(art.pk),
                    locale=art.locale,
                    slug=art.slug,
                    title=art.title,
                    summary=_extract_summary("article", art),
                    routeFamily=PUBLIC_ROUTE_FAMILY_MAP.get("article", "blog"),
                    courseSlug=None,
                )
            )
    else:
        candidates = list(
            series.articles.filter(locale=locale).order_by("published_at", "id")
        )
        targets = []
        for art in candidates:
            target = resolve_published_target(Article, "article", art.pk, locale)
            if target is None:
                continue
            targets.append(target)
        for art in order_like(targets, "published_at", "id"):
            resolved.append(
                WorkRefOut(
                    family="article",
                    id=str(art.pk),
                    locale=art.locale,
                    slug=art.slug,
                    title=art.title,
                    summary=_extract_summary("article", art),
                    routeFamily=PUBLIC_ROUTE_FAMILY_MAP.get("article", "blog"),
                    courseSlug=None,
                )
            )
    return resolved


class SeriesDetailOut(Schema):
    """Public series detail projection with ordered article items and story."""

    locale: str
    slug: str
    title: str
    description: str = ""
    story: StoryDocumentOut | None = None
    items: list[WorkRefOut] = Field(default_factory=list)
    seo: PublicSeoOut | None = None
    alternates: list[AlternateLocaleOut] = Field(default_factory=list)


@api.get(
    "/v1/series/{locale}/{slug}",
    response=SeriesDetailOut,
    summary="Get one published series by slug with ordered article items",
)
def get_series_detail(request, locale: str, slug: str) -> SeriesDetailOut:
    """Get published series detail with ordered article members and story."""
    if locale not in Locale.values:
        raise HttpError(404, "series not found")
    # A04: fall back to the publication snapshot while the row is a draft.
    series = resolve_published_detail(
        Series,
        "series",
        locale,
        slug,
        base_qs=Series.objects.public().select_related("story", "social_image"),
    )
    if series is None:
        raise HttpError(404, "series not found")

    story_doc = public_story_document(getattr(series, "story", None), series.locale)
    items = _resolve_series_items(series)
    seo = _resolve_public_seo(series, context={"request": request})
    alternates = _resolve_public_alternates(series)

    return SeriesDetailOut(
        locale=series.locale,
        slug=series.slug,
        title=series.title,
        description=series.description or "",
        story=story_doc,
        items=items,
        seo=seo,
        alternates=alternates,
    )


@api.get(
    "/creative-works/{locale}",
    response=list[CreativeWorkListOut],
    summary="List published creative works for a locale (paginated)",
)
@paginate(PageNumberPagination, page_size=10)
def list_creative_works(request, locale: str):
    # A04: still-published (snapshot-backed) records stay listed.
    items = list(
        CreativeWork.objects.public().filter(locale=locale).order_by("slug")
    )
    items.extend(
        published_list_extras(
            CreativeWork, "creative-work", locale, {o.pk for o in items}
        )
    )
    return order_like(items, "slug")


@api.get(
    "/creative-works/{locale}/{slug}",
    response=CreativeWorkDetailOut,
    summary="Get one published creative work by slug",
)
def get_creative_work(request, locale: str, slug: str) -> CreativeWork:
    # A04: fall back to the publication snapshot while the row is a draft.
    work = resolve_published_detail(
        CreativeWork,
        "creative-work",
        locale,
        slug,
        base_qs=CreativeWork.objects.public()
        .select_related("cover_media")
        .prefetch_related("gallery_images__media"),
    )
    if work is None:
        raise HttpError(404, "creative work not found")
    return work


@api.get(
    "/creative/{locale}",
    response=list[CreativeWorkListOut],
    summary="List published creative works for a locale (alias of /creative-works/)",
)
@paginate(PageNumberPagination, page_size=10)
def list_creative(request, locale: str):
    # A04: still-published (snapshot-backed) records stay listed.
    items = list(
        CreativeWork.objects.public().filter(locale=locale).order_by("slug")
    )
    items.extend(
        published_list_extras(
            CreativeWork, "creative-work", locale, {o.pk for o in items}
        )
    )
    return order_like(items, "slug")


@api.get(
    "/creative/{locale}/{slug}",
    response=CreativeWorkDetailOut,
    summary="Get one published creative work by slug (alias)",
)
def get_creative(request, locale: str, slug: str) -> CreativeWork:
    return get_creative_work(request, locale, slug)


class HomeModuleOut(Schema):
    """One visible home composition slot: key + order only (BK-01)."""

    key: str
    order: int


class HomeCompositionOut(Schema):
    """Public home composition (published+visible rows only, fail-closed)."""

    revision: str
    modules: list[HomeModuleOut] = Field(default_factory=list)


@api.get(
    "/home-composition/{locale}",
    response=HomeCompositionOut,
    summary="Home composition for a locale (published+visible rows, ordered)",
)
def get_home_composition(request, locale: str) -> HomeCompositionOut:
    """Fail-closed: 404 unless locale is fa/en and at least one row projects."""
    if locale not in Locale.values:
        raise HttpError(404, "home composition not found")
    rows = list(HomeModule.objects.visible_for_locale(locale))
    if not rows:
        raise HttpError(404, "home composition not found")
    return HomeCompositionOut(
        revision=max(row.updated_at for row in rows).isoformat(),
        modules=[HomeModuleOut(key=row.key, order=row.order) for row in rows],
    )


# --- [PUBLIC-API] Graph (BK-05) ---------------------------------------------
# Target contract: GraphNodePublic/GraphEdgePublic of
# Assets/site-redesign/implementation-reference/AGENT-COORDINATION.md §4.
# Groups (GraphGroup) are editor-side only and never projected (phase 1).


class GraphRelatedRecordOut(Schema):
    """One published linked record behind a node (family from ContentType)."""

    family: str
    id: str


class GraphNodePositionOut(Schema):
    """Pinned node position; ``z`` is omitted when unpinned."""

    x: float
    y: float
    z: float | None = None


class GraphNodePublicOut(Schema):
    """Public node - camelCase mapping of GraphNode.

    ``summary``/``colorRole``/``iconRole`` and ``position`` are omitted when
    blank/unpinned (response serialized with ``exclude_none``).
    """

    id: str
    type: str
    label: str
    accessibleLabel: str
    weight: int
    summary: str | None = None
    colorRole: str | None = None
    iconRole: str | None = None
    position: GraphNodePositionOut | None = None
    relatedRecords: list[GraphRelatedRecordOut] = Field(default_factory=list)


class GraphEdgePublicOut(Schema):
    """Public edge - camelCase mapping of GraphEdge (blank explanation omitted)."""

    id: str
    source: str
    target: str
    relationType: str
    directed: bool
    weight: int
    explanation: str | None = None


class GraphPayloadOut(Schema):
    """One active graph version: nodes + edges only (no groups in phase 1)."""

    nodes: list[GraphNodePublicOut] = Field(default_factory=list)
    edges: list[GraphEdgePublicOut] = Field(default_factory=list)


def _graph_public_related(
    nodes: list[GraphNode],
) -> dict[int, list[GraphRelatedRecordOut]]:
    """Batch-resolve node related targets; only publicly readable rows survive.

    Dangling references (deleted rows), unpublished objects and models without
    a ``public()`` gate are omitted fail-closed - re-checked at read time,
    never trusted from authoring-time validation alone.
    """
    by_node: dict[int, list[GraphRelatedRecordOut]] = {node.pk: [] for node in nodes}
    if not by_node:
        return by_node
    rows = GraphNodeRelated.objects.filter(node__in=by_node).select_related(
        "content_type"
    )
    by_content_type: dict[int, list[GraphNodeRelated]] = {}
    for row in rows:
        by_content_type.setdefault(row.content_type_id, []).append(row)
    visible: set[tuple[int, int]] = set()
    for content_type_id, group in by_content_type.items():
        model = group[0].content_type.model_class()
        public = getattr(getattr(model, "objects", None), "public", None)
        if public is None:
            continue
        ids = public().filter(pk__in=[row.object_id for row in group]).values_list(
            "pk", flat=True
        )
        visible.update((content_type_id, pk) for pk in ids)
    for row in rows:
        if (row.content_type_id, row.object_id) in visible:
            by_node[row.node_id].append(
                GraphRelatedRecordOut(
                    family=row.content_type.model, id=str(row.object_id)
                )
            )
    return by_node


def public_graph_payload(locale: str) -> GraphPayloadOut | None:
    """Public projection of the ACTIVE graph version for one locale (BK-05).

    Business rules live here (doctrine 8.1): fail-closed active-version gate,
    camelCase shape mapping, blank-value omission and the stable edge id
    composition ``{sourceNodeId}->{targetNodeId}:{relationType}``. Returns
    ``None`` when no active version exists (the view raises 404).
    """
    version = GraphVersion.objects.latest_active(locale)
    if version is None:
        return None
    nodes = list(version.nodes.order_by("node_id"))
    edges = list(version.edges.select_related("source", "target").order_by("id"))
    related = _graph_public_related(nodes)
    out_nodes: list[GraphNodePublicOut] = []
    for node in nodes:
        position = None
        if node.pos_x is not None and node.pos_y is not None:
            position = GraphNodePositionOut(x=node.pos_x, y=node.pos_y, z=node.pos_z)
        out_nodes.append(
            GraphNodePublicOut(
                id=node.node_id,
                type=node.type,
                label=node.label,
                accessibleLabel=(node.accessible_label or "").strip(),
                summary=(node.summary or "").strip() or None,
                colorRole=(node.color_role or "").strip() or None,
                iconRole=(node.icon_role or "").strip() or None,
                weight=node.weight,
                position=position,
                relatedRecords=related.get(node.pk, []),
            )
        )
    out_edges = [
        GraphEdgePublicOut(
            id=f"{edge.source.node_id}->{edge.target.node_id}:{edge.relation_type}",
            source=edge.source.node_id,
            target=edge.target.node_id,
            relationType=edge.relation_type,
            directed=edge.directed,
            weight=edge.weight,
            explanation=(edge.explanation or "").strip() or None,
        )
        for edge in edges
    ]
    return GraphPayloadOut(nodes=out_nodes, edges=out_edges)


@api.get(
    "/graph/{locale}",
    response=GraphPayloadOut,
    exclude_none=True,
    summary="Active research graph for a locale (nodes + edges, no groups)",
)
def get_graph(request, locale: str) -> GraphPayloadOut:
    """Fail-closed: 404 unless locale is fa/en and an active version exists."""
    if locale not in Locale.values:
        raise HttpError(404, "graph not found")
    payload = public_graph_payload(locale)
    if payload is None:
        raise HttpError(404, "graph not found")
    return payload


from apps.analytics.api import (  # noqa: E402
    analytics_public_router,
    analytics_validation_envelope,
)
from apps.api.public_contact import contact_router  # noqa: E402
from apps.api.record_resolver import record_resolver_router  # noqa: E402

api.add_router("", contact_router)
api.add_router("", record_resolver_router)
api.add_router("", analytics_public_router)


@api.exception_handler(NinjaValidationError)
def _analytics_scoped_validation_error(request, exc):
    """A09: I08 envelope for analytics schema-shape (422) rejections only.

    Every other path keeps ninja's default 422 shape untouched
    (ERROR-COMPATIBILITY until explicitly migrated).
    """
    if (getattr(request, "path", "") or "").startswith("/api/v1/analytics/"):
        return api.create_response(
            request, analytics_validation_envelope(request, exc.errors), status=422
        )
    return api.create_response(request, {"detail": exc.errors}, status=422)
