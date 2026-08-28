"""Typed localized content contracts — plain Django models, lifecycle-aware.

The public projection is enforced by :meth:`ContentQuerySet.public` and the
associated manager; no other access path leaks non-public records to consumers.
"""

from __future__ import annotations

import math
import re
import uuid

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.html import strip_tags

READING_WPM = 200
# Per-locale reading speed (custom-admin-rebuild-fa §14.1 F1); unknown locales fall back.
READING_WPM_BY_LOCALE = {"fa": 180, "en": 230}

# ADR-0022 allowlist — editor/docs contract; storage is plain TextField HTML.
# Keep in sync with RICHTEXT_ALLOWED_FEATURES in config.settings.base.
ARTICLE_RICHTEXT_FEATURES = [
    "h2",
    "h3",
    "h4",
    "bold",
    "italic",
    "ol",
    "ul",
    "link",
    "document-link",
    "hr",
    "blockquote",
    "code",
]


class LifecycleStatus(models.TextChoices):
    """Publication lifecycle shared by all CMS content entities."""

    DRAFT = "draft", "Draft"
    REVIEW = "review", "Review"
    SCHEDULED = "scheduled", "Scheduled"
    PUBLISHED = "published", "Published"
    ARCHIVED = "archived", "Archived"


class Locale(models.TextChoices):
    """Locales supported by the platform (fa/en content identity is independent)."""

    FA = "fa", "Persian"
    EN = "en", "English"


class License(models.TextChoices):
    """Editorial license choices for public articles (P4)."""

    CC_BY_4 = "cc-by-4", "CC BY 4.0"
    CC_BY_NC_4 = "cc-by-nc-4", "CC BY-NC 4.0"
    ALL_RIGHTS_RESERVED = "all-rights-reserved", "All Rights Reserved"


class ContentQuerySet(models.QuerySet):
    """QuerySet enforcing the strict public projection: published and in the past only."""

    def public(self) -> ContentQuerySet:
        """Return only records with ``status=published`` and ``published_at <= now``."""
        return self.filter(
            status=LifecycleStatus.PUBLISHED,
            published_at__lte=timezone.now(),
        )


class ContentManager(models.Manager.from_queryset(ContentQuerySet)):
    """Default manager; ``public()`` is available on both manager and queryset."""


class LifecycleMixin(models.Model):
    """Shared lifecycle fields and manager for every content entity."""

    status = models.CharField(
        max_length=20,
        choices=LifecycleStatus.choices,
        default=LifecycleStatus.DRAFT,
        db_index=True,
    )
    published_at = models.DateTimeField(null=True, blank=True)
    scheduled_for = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ContentManager()

    class Meta:
        abstract = True


class LocalizedContentMixin(models.Model):
    """Shared locale-identity fields (slug is unique per locale, not globally)."""

    locale = models.CharField(max_length=2, choices=Locale.choices, db_index=True)
    slug = models.SlugField(max_length=200)
    title = models.CharField(max_length=200)

    class Meta:
        abstract = True


def reading_wpm_for_locale(locale: str | None) -> int:
    """Words-per-minute for a locale (fa=180, en=230); unknown locales → 200."""
    return READING_WPM_BY_LOCALE.get(str(locale or "").lower(), READING_WPM)


def compute_reading_time_minutes(
    body_html: str, *, wpm: int | None = None, locale: str | None = None
) -> int:
    """Estimate reading time from rich-text body using per-locale WPM.

    Empty body → 0. An explicit ``wpm`` overrides the locale lookup.
    """
    if wpm is None:
        wpm = reading_wpm_for_locale(locale)
    text = strip_tags(body_html or "")
    words = re.findall(r"\S+", text)
    if not words:
        return 0
    return max(1, math.ceil(len(words) / wpm))


class Landing(LocalizedContentMixin, LifecycleMixin):
    """Localized landing page content (one row per locale)."""

    body = models.TextField(blank=True)
    seo_title = models.CharField(max_length=200, blank=True)
    seo_description = models.TextField(blank=True)

    class Meta:
        db_table = "content_landing"
        ordering = ["locale", "slug"]
        indexes = [
            models.Index(
                fields=["locale", "slug", "status"],
                name="landing_locale_slug_status_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["locale", "slug"],
                name="content_landing_unique_locale_slug",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.title} ({self.locale})"


class Profile(LocalizedContentMixin, LifecycleMixin):
    """Localized profile page content (one row per locale)."""

    body = models.TextField(blank=True)
    seo_title = models.CharField(max_length=200, blank=True)
    seo_description = models.TextField(blank=True)
    translation_key = models.UUIDField(default=uuid.uuid4, editable=False, db_index=True)
    revision = models.PositiveIntegerField(default=1)
    short_bio = models.TextField(blank=True)
    long_bio = models.TextField(blank=True)
    availability = models.TextField(blank=True)

    class Meta:
        db_table = "content_profile"
        ordering = ["locale", "slug"]
        indexes = [
            models.Index(
                fields=["locale", "slug", "status"],
                name="profile_locale_slug_status_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["locale", "slug"],
                name="content_profile_unique_locale_slug",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.title} ({self.locale})"

    def available_locales(self) -> list[str]:
        return list(
            Profile.objects.public()
            .filter(translation_key=self.translation_key)
            .order_by("locale")
            .values_list("locale", flat=True)
        )


class OrderedProfileItem(models.Model):
    """Abstract ordered child row for typed profile sections."""

    ordering = models.PositiveIntegerField(default=0)

    class Meta:
        abstract = True
        ordering = ["ordering", "id"]


class ProfileDetailItem(OrderedProfileItem):
    """Child rows that may expose public About detail routes."""

    slug = models.SlugField(max_length=200, blank=True)
    translation_key = models.UUIDField(null=True, blank=True, db_index=True)
    detail_body = models.TextField(blank=True)

    class Meta(OrderedProfileItem.Meta):
        abstract = True


class ProfileSkill(ProfileDetailItem):
    profile = models.ForeignKey(
        Profile,
        on_delete=models.CASCADE,
        related_name="skills",
    )
    category = models.CharField(max_length=200)
    name = models.CharField(max_length=200)
    source = models.CharField(max_length=300)

    class Meta(OrderedProfileItem.Meta):
        db_table = "content_profile_skill"


class ProfileExperience(ProfileDetailItem):
    profile = models.ForeignKey(
        Profile,
        on_delete=models.CASCADE,
        related_name="experience_entries",
    )
    organization = models.CharField(max_length=300)
    role = models.CharField(max_length=300)
    period = models.CharField(max_length=100)
    location = models.CharField(max_length=200, blank=True)
    website = models.URLField(blank=True)
    bullets = models.JSONField(default=list, blank=True)
    story = models.ForeignKey(
        "composition.CompositionPage",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="attached_profile_experiences",
    )

    class Meta(OrderedProfileItem.Meta):
        db_table = "content_profile_experience"


class ProfileEducation(ProfileDetailItem):
    profile = models.ForeignKey(
        Profile,
        on_delete=models.CASCADE,
        related_name="education_entries",
    )
    institution = models.CharField(max_length=300)
    degree = models.CharField(max_length=200)
    field = models.CharField(max_length=200)
    period = models.CharField(max_length=100)
    gpa = models.CharField(max_length=50, blank=True)
    thesis = models.TextField(blank=True)

    class Meta(OrderedProfileItem.Meta):
        db_table = "content_profile_education"


class ProfilePublication(ProfileDetailItem):
    profile = models.ForeignKey(
        Profile,
        on_delete=models.CASCADE,
        related_name="publication_entries",
    )
    title = models.CharField(max_length=300)
    status = models.TextField(blank=True)

    class Meta(OrderedProfileItem.Meta):
        db_table = "content_profile_publication"


class ProfileResearchProject(ProfileDetailItem):
    profile = models.ForeignKey(
        Profile,
        on_delete=models.CASCADE,
        related_name="research_projects",
    )
    title = models.CharField(max_length=300)
    summary = models.TextField(blank=True)
    url = models.URLField(blank=True)
    link_label = models.CharField(max_length=100, blank=True)

    class Meta(OrderedProfileItem.Meta):
        db_table = "content_profile_research_project"


class ProfileCertificate(ProfileDetailItem):
    profile = models.ForeignKey(
        Profile,
        on_delete=models.CASCADE,
        related_name="certificate_entries",
    )
    name = models.CharField(max_length=300)
    detail = models.CharField(max_length=300, blank=True)

    class Meta(OrderedProfileItem.Meta):
        db_table = "content_profile_certificate"


class ProfileSocialLink(OrderedProfileItem):
    profile = models.ForeignKey(
        Profile,
        on_delete=models.CASCADE,
        related_name="social_links",
    )
    platform = models.CharField(max_length=100)
    url = models.URLField()

    class Meta(OrderedProfileItem.Meta):
        db_table = "content_profile_social_link"


class TopicTag(models.Model):
    """Locale-aware editorial topic tag (slug unique globally per P4 Spec).

    ``description`` is the glossary entry shown on canonical topic pages
    (P10-01); ``synonyms`` is a comma-separated alias list — aliases are not
    separate tags (see ``docs/governance/TAXONOMY_GOVERNANCE.md`` §3).
    """

    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=100, unique=True)
    locale = models.CharField(max_length=2, choices=Locale.choices, db_index=True)
    description = models.TextField(
        blank=True,
        help_text=(
            "Glossary entry / editorial definition for the tag "
            "(shown on topic pages when present)."
        ),
    )
    synonyms = models.TextField(
        blank=True,
        help_text=(
            "Comma-separated synonyms / aliases (not separate tags); "
            "creation must check this list."
        ),
    )

    class Meta:
        db_table = "content_topic_tag"
        ordering = ["locale", "name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.locale})"


class Series(LocalizedContentMixin, LifecycleMixin):
    """Locale-aware article series with manual ordering within a locale."""

    description = models.TextField(blank=True)
    ordering = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "content_series"
        ordering = ["locale", "ordering", "slug"]
        indexes = [
            models.Index(
                fields=["locale", "slug", "status"],
                name="series_locale_slug_status_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["locale", "slug"],
                name="content_series_unique_locale_slug",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.title} ({self.locale})"


class Article(LocalizedContentMixin, LifecycleMixin):
    """Published writing unit — rich text body, tags, series, license (P4)."""

    body = models.TextField(blank=True)
    excerpt = models.TextField(blank=True)
    featured_image = models.ForeignKey(
        "media.Media",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    topic_tags = models.ManyToManyField(TopicTag, blank=True, related_name="articles")
    series = models.ManyToManyField(Series, blank=True, related_name="articles")
    license = models.CharField(
        max_length=32,
        choices=License.choices,
        default=License.CC_BY_NC_4,
    )
    accessibility_notes = models.TextField(blank=True)
    reading_time_minutes = models.PositiveIntegerField(default=0)
    allow_comments = models.BooleanField(default=False)
    story = models.ForeignKey(
        "composition.CompositionPage",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="attached_articles",
    )

    class Meta:
        db_table = "content_article"
        ordering = ["locale", "slug"]
        indexes = [
            models.Index(
                fields=["locale", "slug", "status"],
                name="article_locale_slug_status_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["locale", "slug"],
                name="content_article_unique_locale_slug",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.title} ({self.locale})"

    def save(self, *args, **kwargs) -> None:
        self.reading_time_minutes = compute_reading_time_minutes(
            str(self.body or ""), locale=self.locale
        )
        super().save(*args, **kwargs)


class ArticleSlugRedirect(models.Model):
    """Stable redirect from a retired article slug to the current slug (per locale)."""

    locale = models.CharField(max_length=2, choices=Locale.choices, db_index=True)
    old_slug = models.SlugField(max_length=200)
    new_slug = models.SlugField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "content_article_slug_redirect"
        ordering = ["locale", "old_slug"]
        constraints = [
            models.UniqueConstraint(
                fields=["locale", "old_slug"],
                name="content_article_slug_redirect_unique",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.locale}:{self.old_slug} → {self.new_slug}"


class ProjectType(models.TextChoices):
    """Canonical Project.type values (master plan / P5 Spec)."""

    RESEARCH = "research", "Research"
    ENGINEERING = "engineering", "Engineering"
    AI = "ai", "AI"
    DATA = "data", "Data"
    DESIGN = "design", "Design"
    EXPERIMENT = "experiment", "Experiment"


class Availability(models.TextChoices):
    """Code/data/demo availability (product §20 + Task-list restricted for data)."""

    PUBLIC = "public", "Public"
    AVAILABLE_ON_REQUEST = "available_on_request", "Available on request"
    PRIVATE = "private", "Private"
    NOT_AVAILABLE = "not_available", "Not available"
    NOT_APPLICABLE = "not_applicable", "Not applicable"
    RESTRICTED = "restricted", "Restricted"


class EvidenceVisibility(models.TextChoices):
    """Visibility gate for structured project evidence rows."""

    PUBLIC = "public", "Public"
    RESTRICTED = "restricted", "Restricted"
    INTERNAL = "internal", "Internal"


class AccessState(models.TextChoices):
    """Public file/link access for P8 publications, books, talks, downloads."""

    PUBLIC = "public", "Public"
    RESTRICTED = "restricted", "Restricted"
    METADATA_ONLY = "metadata_only", "Metadata only"


class PublicationType(models.TextChoices):
    """Bibliographic kind (orthogonal to CMS lifecycle status)."""

    JOURNAL = "journal", "Journal article"
    CONFERENCE = "conference", "Conference paper"
    BOOK_CHAPTER = "book_chapter", "Book chapter"
    MANUSCRIPT = "manuscript", "Manuscript"
    OTHER = "other", "Other"


class AcademicStage(models.TextChoices):
    """Academic publication stage (orthogonal to CMS lifecycle status)."""

    PREPRINT = "preprint", "Preprint"
    IN_PRESS = "in_press", "In press"
    PUBLISHED = "published", "Published"
    OTHER = "other", "Other"


class DownloadType(models.TextChoices):
    """Typed download catalog categories (Media-backed)."""

    PDF = "pdf", "PDF"
    DATASET = "dataset", "Dataset"
    CODE = "code", "Code archive"
    ARCHIVE = "archive", "Archive"
    OTHER = "other", "Other"


class ResearchTopic(LocalizedContentMixin, LifecycleMixin):
    """Research domain / agenda area (distinct from blog TopicTag)."""

    summary = models.TextField(blank=True)
    motivation = models.TextField(blank=True)
    problems = models.TextField(blank=True)
    research_questions = models.TextField(blank=True)
    methods = models.TextField(blank=True)
    future_directions = models.TextField(blank=True)
    story = models.ForeignKey(
        "composition.CompositionPage",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="attached_research_topics",
    )

    class Meta:
        db_table = "content_research_topic"
        ordering = ["locale", "slug"]
        indexes = [
            models.Index(
                fields=["locale", "slug", "status"],
                name="research_topic_loc_slug_st_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["locale", "slug"],
                name="content_research_topic_unique_locale_slug",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.title} ({self.locale})"


class ResearchStatement(LocalizedContentMixin, LifecycleMixin):
    """Independent research agenda statement (HTML text + optional PDF)."""

    body = models.TextField(blank=True)
    statement_pdf = models.ForeignKey(
        "media.Media",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="Optional downloadable PDF (Media library; public only when active).",
    )
    story = models.ForeignKey(
        "composition.CompositionPage",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="attached_research_statements",
    )

    class Meta:
        db_table = "content_research_statement"
        ordering = ["locale", "slug"]
        indexes = [
            models.Index(
                fields=["locale", "slug", "status"],
                name="research_stmt_loc_slug_st_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["locale", "slug"],
                name="content_research_statement_unique_locale_slug",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.title} ({self.locale})"

    def clean(self) -> None:
        """P5: at most one published statement per locale."""
        from django.core.exceptions import ValidationError

        super().clean()
        if self.status != LifecycleStatus.PUBLISHED:
            return
        qs = ResearchStatement.objects.filter(
            locale=self.locale,
            status=LifecycleStatus.PUBLISHED,
        )
        if self.pk:
            qs = qs.exclude(pk=self.pk)
        if qs.exists():
            raise ValidationError(
                {"status": "Only one published ResearchStatement is allowed per locale."}
            )


class Publication(LocalizedContentMixin, LifecycleMixin):
    """Publication core (P5) extended for P8 abstract/identifiers/access."""

    authors = models.TextField(blank=True)
    venue = models.CharField(max_length=300, blank=True)
    date = models.DateField(null=True, blank=True)
    doi = models.CharField(max_length=200, blank=True)
    url = models.URLField(blank=True)
    pdf_url = models.URLField(blank=True)
    abstract = models.TextField(blank=True)
    publication_type = models.CharField(
        max_length=32,
        choices=PublicationType.choices,
        default=PublicationType.OTHER,
    )
    academic_stage = models.CharField(
        max_length=32,
        choices=AcademicStage.choices,
        default=AcademicStage.OTHER,
    )
    isbn = models.CharField(max_length=32, blank=True)
    preprint_url = models.URLField(blank=True)
    code_url = models.URLField(blank=True)
    dataset_url = models.URLField(blank=True)
    access_state = models.CharField(
        max_length=32,
        choices=AccessState.choices,
        default=AccessState.PUBLIC,
    )
    accessibility_notes = models.TextField(blank=True)
    citation_text = models.TextField(
        blank=True,
        help_text="Editor-supplied citation export text only; never auto-fabricated.",
    )
    pdf_media = models.ForeignKey(
        "media.Media",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    license = models.CharField(
        max_length=32,
        choices=License.choices,
        default=License.CC_BY_NC_4,
    )
    citation_count = models.PositiveIntegerField(null=True, blank=True)
    citation_source = models.CharField(max_length=300, blank=True)
    citation_last_verified = models.DateField(null=True, blank=True)
    citation_visibility = models.CharField(
        max_length=20,
        choices=EvidenceVisibility.choices,
        default=EvidenceVisibility.INTERNAL,
    )

    class Meta:
        db_table = "content_publication"
        ordering = ["locale", "slug"]
        indexes = [
            models.Index(
                fields=["locale", "slug", "status"],
                name="publication_loc_slug_st_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["locale", "slug"],
                name="content_publication_unique_locale_slug",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.title} ({self.locale})"

    def allows_public_file(self) -> bool:
        return self.access_state == AccessState.PUBLIC

    def public_citation_count(self) -> int | None:
        """Return citation_count only when source + verified + public visibility."""
        if self.citation_visibility != EvidenceVisibility.PUBLIC:
            return None
        if not (self.citation_source or "").strip():
            return None
        if self.citation_last_verified is None:
            return None
        return self.citation_count

    def public_citation_text(self) -> str | None:
        """Return citation text only when editor-supplied with authors + title."""
        text = (self.citation_text or "").strip()
        if not text:
            return None
        if not (self.title or "").strip():
            return None
        if not (self.authors or "").strip():
            return None
        return text

    def public_pdf_url(self) -> str:
        """External PDF URL only when access_state is public."""
        if not self.allows_public_file():
            return ""
        return (self.pdf_url or "").strip()

    def public_external_url(self) -> str:
        return (self.url or "").strip()


class Project(LocalizedContentMixin, LifecycleMixin):
    """Canonical project entity (research/engineering/ai/…); no ResearchProject twin."""

    project_type = models.CharField(
        max_length=32,
        choices=ProjectType.choices,
        default=ProjectType.RESEARCH,
    )
    objective = models.TextField(blank=True)
    methods_summary = models.TextField(blank=True)
    role = models.TextField(blank=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    license = models.CharField(
        max_length=32,
        choices=License.choices,
        default=License.CC_BY_NC_4,
    )
    code_availability = models.CharField(
        max_length=32,
        choices=Availability.choices,
        default=Availability.NOT_APPLICABLE,
    )
    data_availability = models.CharField(
        max_length=32,
        choices=Availability.choices,
        default=Availability.NOT_APPLICABLE,
    )
    demo_availability = models.CharField(
        max_length=32,
        choices=Availability.choices,
        default=Availability.NOT_APPLICABLE,
    )
    code_url = models.URLField(blank=True)
    data_url = models.URLField(blank=True)
    demo_url = models.URLField(blank=True)
    show_on_projects = models.BooleanField(
        default=True,
        db_default=True,
        help_text="When false, a published project is omitted from the public /projects/ list.",
    )
    story = models.ForeignKey(
        "composition.CompositionPage",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="attached_projects",
    )
    topics = models.ManyToManyField(
        ResearchTopic,
        blank=True,
        related_name="projects",
    )
    publications = models.ManyToManyField(
        Publication,
        blank=True,
        related_name="projects",
    )

    class Meta:
        db_table = "content_project"
        ordering = ["locale", "slug"]
        indexes = [
            models.Index(
                fields=["locale", "slug", "status"],
                name="project_loc_slug_st_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["locale", "slug"],
                name="content_project_unique_locale_slug",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.title} ({self.locale})"

    def public_code_url(self) -> str:
        if self.code_availability == Availability.PUBLIC:
            return self.code_url or ""
        return ""

    def public_data_url(self) -> str:
        if self.data_availability == Availability.PUBLIC:
            return self.data_url or ""
        return ""

    def public_demo_url(self) -> str:
        if self.demo_availability == Availability.PUBLIC:
            return self.demo_url or ""
        return ""

    def clean(self) -> None:
        """P6: featured case study publish gate when extension exists."""
        from django.core.exceptions import ValidationError

        super().clean()
        if self.status != LifecycleStatus.PUBLISHED:
            return
        try:
            case_study = self.case_study
        except ProjectCaseStudyDetails.DoesNotExist:
            return
        try:
            case_study.validate_featured_publish_gate()
        except ValidationError as exc:
            raise ValidationError(exc.message_dict) from exc

    def save(self, *args, **kwargs) -> None:
        if self.status == LifecycleStatus.PUBLISHED:
            self.full_clean()
        super().save(*args, **kwargs)

    @property
    def has_case_study(self) -> bool:
        try:
            return self.case_study is not None
        except ProjectCaseStudyDetails.DoesNotExist:
            return False


class CaseStudyDepth(models.TextChoices):
    """Case study presentation depth (product §30)."""

    FEATURED = "featured_case_study", "Featured case study"
    STANDARD = "standard", "Standard"
    EXPERIMENT = "experiment", "Experiment"


class ProjectCaseStudyDetails(models.Model):
    """Typed case-study extension for canonical Project (P6); no parallel model."""

    project = models.OneToOneField(
        Project,
        on_delete=models.CASCADE,
        related_name="case_study",
    )
    depth = models.CharField(
        max_length=32,
        choices=CaseStudyDepth.choices,
        default=CaseStudyDepth.STANDARD,
    )
    problem = models.TextField(blank=True)
    constraints = models.TextField(blank=True)
    technical_decisions = models.TextField(blank=True)
    trade_offs = models.TextField(blank=True)
    outcomes_summary = models.TextField(blank=True)
    lessons_learned = models.TextField(blank=True)
    testing_summary = models.TextField(blank=True)

    class Meta:
        db_table = "content_project_case_study_details"

    def __str__(self) -> str:
        return f"Case study ({self.project_id})"

    def validate_featured_publish_gate(self) -> None:
        """Reject publish when featured baseline is incomplete."""
        from django.core.exceptions import ValidationError

        if self.depth != CaseStudyDepth.FEATURED:
            return
        if self.project.status != LifecycleStatus.PUBLISHED:
            return
        errors: dict[str, str] = {}
        if not (self.problem or "").strip():
            errors["problem"] = (
                "Required for published featured case studies."
            )
        if not (self.project.role or "").strip():
            errors["role"] = (
                "Project role is required for published featured case studies."
            )
        if not (self.trade_offs or "").strip():
            errors["trade_offs"] = (
                "Required for published featured case studies."
            )
        if not (self.outcomes_summary or "").strip():
            errors["outcomes_summary"] = (
                "Required for published featured case studies."
            )
        if not (self.project.license or "").strip():
            errors["license"] = (
                "License is required for published featured case studies."
            )
        for field in (
            "code_availability",
            "data_availability",
            "demo_availability",
        ):
            if not getattr(self.project, field):
                errors[field] = (
                    "Availability state is required for published featured case studies."
                )
        if errors:
            raise ValidationError(errors)

    def clean(self) -> None:
        super().clean()
        self.validate_featured_publish_gate()

    def save(self, *args, **kwargs) -> None:
        if self.project.status == LifecycleStatus.PUBLISHED:
            self.full_clean()
        super().save(*args, **kwargs)


class ProjectDiagram(models.Model):
    """Architecture diagram metadata for a Project (image admin-only until /media/)."""

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="diagrams",
    )
    title = models.CharField(max_length=200)
    version = models.CharField(max_length=50)
    diagram_date = models.DateField()
    alt_text = models.TextField(blank=True)
    long_description = models.TextField(blank=True)
    diagram_image = models.ForeignKey(
        "media.Media",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    visibility = models.CharField(
        max_length=20,
        choices=EvidenceVisibility.choices,
        default=EvidenceVisibility.INTERNAL,
    )

    class Meta:
        db_table = "content_project_diagram"
        ordering = ["id"]

    def __str__(self) -> str:
        return f"{self.title} ({self.project_id})"

    def is_publicly_projectable(self) -> bool:
        return (
            self.visibility == EvidenceVisibility.PUBLIC
            and bool((self.alt_text or "").strip())
            and bool((self.title or "").strip())
            and bool((self.version or "").strip())
            and self.diagram_date is not None
        )


class ProjectScreenshot(models.Model):
    """Optional screenshot row; no PII/credentials in public projection."""

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="screenshots",
    )
    caption = models.CharField(max_length=300, blank=True)
    alt_text = models.TextField(blank=True)
    external_url = models.URLField(blank=True)
    screenshot_image = models.ForeignKey(
        "media.Media",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    visibility = models.CharField(
        max_length=20,
        choices=EvidenceVisibility.choices,
        default=EvidenceVisibility.INTERNAL,
    )

    class Meta:
        db_table = "content_project_screenshot"
        ordering = ["id"]

    def __str__(self) -> str:
        return f"Screenshot ({self.project_id})"

    def is_publicly_projectable(self) -> bool:
        return (
            self.visibility == EvidenceVisibility.PUBLIC
            and bool((self.alt_text or "").strip())
            and bool((self.caption or "").strip())
        )

    def public_external_url(self) -> str:
        url = (self.external_url or "").strip()
        if not url or not self.is_publicly_projectable():
            return ""
        if url.startswith("http://") or url.startswith("https://"):
            return url
        return ""


class ProjectEvidence(models.Model):
    """Structured outcome/evidence row for a Project (visibility-gated)."""

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="evidence_items",
    )
    label = models.CharField(max_length=200)
    value = models.TextField(blank=True)
    source = models.TextField(blank=True)
    last_verified = models.DateField(null=True, blank=True)
    visibility = models.CharField(
        max_length=20,
        choices=EvidenceVisibility.choices,
        default=EvidenceVisibility.INTERNAL,
    )

    class Meta:
        db_table = "content_project_evidence"
        ordering = ["id"]

    def __str__(self) -> str:
        return f"{self.label} ({self.project_id})"

    def is_publicly_projectable(self) -> bool:
        return (
            self.visibility == EvidenceVisibility.PUBLIC
            and bool((self.source or "").strip())
        )


class ProjectCollaborator(models.Model):
    """Collaborator credit; public only when publication_approved."""

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="collaborators",
    )
    name = models.CharField(max_length=200)
    role = models.CharField(max_length=200, blank=True)
    publication_approved = models.BooleanField(default=False)

    class Meta:
        db_table = "content_project_collaborator"
        ordering = ["id"]

    def __str__(self) -> str:
        return self.name


class ProjectFunding(models.Model):
    """Funding disclosure; public only when publication_approved."""

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="funding_items",
    )
    funder = models.CharField(max_length=300)
    grant_id = models.CharField(max_length=200, blank=True)
    publication_approved = models.BooleanField(default=False)

    class Meta:
        db_table = "content_project_funding"
        ordering = ["id"]

    def __str__(self) -> str:
        return self.funder


class Book(LocalizedContentMixin, LifecycleMixin):
    """Typed book catalog entity (P8)."""

    authors = models.TextField(blank=True)
    isbn = models.CharField(max_length=32, blank=True)
    publisher = models.CharField(max_length=300, blank=True)
    publication_date = models.DateField(null=True, blank=True)
    description = models.TextField(blank=True)
    url = models.URLField(blank=True)
    license = models.CharField(
        max_length=32,
        choices=License.choices,
        default=License.ALL_RIGHTS_RESERVED,
    )
    access_state = models.CharField(
        max_length=32,
        choices=AccessState.choices,
        default=AccessState.METADATA_ONLY,
    )
    accessibility_notes = models.TextField(blank=True)
    cover_media = models.ForeignKey(
        "media.Media",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        db_table = "content_book"
        ordering = ["locale", "slug"]
        indexes = [
            models.Index(
                fields=["locale", "slug", "status"],
                name="book_loc_slug_st_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["locale", "slug"],
                name="content_book_unique_locale_slug",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.title} ({self.locale})"

    def allows_public_file(self) -> bool:
        return self.access_state == AccessState.PUBLIC


class Talk(LocalizedContentMixin, LifecycleMixin):
    """Typed talk / presentation catalog entity (P8)."""

    speakers = models.TextField(blank=True)
    event_name = models.CharField(max_length=300, blank=True)
    event_date = models.DateField(null=True, blank=True)
    location = models.CharField(max_length=300, blank=True)
    abstract = models.TextField(blank=True)
    video_url = models.URLField(blank=True)
    slides_url = models.URLField(blank=True)
    license = models.CharField(
        max_length=32,
        choices=License.choices,
        default=License.CC_BY_NC_4,
    )
    access_state = models.CharField(
        max_length=32,
        choices=AccessState.choices,
        default=AccessState.PUBLIC,
    )
    accessibility_notes = models.TextField(blank=True)
    slides_media = models.ForeignKey(
        "media.Media",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        db_table = "content_talk"
        ordering = ["locale", "slug"]
        indexes = [
            models.Index(
                fields=["locale", "slug", "status"],
                name="talk_loc_slug_st_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["locale", "slug"],
                name="content_talk_unique_locale_slug",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.title} ({self.locale})"

    def allows_public_file(self) -> bool:
        return self.access_state == AccessState.PUBLIC

    def public_video_url(self) -> str:
        if not self.allows_public_file():
            return ""
        return (self.video_url or "").strip()

    def public_slides_url(self) -> str:
        if not self.allows_public_file():
            return ""
        return (self.slides_url or "").strip()


class Download(LocalizedContentMixin, LifecycleMixin):
    """Media-backed download catalog entry (P8) — never a bare external URL."""

    description = models.TextField(blank=True)
    media = models.ForeignKey(
        "media.Media",
        on_delete=models.PROTECT,
        related_name="+",
    )
    download_type = models.CharField(
        max_length=32,
        choices=DownloadType.choices,
        default=DownloadType.OTHER,
    )
    language = models.CharField(
        max_length=16,
        blank=True,
        help_text="Language of the file contents (fa/en/…), not CMS locale identity.",
    )
    access_state = models.CharField(
        max_length=32,
        choices=AccessState.choices,
        default=AccessState.PUBLIC,
    )
    accessibility_notes = models.TextField(blank=True)
    license = models.CharField(
        max_length=32,
        choices=License.choices,
        default=License.ALL_RIGHTS_RESERVED,
    )

    class Meta:
        db_table = "content_download"
        ordering = ["locale", "slug"]
        indexes = [
            models.Index(
                fields=["locale", "slug", "status"],
                name="download_loc_slug_st_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["locale", "slug"],
                name="content_download_unique_locale_slug",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.title} ({self.locale})"

    def allows_public_file(self) -> bool:
        return self.access_state == AccessState.PUBLIC

    def public_media_is_downloadable(self) -> bool:
        media = self.media
        return bool(
            self.allows_public_file()
            and media is not None
            and media.is_active
            and media.file
        )


class Collection(LocalizedContentMixin, LifecycleMixin):
    """Curated collection (P10-03) — explicit editorial set with curator/criteria/date.

    Members are curated via M2M to published public projections only
    (``Article``, ``Project``, ``Publication``). No auto-query or AI
    inference. See ``docs/governance/TAXONOMY_GOVERNANCE.md`` §4.2 and
    ADR-0029 P10-03. ``curator_name`` / ``criteria`` / ``curated_date``
    are the curation contract (required for publish in a later strictness
    slice; scaffold allows blank for draft).
    """

    description = models.TextField(
        blank=True, help_text="Summary for list cards / collection page."
    )
    curator_name = models.CharField(
        max_length=200,
        blank=True,
        help_text="Human curator responsible for the collection (P10-03).",
    )
    curator_title = models.CharField(
        max_length=200,
        blank=True,
        help_text="Curator title / affiliation (optional).",
    )
    criteria = models.TextField(
        blank=True,
        help_text="Inclusion criteria prose — why members belong (P10-03).",
    )
    curated_date = models.DateField(
        null=True,
        blank=True,
        help_text="Date of last human curation review (P10-03).",
    )
    cover_media = models.ForeignKey(
        "media.Media",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="Optional cover image (active Media only in public projection).",
    )
    # Explicit curated members — all blank; projection filters to public() members.
    articles = models.ManyToManyField(
        "Article",
        blank=True,
        related_name="collections",
        help_text="Curated articles (editorial only).",
    )
    projects = models.ManyToManyField(
        "Project",
        blank=True,
        related_name="collections",
        help_text="Curated projects (editorial only).",
    )
    publications = models.ManyToManyField(
        "Publication",
        blank=True,
        related_name="collections",
        help_text="Curated publications (editorial only).",
    )

    class Meta:
        db_table = "content_collection"
        ordering = ["locale", "slug"]
        indexes = [
            models.Index(
                fields=["locale", "slug", "status"],
                name="collection_loc_slug_st_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["locale", "slug"],
                name="content_collection_unique_locale_slug",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.title} ({self.locale})"


class CourseLevel(models.TextChoices):
    """Course difficulty/level (P9-01)."""

    BEGINNER = "beginner", "Beginner"
    INTERMEDIATE = "intermediate", "Intermediate"
    ADVANCED = "advanced", "Advanced"
    ALL_LEVELS = "all_levels", "All levels"


class CourseFormat(models.TextChoices):
    """Course delivery format (P9-01)."""

    ONLINE = "online", "Online"
    IN_PERSON = "in_person", "In person"
    HYBRID = "hybrid", "Hybrid"
    SELF_PACED = "self_paced", "Self-paced"
    WORKSHOP = "workshop", "Workshop"


class CourseLanguage(models.TextChoices):
    """Language of instruction (orthogonal to CMS locale)."""

    FA = "fa", "Persian"
    EN = "en", "English"
    BILINGUAL = "bilingual", "Bilingual"


class Course(LocalizedContentMixin, LifecycleMixin):
    """Teaching course catalog entity (P9-01) — no LMS/payment."""

    description = models.TextField(blank=True, help_text="Short summary for list cards.")
    body = models.TextField(blank=True, help_text="Full description / syllabus (sanitized HTML).")
    level = models.CharField(
        max_length=32,
        choices=CourseLevel.choices,
        default=CourseLevel.BEGINNER,
    )
    prerequisites = models.TextField(
        blank=True,
        help_text="Prerequisites; leave blank or 'none' for no prerequisites.",
    )
    outcomes = models.TextField(blank=True, help_text="Learning outcomes, one per line or prose.")
    course_format = models.CharField(
        max_length=32,
        choices=CourseFormat.choices,
        default=CourseFormat.ONLINE,
    )
    course_language = models.CharField(
        max_length=32,
        choices=CourseLanguage.choices,
        default=CourseLanguage.EN,
    )
    availability = models.CharField(
        max_length=32,
        choices=Availability.choices,
        default=Availability.PUBLIC,
    )
    license = models.CharField(
        max_length=32,
        choices=License.choices,
        default=License.CC_BY_NC_4,
    )
    last_updated = models.DateField(null=True, blank=True, help_text="Required last-updated date.")
    accessibility_notes = models.TextField(blank=True)
    cover_media = models.ForeignKey(
        "media.Media",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        db_table = "content_course"
        ordering = ["locale", "slug"]
        indexes = [
            models.Index(
                fields=["locale", "slug", "status"],
                name="course_loc_slug_st_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["locale", "slug"],
                name="content_course_unique_locale_slug",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.title} ({self.locale})"

    def allows_public_detail(self) -> bool:
        return self.availability != Availability.PRIVATE

    def public_prerequisites_display(self) -> str:
        text = (self.prerequisites or "").strip()
        return text if text else "none"

    def public_cover(self) -> bool:
        return bool(self.cover_media and self.cover_media.is_active)


class CreativeWorkType(models.TextChoices):
    """Creative work category (P9-02)."""

    DESIGN = "design", "Design"
    VISUAL = "visual", "Visual"
    PHOTOGRAPHY = "photography", "Photography"
    EXPERIMENT = "experiment", "Experiment"
    OTHER = "other", "Other"


class CreativeWork(LocalizedContentMixin, LifecycleMixin):
    """Creative work catalog entity (P9-02) — no student PII."""

    description = models.TextField(blank=True, help_text="Short summary for list cards.")
    body = models.TextField(blank=True, help_text="Full description (sanitized HTML).")
    work_type = models.CharField(
        max_length=32,
        choices=CreativeWorkType.choices,
        default=CreativeWorkType.OTHER,
    )
    creator_name = models.CharField(max_length=200, blank=True, help_text="Creator / author name.")
    creator_role = models.CharField(max_length=200, blank=True, help_text="Role of the creator.")
    creation_date = models.DateField(
        null=True, blank=True, help_text="Creation / publication date."
    )
    license = models.CharField(
        max_length=32,
        choices=License.choices,
        default=License.CC_BY_NC_4,
    )
    access_state = models.CharField(
        max_length=32,
        choices=AccessState.choices,
        default=AccessState.PUBLIC,
    )
    rights_statement = models.TextField(
        blank=True, help_text="Rights / consent disclosure (no student PII)."
    )
    consent_verified = models.BooleanField(
        default=False, help_text="Rights / consent verified; no student PII stored."
    )
    accessibility_notes = models.TextField(blank=True)
    cover_media = models.ForeignKey(
        "media.Media",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        db_table = "content_creative_work"
        ordering = ["locale", "slug"]
        indexes = [
            models.Index(
                fields=["locale", "slug", "status"],
                name="creative_work_loc_slug_st_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["locale", "slug"],
                name="content_creative_work_unique_locale_slug",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.title} ({self.locale})"

    def allows_public_file(self) -> bool:
        return self.access_state == AccessState.PUBLIC

    def public_creator_display(self) -> str:
        return (self.creator_name or "").strip()


class CreativeWorkGalleryImage(models.Model):
    """Ordered gallery image for a CreativeWork (P9-03 keyboard + captions)."""

    creative_work = models.ForeignKey(
        CreativeWork,
        on_delete=models.CASCADE,
        related_name="gallery_images",
    )
    media = models.ForeignKey(
        "media.Media",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    caption = models.CharField(max_length=300, blank=True)
    alt_text = models.TextField(blank=True, help_text="Alt text (required for public projection).")
    ordering = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "content_creative_work_gallery_image"
        ordering = ["ordering", "id"]
        indexes = [
            models.Index(fields=["creative_work", "ordering"], name="cw_gallery_work_order_idx"),
        ]

    def __str__(self) -> str:
        return f"Gallery {self.creative_work_id}:{self.ordering}"

    def is_publicly_projectable(self) -> bool:
        return bool(
            self.media is not None
            and self.media.is_active
            and (self.alt_text or "").strip()
        )


class ContentRevision(models.Model):
    """Immutable content snapshot for restore-as-draft (ADM-4 / DEBT-0005).

    ``entity_key`` matches admin content API keys (``landing``, ``article``, …).
    Snapshots never mutate after create; restore always forces draft and keeps
    a fresh pre-restore snapshot of the live row.
    """

    entity_key = models.CharField(max_length=64, db_index=True)
    object_id = models.PositiveIntegerField(db_index=True)
    snapshot = models.JSONField()
    note = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="content_revisions",
    )

    class Meta:
        db_table = "content_revision"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(
                fields=["entity_key", "object_id", "-created_at"],
                name="content_rev_entity_obj_created",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.entity_key}:{self.object_id}@{self.pk}"


class HomeModuleKey(models.TextChoices):
    """Home page module slots (BK-01; admin write side is AB-02)."""

    IDENTITY = "identity", "Identity"
    GRAPH = "graph", "Graph"
    RESEARCH_FIT = "research-fit", "Research fit"
    JOURNEY = "journey", "Journey"
    PROJECTS = "projects", "Projects"
    PUBLICATIONS = "publications", "Publications"
    PREVIEWS = "previews", "Previews"
    CTA = "cta", "Call to action"


class SelectionMode(models.TextChoices):
    """How a home module selects its content (manual picks vs rules)."""

    MANUAL = "manual", "Manual"
    RULE = "rule", "Rule"
    HYBRID = "hybrid", "Hybrid"


class HomeModuleQuerySet(ContentQuerySet):
    """Home composition public gate: lifecycle-published AND explicitly visible."""

    def visible_for_locale(self, locale: str) -> HomeModuleQuerySet:
        """Published+visible rows for one locale, ordered by ``order``."""
        return self.public().filter(locale=locale, visible=True).order_by("order", "id")


class HomeModuleManager(models.Manager.from_queryset(HomeModuleQuerySet)):
    """Default manager; ``visible_for_locale`` is the public read gate."""


class HomeModule(LifecycleMixin):
    """Per-locale home page composition row (BK-01, LAUNCH-CRITICAL).

    Per-locale-row interpretation of the BK-01 contract: one row per
    ``(locale, key)`` carrying its own ``visible``/``order`` state; the
    ``unique(locale, key)`` constraint forbids a shared row holding both
    locales. Public reads go through
    :meth:`HomeModuleQuerySet.visible_for_locale` only.
    """

    locale = models.CharField(max_length=2, choices=Locale.choices, db_index=True)
    key = models.CharField(max_length=32, choices=HomeModuleKey.choices)
    visible = models.BooleanField(default=False)
    order = models.PositiveIntegerField(default=0)
    selection_mode = models.CharField(
        max_length=32,
        choices=SelectionMode.choices,
        default=SelectionMode.MANUAL,
    )
    provenance_note = models.CharField(max_length=300, blank=True)

    objects = HomeModuleManager()

    class Meta:
        db_table = "content_home_module"
        ordering = ["locale", "order"]
        indexes = [
            models.Index(
                fields=["locale", "status", "visible"],
                name="home_module_loc_st_vis_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["locale", "key"],
                name="content_home_module_unique_locale_key",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.key} ({self.locale})"


_DETAIL_URL_ABS_RE = re.compile(r"^https?://\S+$", re.IGNORECASE)


def validate_detail_url(value: str) -> None:
    """Accept blank, site-relative (single leading '/') or absolute http(s) only.

    Rejects other schemes (``javascript:``, ``ftp://``) and scheme-less
    garbage; ``//host/path`` is cross-origin, not site-relative, so it is
    rejected too.
    """
    url = (value or "").strip()
    if not url:
        return
    if url.startswith("/") and not url.startswith("//"):
        return
    if _DETAIL_URL_ABS_RE.match(url):
        return
    raise ValidationError(
        "detail_url must be blank, site-relative (starting with a single '/') "
        "or an absolute http(s) URL."
    )


class TimelineRecordType(models.TextChoices):
    """Timeline/journey record kinds (BK-02); names mirror existing entities."""

    EXPERIENCE = "experience", "Experience"
    EDUCATION = "education", "Education"
    PROJECT = "project", "Project"
    MILESTONE = "milestone", "Milestone"
    TALK = "talk", "Talk"
    PUBLICATION = "publication", "Publication"


class TimelineRecordQuerySet(ContentQuerySet):
    """Timeline gates mirror HomeModule: locale scope + lifecycle publication."""

    def for_locale(self, locale: str) -> TimelineRecordQuerySet:
        """All rows for one locale in stable display order (preview/admin scope)."""
        return self.filter(locale=locale).order_by("order", "id")

    def published_for_locale(self, locale: str) -> TimelineRecordQuerySet:
        """Published rows for one locale in stable display order (public scope)."""
        return self.public().filter(locale=locale).order_by("order", "id")


class TimelineRecordManager(models.Manager.from_queryset(TimelineRecordQuerySet)):
    """Default manager; ``published_for_locale`` is the public read gate."""


class TimelineRecord(LifecycleMixin):
    """Ordered reusable timeline/journey record (BK-02; admin writes are AB-03).

    Locale-row convention matches HomeModule: each locale carries its own
    records. ``attach`` optionally pins a record to a Profile row (CV
    context); null keeps it standalone. ``order`` repeats freely across
    types — no uniqueness ships here; reorder semantics are AB-03.
    """

    locale = models.CharField(max_length=2, choices=Locale.choices, db_index=True)
    attach = models.ForeignKey(
        Profile,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="timeline_records",
    )
    type = models.CharField(max_length=32, choices=TimelineRecordType.choices)
    label = models.CharField(max_length=200)
    period_label = models.CharField(max_length=100, blank=True)
    body = models.TextField(blank=True)
    role = models.CharField(max_length=300, blank=True)
    weight = models.PositiveSmallIntegerField(default=0)
    detail_url = models.CharField(
        max_length=300,
        blank=True,
        validators=[validate_detail_url],
    )
    order = models.PositiveIntegerField(default=1)

    objects = TimelineRecordManager()

    class Meta:
        db_table = "content_timeline_record"
        ordering = ["locale", "order", "id"]
        indexes = [
            models.Index(
                fields=["locale", "order"],
                name="timeline_record_loc_order_idx",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(order__gte=1),
                name="content_timeline_record_order_gte_1",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.label} ({self.locale})"


class GraphVersionStatus(models.TextChoices):
    """Graph publishing lifecycle (BK-04): draft | active, one active per locale."""

    DRAFT = "draft", "Draft"
    ACTIVE = "active", "Active"


class GraphVersionQuerySet(models.QuerySet):
    """Graph read gates; the active row per locale is the public candidate."""

    def active_for_locale(self, locale: str) -> GraphVersionQuerySet:
        """Active version rows for one locale (public read scope)."""
        return self.filter(locale=locale, status=GraphVersionStatus.ACTIVE)

    def latest_active(self, locale: str) -> GraphVersion | None:
        """Newest active version for a locale, or ``None`` (fail-closed seam)."""
        return self.active_for_locale(locale).order_by("-id").first()


class GraphVersionManager(models.Manager.from_queryset(GraphVersionQuerySet)):
    """Default manager; ``latest_active`` is the public read gate."""


class GraphVersion(models.Model):
    """Per-locale graph version (BK-04 storage; admin writes are AB-06).

    Graph versions are not lifecycle content: ``draft``/``active`` is a
    two-state editor lifecycle, so this model does not mix in
    ``LifecycleMixin``. At most one ``active`` row per locale is enforced by
    a conditional unique constraint (partial unique index on Postgres).
    Public reads (BK-05) go through :meth:`GraphVersionQuerySet.latest_active`
    only.
    """

    locale = models.CharField(max_length=2, choices=Locale.choices, db_index=True)
    status = models.CharField(
        max_length=20,
        choices=GraphVersionStatus.choices,
        default=GraphVersionStatus.DRAFT,
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = GraphVersionManager()

    class Meta:
        db_table = "content_graph_version"
        ordering = ["locale", "-id"]
        indexes = [
            models.Index(fields=["locale", "status"], name="graph_version_loc_st_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["locale"],
                condition=models.Q(status=GraphVersionStatus.ACTIVE),
                name="content_graph_version_unique_active_locale",
            ),
        ]

    def __str__(self) -> str:
        return f"Graph {self.locale} #{self.pk} ({self.status})"

    def clean(self) -> None:
        """Validate the whole version payload: edge rules and related targets."""
        super().clean()
        if self.pk is None:
            return
        problems: list[ValidationError] = []
        for edge in self.edges.select_related("source", "target"):
            try:
                edge.clean()
            except ValidationError as exc:
                problems.append(exc)
        related_rows = GraphNodeRelated.objects.filter(
            node__version_id=self.pk
        ).select_related("content_type")
        for row in related_rows:
            try:
                row.clean()
            except ValidationError as exc:
                problems.append(exc)
        if problems:
            raise ValidationError(problems)


class GraphGroup(models.Model):
    """Editor-side node grouping within one graph version (MASTER-SPEC §8).

    Authoring structure only — not part of the phase-1 public payload
    (``GraphNodePublic`` maps no group key today).
    """

    version = models.ForeignKey(
        GraphVersion,
        on_delete=models.CASCADE,
        related_name="groups",
    )
    label = models.CharField(max_length=200)
    color_role = models.CharField(max_length=100)

    class Meta:
        db_table = "content_graph_group"
        ordering = ["id"]

    def __str__(self) -> str:
        return f"{self.label} ({self.version_id})"


class GraphNode(models.Model):
    """Node row of one graph version (BK-04).

    Columns map 1:1 to the ``GraphNodePublic`` target (AGENT-COORDINATION
    §4): ``node_id``->id, ``type``, ``label``, ``summary`` (blank -> absent),
    ``accessible_label``->accessibleLabel, ``color_role``->colorRole,
    ``icon_role``->iconRole, ``weight``, ``pos_x/pos_y/pos_z``->position
    (nullable until an editor pins coordinates). camelCase naming happens at
    the API layer (BK-05), never in storage.
    """

    version = models.ForeignKey(
        GraphVersion,
        on_delete=models.CASCADE,
        related_name="nodes",
    )
    node_id = models.CharField(max_length=100)
    label = models.CharField(max_length=200)
    type = models.CharField(max_length=100)
    summary = models.TextField(blank=True)
    accessible_label = models.CharField(max_length=300, blank=True)
    color_role = models.CharField(max_length=100)
    icon_role = models.CharField(max_length=100)
    weight = models.PositiveSmallIntegerField(default=0)
    pos_x = models.FloatField(null=True, blank=True)
    pos_y = models.FloatField(null=True, blank=True)
    pos_z = models.FloatField(null=True, blank=True)
    group = models.ForeignKey(
        GraphGroup,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="members",
    )

    class Meta:
        db_table = "content_graph_node"
        ordering = ["version", "node_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["version", "node_id"],
                name="content_graph_node_unique_version_node_id",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.label} ({self.node_id})"


class GraphNodeRelated(models.Model):
    """Generic record reference from a node (``GraphNodePublic.relatedRecords``).

    ``family`` is not stored: the API layer derives it from ``content_type``
    (single source of truth). Dangling targets (deleted rows) are allowed —
    validation only rejects references to existing but unpublished objects
    ("published-or-null", BK-04).
    """

    node = models.ForeignKey(
        GraphNode,
        on_delete=models.CASCADE,
        related_name="related_records",
    )
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField(db_index=True)
    content_object = GenericForeignKey("content_type", "object_id")

    class Meta:
        db_table = "content_graph_node_related"
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(
                fields=["node", "content_type", "object_id"],
                name="content_graph_node_related_unique_target",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.node_id} <- {self.content_type_id}:{self.object_id}"

    def clean(self) -> None:
        """Reject references to existing objects that are not publicly readable."""
        super().clean()
        target = self.content_object
        if target is None:
            return
        public = getattr(type(target).objects, "public", None)
        if public is None or not public().filter(pk=target.pk).exists():
            raise ValidationError(
                {
                    "object_id": (
                        "Related records must reference a published object or be null."
                    )
                }
            )


class GraphEdge(models.Model):
    """Directed/undirected edge within one graph version (BK-04).

    Maps to ``GraphEdgePublic`` (AGENT-COORDINATION §4): ``source``/``target``
    are :class:`GraphNode` rows serialized as their stable ``node_id``
    strings, ``relation_type``->relationType, ``explanation`` blank -> absent
    at the API layer. The stable public ``id`` string is composed by BK-05.
    """

    version = models.ForeignKey(
        GraphVersion,
        on_delete=models.CASCADE,
        related_name="edges",
    )
    source = models.ForeignKey(
        GraphNode,
        on_delete=models.CASCADE,
        related_name="outgoing_edges",
    )
    target = models.ForeignKey(
        GraphNode,
        on_delete=models.CASCADE,
        related_name="incoming_edges",
    )
    relation_type = models.CharField(max_length=100)
    directed = models.BooleanField(default=True)
    weight = models.PositiveSmallIntegerField(default=0)
    explanation = models.TextField(blank=True)

    class Meta:
        db_table = "content_graph_edge"
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(
                fields=["version", "source", "target", "relation_type"],
                name="content_graph_edge_unique_version_pair_rel",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.relation_type}: {self.source_id}->{self.target_id}"

    def clean(self) -> None:
        """Same-version endpoints; undirected pairs must not duplicate in reverse."""
        super().clean()
        if self.version_id is None or self.source_id is None or self.target_id is None:
            return
        errors: dict[str, str] = {}
        if (
            self.source.version_id != self.version_id
            or self.target.version_id != self.version_id
        ):
            errors["version"] = "Edge endpoints must belong to the same graph version."
        if not errors and not self.directed:
            mirror = GraphEdge.objects.filter(
                version_id=self.version_id,
                source_id=self.target_id,
                target_id=self.source_id,
                relation_type=self.relation_type,
            )
            if self.pk:
                mirror = mirror.exclude(pk=self.pk)
            if mirror.exists():
                errors["source"] = (
                    "An undirected edge must not duplicate an existing reversed pair."
                )
        if errors:
            raise ValidationError(errors)
