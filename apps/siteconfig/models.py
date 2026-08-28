"""Site-level customization models (ADR-0026, ADM-5)."""

from django.db import models


class SiteSettings(models.Model):
    """Singleton of site-wide presentation + SEO defaults.

    ``nav_links`` is a list of ``{label, href, locale}`` dicts used by the
    admin SPA to render the public navigation; no nav link is resolved or
    validated against public routes here (presentation owns that).

    ``current_cv_media`` / ``current_resume_media`` enforce the one-current-
    document policy (§14 F5): at most one active academic CV and one industry
    resume from the media library, projected to Astro CV downloads.
    """

    brand_name = models.CharField(max_length=200, default="Taha Mohammadi")
    tagline = models.CharField(max_length=500, blank=True, default="")
    footer_text = models.TextField(blank=True, default="")
    primary_color = models.CharField(max_length=7, default="#1f2937")
    nav_links = models.JSONField(default=list, blank=True)
    seo_default_title = models.CharField(max_length=200, blank=True, default="")
    seo_default_description = models.TextField(blank=True, default="")
    # Public contact block (board A10 decision 2026-08-23: contact path
    # reopened with a non-persistent email form — no inbox persistence).
    contact_email = models.CharField(max_length=254, blank=True, default="")
    contact_phone = models.CharField(max_length=32, blank=True, default="")
    contact_phone_intl = models.CharField(max_length=32, blank=True, default="")
    contact_location = models.CharField(max_length=200, blank=True, default="")
    contact_linkedin = models.CharField(max_length=300, blank=True, default="")
    contact_orcid = models.CharField(max_length=300, blank=True, default="")
    contact_employer = models.CharField(max_length=200, blank=True, default="")
    contact_employer_url = models.CharField(max_length=300, blank=True, default="")
    contact_form_enabled = models.BooleanField(default=False)
    current_cv_media = models.ForeignKey(
        "media.Media",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    current_resume_media = models.ForeignKey(
        "media.Media",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    updated_at = models.DateTimeField(auto_now=True)
    site_key = models.CharField(max_length=20, default="default", unique=True)

    class Meta:
        verbose_name = "Site settings"
        verbose_name_plural = "Site settings"

    def __str__(self) -> str:
        return self.brand_name

    @classmethod
    def get_singleton(cls):
        # Atomic: the unique site_key guarantees at most one row even under
        # concurrent requests.
        return cls.objects.get_or_create(site_key="default")[0]


class FeaturedItem(models.Model):
    """Time-window spotlight linking a content entity row to the homepage."""

    title = models.CharField(max_length=200)
    target_entity = models.CharField(max_length=40)
    target_slug = models.CharField(max_length=200)
    locale = models.CharField(
        max_length=2,
        choices=[("fa", "Persian"), ("en", "English")],
        default="fa",
    )
    start_at = models.DateTimeField()
    end_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-start_at"]

    def __str__(self) -> str:
        return self.title
