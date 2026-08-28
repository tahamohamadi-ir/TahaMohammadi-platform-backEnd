"""Page composition data layer (ADR-0026, ADM-3).

A ``CompositionPage`` is a locale-specific page composed of ordered
``CompositionSection`` rows, each carrying an ordered list of
``CompositionBlock`` rows. Block payloads are validated against the typed
catalog in ``blocks.py`` (fail-closed) before they are persisted.

``kind=landing`` is the bilingual homepage/landing catalog. ``kind=story`` is
a single-locale document attached to a content entity (blog first).
"""

from django.db import models


class CompositionPage(models.Model):
    KIND_LANDING = "landing"
    KIND_STORY = "story"
    KIND_CHOICES = [
        (KIND_LANDING, "Landing"),
        (KIND_STORY, "Story"),
    ]

    key = models.SlugField(max_length=120, unique=True)
    kind = models.CharField(
        max_length=16,
        choices=KIND_CHOICES,
        default=KIND_LANDING,
        db_index=True,
    )
    locale = models.CharField(
        max_length=2,
        choices=[("fa", "Persian"), ("en", "English")],
        db_index=True,
    )
    title = models.CharField(max_length=200)
    status = models.CharField(
        max_length=20,
        choices=[
            ("draft", "Draft"),
            ("review", "Review"),
            ("published", "Published"),
            ("archived", "Archived"),
        ],
        default="draft",
        db_index=True,
    )
    published_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        indexes = [models.Index(fields=["locale", "status"])]

    def __str__(self):
        return self.title


class CompositionSection(models.Model):
    page = models.ForeignKey(
        CompositionPage,
        on_delete=models.CASCADE,
        related_name="sections",
    )
    position = models.PositiveIntegerField(default=0)
    layout = models.CharField(
        max_length=8,
        choices=[("1col", "1 col"), ("2col", "2 col"), ("3col", "3 col")],
        default="1col",
    )
    ratio = models.CharField(max_length=16, blank=True, default="")
    enabled = models.BooleanField(default=True)

    class Meta:
        ordering = ["position"]
        constraints = [
            models.UniqueConstraint(fields=["page", "position"], name="uniq_section_position"),
        ]

    def __str__(self):
        return f"{self.page.key} [{self.position}] {self.layout}"


class CompositionBlock(models.Model):
    section = models.ForeignKey(
        CompositionSection,
        on_delete=models.CASCADE,
        related_name="blocks",
    )
    position = models.PositiveIntegerField(default=0)
    block_type = models.CharField(max_length=32)
    settings = models.JSONField(default=dict, blank=True)
    enabled = models.BooleanField(default=True)

    class Meta:
        ordering = ["position"]
        constraints = [
            models.UniqueConstraint(fields=["section", "position"], name="uniq_block_position"),
        ]

    def __str__(self):
        return f"{self.section_id} [{self.position}] {self.block_type}"
