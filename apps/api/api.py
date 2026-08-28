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
from ninja.pagination import PageNumberPagination, paginate

from apps.composition.projection import public_story_document
from apps.content.models import (
    AccessState,
    Article,
    ArticleSlugRedirect,
    Book,
    Course,
    CreativeWork,
    Download,
    GraphNode,
    GraphNodeRelated,
    GraphVersion,
    HomeModule,
    Landing,
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
from apps.content.services.public_projection import (
    published_for_locale,
    sanitize_public_richtext,
)
from apps.media.models import Media
from apps.media.public_urls import public_media_ref
from apps.siteconfig.models import SiteSettings

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


class ArticleDetailOut(ArticleListOut):
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


@api.get(
    "/landings/{locale}",
    response=list[LandingOut],
    summary="List published landing pages for a locale",
)
def list_landings(request, locale: str) -> list[Landing]:
    return list(published_for_locale(Landing.objects, locale))


@api.get(
    "/landings/{locale}/{slug}",
    response=LandingOut,
    summary="Get one published landing page by slug",
)
def get_landing(request, locale: str, slug: str) -> Landing:
    landing = published_for_locale(Landing.objects, locale).filter(slug=slug).first()
    if landing is None:
        raise HttpError(404, "landing not found")
    return landing


@api.get(
    "/profiles/{locale}",
    response=list[ProfileOut],
    summary="List published profile pages for a locale",
)
def list_profiles(request, locale: str) -> list[Profile]:
    return list(published_for_locale(Profile.objects, locale))


@api.get(
    "/profiles/{locale}/{slug}",
    response=ProfileOut,
    summary="Get one published profile page by slug",
)
def get_profile(request, locale: str, slug: str) -> Profile:
    profile = published_for_locale(Profile.objects, locale).filter(slug=slug).first()
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
    return qs.distinct()


@api.get(
    "/articles/{locale}/{slug}",
    response=ArticleDetailOut,
    summary="Get one published article by slug",
)
def get_article(request, locale: str, slug: str) -> Article:
    article = (
        Article.objects.public()
        .filter(locale=locale, slug=slug)
        .select_related("story", "featured_image")
        .prefetch_related("topic_tags", "series", "story__sections__blocks")
        .first()
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
    return list(
        Series.objects.public().filter(locale=locale).order_by("ordering", "slug")
    )


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


class ResearchTopicDetailOut(ResearchTopicListOut):
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
    def resolve_projects(obj: ResearchTopic) -> list[RelatedSlugOut]:
        return [
            RelatedSlugOut(slug=p.slug, title=p.title)
            for p in obj.projects.public().filter(locale=obj.locale).order_by("slug")
        ]

    @staticmethod
    def resolve_publications(obj: ResearchTopic) -> list[RelatedSlugOut]:
        pubs = (
            Publication.objects.public()
            .filter(projects__in=obj.projects.public(), locale=obj.locale)
            .distinct()
            .order_by("slug")
        )
        return [RelatedSlugOut(slug=p.slug, title=p.title) for p in pubs]


class ResearchStatementOut(Schema):
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


class ProjectDetailOut(ProjectListOut):
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
        return [
            RelatedSlugOut(slug=t.slug, title=t.title)
            for t in obj.topics.public().filter(locale=obj.locale).order_by("slug")
        ]

    @staticmethod
    def resolve_publications(obj: Project) -> list[RelatedSlugOut]:
        return [
            RelatedSlugOut(slug=p.slug, title=p.title)
            for p in obj.publications.public().filter(locale=obj.locale).order_by("slug")
        ]

    @staticmethod
    def resolve_evidence(obj: Project) -> list[EvidenceOut]:
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
        return [
            CollaboratorOut(name=row.name, role=row.role)
            for row in obj.collaborators.filter(publication_approved=True)
        ]

    @staticmethod
    def resolve_funding(obj: Project) -> list[FundingOut]:
        return [
            FundingOut(funder=row.funder, grant_id=row.grant_id)
            for row in obj.funding_items.filter(publication_approved=True)
        ]

    @staticmethod
    def resolve_case_study(obj: Project):
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
    project = _project_detail_queryset().filter(locale=locale, slug=slug).first()
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


class PublicationDetailOut(PublicationListOut):
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


class BookDetailOut(BookListOut):
    description: str
    url: str
    accessibility_notes: str
    cover: dict | None = None

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


class TalkDetailOut(TalkListOut):
    abstract: str
    video_url: str
    slides_url: str
    accessibility_notes: str
    slides: dict | None = None

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


class DownloadDetailOut(DownloadListOut):
    accessibility_notes: str
    file: dict | None = None
    mime: str | None = None
    size_bytes: int | None = None

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


class CourseDetailOut(CourseListOut):
    body: str
    prerequisites: str
    outcomes: str
    accessibility_notes: str
    cover: dict | None = None

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


class CreativeWorkDetailOut(CreativeWorkListOut):
    body: str
    rights_statement: str
    accessibility_notes: str
    cover: dict | None = None
    gallery: list[GalleryImageOut] = Field(default_factory=list)

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
    return (
        ResearchTopic.objects.public()
        .filter(locale=locale)
        .order_by("slug")
    )


@api.get(
    "/research/topics/{locale}/{slug}",
    response=ResearchTopicDetailOut,
    summary="Get one published research topic by slug",
)
def get_research_topic(request, locale: str, slug: str) -> ResearchTopic:
    topic = (
        ResearchTopic.objects.public()
        .filter(locale=locale, slug=slug)
        .select_related("story")
        .prefetch_related("projects", "story__sections__blocks")
        .first()
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
    return list(
        ResearchStatement.objects.public()
        .filter(locale=locale)
        .select_related("story", "statement_pdf")
        .prefetch_related("story__sections__blocks")
        .order_by("slug")
    )


@api.get(
    "/research/statements/{locale}/{slug}",
    response=ResearchStatementOut,
    summary="Get one published research statement by slug",
)
def get_research_statement(request, locale: str, slug: str) -> ResearchStatement:
    statement = (
        ResearchStatement.objects.public()
        .filter(locale=locale, slug=slug)
        .select_related("story", "statement_pdf")
        .prefetch_related("story__sections__blocks")
        .first()
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
    qs = (
        Project.objects.public()
        .filter(locale=locale, show_on_projects=True)
        .order_by("-published_at", "slug")
    )
    if has_case_study:
        qs = qs.filter(case_study__isnull=False).select_related("case_study")
    return qs


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
    return (
        Project.objects.public()
        .filter(locale=locale)
        .select_related("case_study")
        .order_by("-published_at", "slug")
    )


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
    return (
        Publication.objects.public()
        .filter(locale=locale)
        .order_by("-date", "slug")
    )


@api.get(
    "/research/publications/{locale}/{slug}",
    response=PublicationDetailOut,
    summary="Get one published publication by slug",
)
def get_research_publication(request, locale: str, slug: str) -> Publication:
    publication = (
        Publication.objects.public()
        .filter(locale=locale, slug=slug)
        .select_related("pdf_media")
        .first()
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
    return (
        Publication.objects.public()
        .filter(locale=locale)
        .order_by("-date", "slug")
    )


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
    return Book.objects.public().filter(locale=locale).order_by("-publication_date", "slug")


@api.get(
    "/books/{locale}/{slug}",
    response=BookDetailOut,
    summary="Get one published book by slug",
)
def get_book(request, locale: str, slug: str) -> Book:
    book = (
        Book.objects.public()
        .filter(locale=locale, slug=slug)
        .select_related("cover_media")
        .first()
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
    return Talk.objects.public().filter(locale=locale).order_by("-event_date", "slug")


@api.get(
    "/talks/{locale}/{slug}",
    response=TalkDetailOut,
    summary="Get one published talk by slug",
)
def get_talk(request, locale: str, slug: str) -> Talk:
    talk = (
        Talk.objects.public()
        .filter(locale=locale, slug=slug)
        .select_related("slides_media")
        .first()
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
    return Download.objects.public().filter(locale=locale).order_by("-published_at", "slug")


@api.get(
    "/downloads/{locale}/{slug}",
    response=DownloadDetailOut,
    summary="Get one published download by slug",
)
def get_download(request, locale: str, slug: str) -> Download:
    download = (
        Download.objects.public()
        .filter(locale=locale, slug=slug)
        .select_related("media")
        .first()
    )
    if download is None:
        raise HttpError(404, "download not found")
    return download


@api.get(
    "/downloads/{locale}/{slug}/file",
    summary="Stream a published public download file (active media only)",
)
def download_file(request, locale: str, slug: str):
    download = (
        Download.objects.public()
        .filter(locale=locale, slug=slug)
        .select_related("media")
        .first()
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
    return Course.objects.public().filter(locale=locale).order_by("slug")


@api.get(
    "/courses/{locale}/{slug}",
    response=CourseDetailOut,
    summary="Get one published course by slug",
)
def get_course(request, locale: str, slug: str) -> Course:
    course = (
        Course.objects.public()
        .filter(locale=locale, slug=slug)
        .select_related("cover_media")
        .first()
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
    return Course.objects.public().filter(locale=locale).order_by("slug")


@api.get(
    "/teaching/{locale}/{slug}",
    response=CourseDetailOut,
    summary="Get one published teaching course by slug (alias)",
)
def get_teaching_course(request, locale: str, slug: str) -> Course:
    return get_course(request, locale, slug)


@api.get(
    "/creative-works/{locale}",
    response=list[CreativeWorkListOut],
    summary="List published creative works for a locale (paginated)",
)
@paginate(PageNumberPagination, page_size=10)
def list_creative_works(request, locale: str):
    return CreativeWork.objects.public().filter(locale=locale).order_by("slug")


@api.get(
    "/creative-works/{locale}/{slug}",
    response=CreativeWorkDetailOut,
    summary="Get one published creative work by slug",
)
def get_creative_work(request, locale: str, slug: str) -> CreativeWork:
    work = (
        CreativeWork.objects.public()
        .filter(locale=locale, slug=slug)
        .select_related("cover_media")
        .prefetch_related("gallery_images__media")
        .first()
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
    return CreativeWork.objects.public().filter(locale=locale).order_by("slug")


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


from apps.api.public_contact import contact_router  # noqa: E402

api.add_router("", contact_router)

