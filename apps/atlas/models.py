"""Atlas domain models — taxonomy (spec §5.3/§6) and its lifecycle rules.

Taxonomy keys are lowercase ``[a-z0-9-]`` slug identifiers; ``~`` is reserved
because it separates the parts of a composed relation key. A key is immutable
once a node or relation uses the row, and in-use taxonomy rows cannot be
deleted — the ``PROTECT`` foreign key from ``AtlasNode.node_type`` carries that
deletion protection.

``AtlasVersion`` is one topology (both locales), ``AtlasNode`` a locale-neutral
member of it, and ``AtlasNodeTranslation`` its per-locale overrides. Layout is
computed once per revision and stored on the version as
``{"<node public key>": [x, y, z], ...}``; consumers project it, never
re-simulate it (spec §12.1).

``Meta.ordering`` is unspecified by the plan in places; wherever it is, the
choice here is deterministic and therefore testable — ``sort_order``/``key`` for
the taxonomies (Task 5 precedent), ``-id`` for versions (the plan fixes it), and
``version, sort_order, public_key`` for nodes (the plan fixes it).
"""

from __future__ import annotations

import re

from django.apps import apps as django_apps
from django.core.exceptions import ValidationError
from django.db import models

from apps.atlas.keys import PUBLIC_KEY_RE, is_valid_public_key
from apps.content.models import Locale

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
        # `_base_manager`, not `_default_manager`: a default manager that later
        # gains a filter would under-count in-use rows and silently disable the
        # immutability rule (the same trap applies to every in-use check below).
        return node_model._base_manager.filter(node_type_id=self.pk).exists()


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
        # `_base_manager` for the same reason as the node-type side above.
        return relation_model._base_manager.filter(relation_type_id=self.pk).exists()


class VERSION_STATUSES(models.TextChoices):
    """Lifecycle of an Atlas version (spec §5): exactly one `active` at a time."""

    DRAFT = "draft", "Draft"
    ACTIVE = "active", "Active"
    ARCHIVED = "archived", "Archived"


class MOBILE_OVERVIEW_PRIORITIES(models.TextChoices):
    """Initial compact-overview role of a node (spec §5/§14.2)."""

    AUTO = "auto", "Auto"
    FEATURED = "featured", "Featured"
    HIDDEN = "hidden", "Hidden"


class AtlasVersion(models.Model):
    """One topology, both locales (spec §5) — a version is never locale-scoped."""

    status = models.CharField(
        max_length=16, choices=VERSION_STATUSES.choices, default=VERSION_STATUSES.DRAFT
    )
    label = models.CharField(max_length=120, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    created_from = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="derived_versions"
    )
    layout_revision = models.PositiveIntegerField(default=0)
    #: ``{"<node public key>": [x, y, z], ...}`` with 3-decimal scene
    #: coordinates — the plan fixes this storage shape (spec §12.1 fixes the
    #: behaviour: computed once per revision, then served).
    layout = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "atlas_version"
        ordering = ["-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["status"],
                condition=models.Q(status="active"),
                name="atlas_version_unique_active",
            ),
        ]

    def __str__(self) -> str:
        label = self.label or f"v{self.pk}"
        return f"{label} ({self.status})"


class AtlasNode(models.Model):
    """A locale-neutral topology node of one version (spec §5)."""

    version = models.ForeignKey(AtlasVersion, on_delete=models.CASCADE, related_name="nodes")
    #: ``<node-type-key>-<8 hex>``, assigned at creation, immutable thereafter,
    #: unique globally so a key never means two things. The uniqueness is a Meta
    #: constraint (plan shape), not ``unique=True`` on the field.
    public_key = models.SlugField(max_length=80)
    node_type = models.ForeignKey(AtlasNodeType, on_delete=models.PROTECT, related_name="nodes")
    canonical_model = models.CharField(
        max_length=32, choices=CANONICAL_SOURCES.choices, default=CANONICAL_SOURCES.NONE
    )
    canonical_translation_key = models.UUIDField(null=True, blank=True, db_index=True)
    importance = models.PositiveSmallIntegerField(default=50)
    visible = models.BooleanField(default=True)
    #: The one compact-overview field (spelled ``mobileOverviewPriority`` on the
    #: wire). ``auto``/``featured``/``hidden``; there is no ``mobile_overview``
    #: alias and no separate ``pinned`` flag anywhere.
    mobile_overview_priority = models.CharField(
        max_length=16,
        choices=MOBILE_OVERVIEW_PRIORITIES.choices,
        default=MOBILE_OVERVIEW_PRIORITIES.AUTO,
    )
    #: Optional layout pin override; ``pin_x``/``pin_y`` are set together or not
    #: at all, and ``pin_z`` needs them (a lone coordinate pins nothing).
    pin_x = models.FloatField(null=True, blank=True)
    pin_y = models.FloatField(null=True, blank=True)
    pin_z = models.FloatField(null=True, blank=True)
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "atlas_node"
        ordering = ["version", "sort_order", "public_key"]
        constraints = [
            models.UniqueConstraint(fields=["public_key"], name="atlas_node_unique_public_key"),
            models.UniqueConstraint(
                fields=["version", "node_type", "canonical_translation_key"],
                condition=models.Q(canonical_translation_key__isnull=False),
                name="atlas_node_unique_canonical_per_version",
            ),
        ]
        indexes = [
            models.Index(fields=["version", "visible"], name="atlas_node_version_visible_idx"),
            models.Index(
                fields=["canonical_model", "canonical_translation_key"],
                name="atlas_node_canonical_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.public_key} ({self.node_type.key})"

    def save(self, *args, **kwargs) -> None:
        """Copy the node type's canonical source into the node (spec §5).

        The copy also makes the ``clean()`` equality rule below satisfiable for
        a caller that only knows the node's *type*: ``none`` is not a value a
        node of a canonical type can hold, because layout and projection would
        then disagree about whether the node has a canonical record.
        """
        if self.node_type_id and self.canonical_model in (None, "", CANONICAL_SOURCES.NONE):
            self.canonical_model = self.node_type.canonical_source
        super().save(*args, **kwargs)

    def clean(self) -> None:
        """Model-level rules of spec §5 / plan Task 6 (the validation layer adds the rest)."""
        super().clean()
        errors: dict[str, str] = {}
        if self.public_key and "~" in self.public_key:
            errors["public_key"] = (
                "A node key must not contain '~': that separator is reserved for relation "
                "keys, so a tilde-bearing key would be read as a relation key."
            )
        elif self.public_key and not is_valid_public_key(self.public_key):
            errors["public_key"] = (
                f"A public key must match the Atlas key grammar ({PUBLIC_KEY_RE.pattern})."
            )
        if self.node_type_id and self.canonical_model != self.node_type.canonical_source:
            errors["canonical_model"] = (
                "canonical_model must equal the node type's canonical_source "
                f"('{self.node_type.canonical_source}')."
            )
        if (self.pin_x is None) != (self.pin_y is None):
            errors["pin_x"] = "pin_x and pin_y must be set together or not at all."
        elif self.pin_x is None and self.pin_z is not None:
            errors["pin_z"] = "pin_z requires pin_x and pin_y."
        if self.mobile_overview_priority not in MOBILE_OVERVIEW_PRIORITIES.values:
            errors["mobile_overview_priority"] = (
                "mobile_overview_priority must be one of: "
                f"{', '.join(MOBILE_OVERVIEW_PRIORITIES.values)}."
            )
        if errors:
            raise ValidationError(errors)


class AtlasNodeTranslation(models.Model):
    """Per-locale overrides of a node's projected label/summary (spec §5)."""

    node = models.ForeignKey(AtlasNode, on_delete=models.CASCADE, related_name="translations")
    locale = models.CharField(max_length=2, choices=Locale.choices)
    label_override = models.CharField(max_length=200, blank=True)
    summary_override = models.TextField(blank=True)
    #: Blank means "the resolved label" (spec §5); the wire carries it as
    #: ``accessibleLabel``.
    accessible_label_override = models.CharField(max_length=300, blank=True)
    #: Optional search aliases/synonyms: a list of non-empty strings, each at
    #: most 120 characters (spec §5).
    aliases = models.JSONField(default=list, blank=True)

    class Meta:
        db_table = "atlas_node_translation"
        ordering = ["node", "locale"]
        constraints = [
            models.UniqueConstraint(
                fields=["node", "locale"], name="atlas_node_translation_unique_locale"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.node.public_key} ({self.locale})"
