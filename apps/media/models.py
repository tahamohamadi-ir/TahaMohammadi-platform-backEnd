"""Media library — signature-validated uploads, private-by-default visibility.

A record is NOT public until ``is_active`` is set by an editor; ``is_active``
IS the public flag. Internal/archived records are simply ``is_active=False``.
"""

from django.db import models
from django.db.models import Q

from apps.media.sniff import sniff_mime
from apps.media.storage import media_upload_path
from apps.media.validators import validate_file_size, validate_file_type


class MediaLicense(models.Model):
    """Editor-curated license vocabulary referenced by Media rows."""

    name = models.CharField(max_length=255, unique=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class MediaQuerySet(models.QuerySet):
    """QuerySet with the public-media projection rule."""

    def active_public(self):
        """Only records explicitly marked active (the private-default rule)."""
        return self.filter(is_active=True)


class MediaManager(models.Manager):
    def get_queryset(self):
        return MediaQuerySet(self.model, using=self._db)

    def active_public(self):
        return self.get_queryset().active_public()


class Media(models.Model):
    file = models.FileField(
        upload_to=media_upload_path,
        validators=[validate_file_type, validate_file_size],
    )
    title = models.CharField(max_length=255)
    alt_text = models.CharField(max_length=255, blank=True)
    alt_text_fa = models.CharField(max_length=255, blank=True, default="", db_default="")
    alt_text_en = models.CharField(max_length=255, blank=True, default="", db_default="")
    caption_fa = models.CharField(max_length=300, blank=True, default="", db_default="")
    caption_en = models.CharField(max_length=300, blank=True, default="", db_default="")
    # Focal point as percent of image width/height (0..100); null = center.
    # Range enforced DB-side below; API-level validation is AB-04's concern.
    focal_x = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
    )
    focal_y = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
    )
    rights_statement_fa = models.TextField(blank=True, default="", db_default="")
    rights_statement_en = models.TextField(blank=True, default="", db_default="")
    license = models.ForeignKey(
        MediaLicense,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="media",
    )
    mime = models.CharField(max_length=100, editable=False, blank=True)
    size = models.PositiveBigIntegerField(editable=False, default=0)
    is_active = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = MediaManager()

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Media"
        verbose_name_plural = "Media"
        constraints = [
            models.CheckConstraint(
                condition=Q(focal_x__isnull=True) | Q(focal_x__gte=0, focal_x__lte=100),
                name="media_media_focal_x_percent_range",
            ),
            models.CheckConstraint(
                condition=Q(focal_y__isnull=True) | Q(focal_y__gte=0, focal_y__lte=100),
                name="media_media_focal_y_percent_range",
            ),
        ]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        """Record mime/size from the actual file content, never from client metadata."""
        if self.file and self.file.name:
            self.file.seek(0)
            self.mime = sniff_mime(self.file) or ""
            self.file.seek(0)
            self.size = self.file.size
        super().save(*args, **kwargs)
