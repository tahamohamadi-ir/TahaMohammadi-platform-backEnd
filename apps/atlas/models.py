"""Atlas domain models — taxonomy (spec §5.3/§6) and its lifecycle rules.

Taxonomy keys are lowercase ``[a-z0-9-]`` slug identifiers; ``~`` is reserved
because it separates the parts of a composed relation key. A key is immutable
once a node or relation uses the row, and in-use taxonomy rows cannot be
deleted — the ``PROTECT`` foreign keys that carry that deletion protection
arrive with the node and relation models in the migrations that follow this one.
"""

from __future__ import annotations

import re

from django.apps import apps as django_apps
from django.core.exceptions import ValidationError
from django.db import models

#: Taxonomy key grammar (spec §5.3): lowercase letters, digits and hyphens only.
#: ``~`` is reserved — it separates the parts of a composed relation key — so a
#: key can never contain it. ``\Z`` (not ``$``) rejects a trailing newline: a key
#: is a persisted identifier, never line-oriented text.
TAXONOMY_KEY_RE = re.compile(r"^[a-z0-9-]+\Z")


def validate_taxonomy_key(value: str) -> None:
    """Reject anything outside ``[a-z0-9-]`` (notably the reserved ``~``)."""
    if not TAXONOMY_KEY_RE.fullmatch(str(value)):
        raise ValidationError(
            "A taxonomy key must match ^[a-z0-9-]+$; '~' is reserved for composed "
            "relation keys."
        )


class SEMANTIC_ROLES(models.TextChoices):
    """Semantic role vocabulary shared by node types and relation types (spec §5.3)."""

    ANCHOR = "anchor", "Anchor"
    AREA = "area", "Area"
    RECORD = "record", "Record"
    UTILITY = "utility", "Utility"


class VISUAL_ROLES(models.TextChoices):
    """Visual/material presentation profile of a node type (spec §5.3/§13.4)."""

    ANCHOR = "anchor", "Anchor"
    DOMAIN = "domain", "Domain"
    RECORD = "record", "Record"
    FINE = "fine", "Fine"


class SELF_LOOP_POLICIES(models.TextChoices):
    """Whether a relation type may point a node back at itself (spec §5.3)."""

    FORBID = "forbid", "Forbid"
    ALLOW = "allow", "Allow"


class CANONICAL_SOURCES(models.TextChoices):
    """Which published CMS model a node of this type must reference (spec §5.3)."""

    RESEARCH_TOPIC = "research_topic", "Research topic"
    PROJECT = "project", "Project"
    PUBLICATION = "publication", "Publication"
    METHOD = "method", "Method"
    TECHNOLOGY = "technology", "Technology"
    PROFILE = "profile", "Profile"
    NONE = "none", "None"


def _referencing_model(app_label: str, model_name: str) -> type[models.Model] | None:
    """Return a model added by a later migration, or ``None`` while it is absent.

    ``AtlasNode`` and ``AtlasRelation`` arrive with the migrations that follow
    this one, so until then nothing can reference a taxonomy row and the "key is
    immutable once used" rule has nothing to check; once the model exists the
    guard activates without further change.
    """
    try:
        return django_apps.get_model(app_label, model_name)
    except LookupError:
        return None


class TaxonomyKeyMixin(models.Model):
    """Shared taxonomy lifecycle rule: ``key`` is immutable once used (spec §5.3)."""

    #: Field-keyed message raised when a used row's ``key`` is changed.
    key_immutable_message = "A taxonomy key is immutable once it is used."

    class Meta:
        abstract = True

    def save(self, *args, **kwargs) -> None:
        self._assert_key_immutable()
        super().save(*args, **kwargs)

    def _assert_key_immutable(self) -> None:
        if not self.pk or not self._referencing_rows_exist():
            return
        original = type(self).objects.only("key").get(pk=self.pk)
        if original.key != self.key:
            raise ValidationError({"key": self.key_immutable_message})

    def _referencing_rows_exist(self) -> bool:
        """Whether any node/relation row references this taxonomy row."""
        raise NotImplementedError


class AtlasNodeType(TaxonomyKeyMixin):
    """Admin-managed node-type taxonomy — one row per node type (spec §5.3/§6.1)."""

    key_immutable_message = "A node type key is immutable once a node uses it."

    key = models.SlugField(max_length=64, unique=True, validators=[validate_taxonomy_key])
    label_en = models.CharField(max_length=120)
    label_fa = models.CharField(max_length=120)
    description_en = models.TextField(blank=True)
    description_fa = models.TextField(blank=True)
    semantic_role = models.CharField(max_length=16, choices=SEMANTIC_ROLES.choices)
    visual_role = models.CharField(max_length=16, choices=VISUAL_ROLES.choices)
    default_importance = models.PositiveSmallIntegerField(default=50)
    allow_as_root = models.BooleanField(default=False)
    allow_children = models.BooleanField(default=True)
    canonical_source = models.CharField(
        max_length=32, choices=CANONICAL_SOURCES.choices, default=CANONICAL_SOURCES.NONE
    )
    filter_visible = models.BooleanField(default=True)
    active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "atlas_node_type"
        ordering = ["sort_order", "key"]

    def __str__(self) -> str:
        return f"{self.label_en} ({self.key})"

    def _referencing_rows_exist(self) -> bool:
        node_model = _referencing_model("atlas", "AtlasNode")
        if node_model is None:
            return False
        return node_model._default_manager.filter(node_type_id=self.pk).exists()


class AtlasRelationType(TaxonomyKeyMixin):
    """Admin-managed relation-type taxonomy (spec §5.3/§6.2)."""

    key_immutable_message = "A relation type key is immutable once a relation uses it."

    key = models.SlugField(max_length=64, unique=True, validators=[validate_taxonomy_key])
    label_en = models.CharField(max_length=120)
    label_fa = models.CharField(max_length=120)
    inverse_label_en = models.CharField(max_length=120)
    inverse_label_fa = models.CharField(max_length=120)
    description_en = models.TextField(blank=True)
    description_fa = models.TextField(blank=True)
    directed_default = models.BooleanField(default=True)
    overridable_direction = models.BooleanField(default=False)
    semantic_role = models.CharField(max_length=16, choices=SEMANTIC_ROLES.choices)
    hierarchy_role = models.BooleanField(default=False)
    default_weight = models.PositiveSmallIntegerField(default=1)
    visual_priority = models.PositiveSmallIntegerField(default=50)
    allowed_source_types = models.ManyToManyField(AtlasNodeType, blank=True, related_name="+")
    allowed_target_types = models.ManyToManyField(AtlasNodeType, blank=True, related_name="+")
    self_loop_policy = models.CharField(
        max_length=16, choices=SELF_LOOP_POLICIES.choices, default=SELF_LOOP_POLICIES.FORBID
    )
    active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "atlas_relation_type"
        ordering = ["sort_order", "key"]

    def __str__(self) -> str:
        return f"{self.label_en} ({self.key})"

    def _referencing_rows_exist(self) -> bool:
        relation_model = _referencing_model("atlas", "AtlasRelation")
        if relation_model is None:
            return False
        return relation_model._default_manager.filter(relation_type_id=self.pk).exists()
