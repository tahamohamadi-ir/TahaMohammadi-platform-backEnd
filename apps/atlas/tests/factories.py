"""Shared ORM builders and fixtures for the Atlas test suite (plan Tasks 6–18).

One home for the builders the plan's Task 6/7/8 test snippets call by name —
``_node_type``, ``_relation_type``, ``_node``, ``_relation``, ``_group`` and the
two translation builders — plus, from Task 8 (ruling R5), the canonical
``seed_pairs`` fixture and the ``atlas_v1`` / ``atlas_active_version`` /
``atlas_two_versions`` fixture family that Tasks 9–16 consume. The scale family
(``atlas_scale_fixture``) arrives with Task 11 — the first task whose tests need
it (its declared home is Task 18) — sized to the composition Task 18 declares,
so that task extends it with projection data instead of rewriting it. Nothing
here is production code: a builder exists so a test states only the fields the
behaviour under test depends on.

Three rules these builders encode:

* a caller that does not care about identity still gets a valid, unique
  ``public_key`` (``new_node_key``/``new_group_key``) — unique per version
  (ruling R9: a clone reuses its source's keys);
* a node's ``canonical_model`` and ``importance`` agree with its node type
  (spec §5: the type's ``canonical_source``/``default_importance`` are copied
  into the node at creation), so ``full_clean()`` on a factory-built node
  reports only the error under test;
* a relation's ``directed`` and ``weight`` start at the relation type's
  ``directed_default``/``default_weight`` (spec §5.3: "initialised from"), which
  is the authoring layer's job — the model itself never rewrites them.

**This module is not a conftest.** The fixtures below are imported by name::

    from apps.atlas.tests.factories import atlas_v1, seed_pairs

so a consumer always states which fixture family it uses (and a missing import
fails as a plain unknown-fixture error instead of a silent skip).

The ``atlas_v1`` family bundles (ruling R5) expose, beyond ``version``:

* ``atlas_v1`` — the draft mirror of the current published graph: 4 visible core
  nodes (identity + 3 research areas), 3 ``research-focus`` relations, a pinned
  node (``pinned_node``/``pinned_key``/``pinned_x``/``pinned_y``), the two
  one-sided canonical nodes ``fa_missing_node`` (resolves EN only) and
  ``en_missing_node`` (resolves FA only) — each an endpoint of a visible
  relation, so hiding one produces both the parity and the dangling-visibility
  effect — plus ``override(node, *, locale, label, …)``,
  ``add_draft_only_node()``/``draft_only_key`` and ``corrupt_layout(...)``, and
  (plan Task 9) the two relation-rule offenders ``bad_relation`` (outside the
  ``research-focus`` allowed pair) and ``direction_relation`` (a ``related-to``
  relation whose ``directed`` contradicts the type's ``directed_default``), with
  ``relation_type`` naming the type the retirement rule is proven on;
* ``atlas_active_version`` — the same mirror as an *active* version (no
  one-sided nodes, so a servable ``nodeCount == 4``), with ``pk`` delegating to
  the active version and a companion draft version for ``add_draft_only_node()``;
* ``atlas_two_versions`` — ``previous_active`` + ``draft`` and
  ``break_/restore_{en,fa}_projection()``, which unpublish/republish one
  canonical row of the draft in exactly one locale.

Plan Task 10 adds the ``atlas_dag`` family on top of the same builders: a legal
multi-parent hierarchy (``area → {area-a, area-b} → area-c``) whose four nodes are
Atlas-only *structural* nodes (``canonical_source="none"``), so the parity gate
levels them through per-locale overrides instead of canonical rows, plus the two
mutations its tests make — ``close_cycle()`` (a hierarchy back edge, which is a
``HIERARCHY_CYCLE``) and ``add_related_cycle()`` (a *non*-hierarchy cycle, which
spec §5.6 permits). Its node keys are fixed and ascending, so the DFS order the
plan pins (``public_key`` order) is a known order rather than eight random hex
characters.

Plan Task 11 adds ``atlas_scale_fixture`` for the layout engine and for the
§19.2 measurement shape: 72 visible nodes (1 identity anchor, 12 research areas,
24 projects, 20 publications, 8 methods, 7 technologies), 136 relations (a
three-level ``parent-of`` hierarchy plus ``uses`` / ``implements`` / ``cites`` /
``related-to``), 6 groups with 36 memberships, four pinned nodes and fixed
``<type>-<8 hex>`` keys — including the plan's ``project-2b3c4d5e``, which the
fixture deliberately leaves *unpinned* so a test can pin it. ``version(...)``
builds a deterministic variant of another size (Task 12's ``visible_nodes=101``,
the timing test's 80 / 150). It creates **no** canonical rows: the layout engine
reads none, and Task 18 adds the projection data on top of these types.

**The two active-version bundles are mutually exclusive in one test.** Both
``atlas_active_version`` and ``atlas_two_versions`` create a version with
``status="active"``; requesting both fixtures in one test makes the second
creation fail at setup with ``IntegrityError: UNIQUE constraint failed:
atlas_version.status`` — that is the spec's one-active-version rule (the
partial unique index ``atlas_version_unique_active``), not a fixture bug. A
test that needs both shapes must build the second version itself, as
``_version()`` documents.

Every bundle writes a placeholder ``layout`` (all-zero coordinates) for each of
its visible nodes, because the endpoints of Tasks 15/16 fail closed on a missing
position: real coordinates are Task 11's engine and Task 13's
``recompute_layout``. ``atlas_scale_fixture`` writes the same placeholder (its
own tests then run the engine over it).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from django.utils.dateparse import parse_datetime

from apps.atlas.canonical import CANONICAL_SOURCES as _CANONICAL_MODEL
from apps.atlas.keys import new_group_key, new_node_key
from apps.atlas.models import (
    AtlasGroup,
    AtlasGroupMembership,
    AtlasGroupTranslation,
    AtlasNode,
    AtlasNodeTranslation,
    AtlasNodeType,
    AtlasRelation,
    AtlasRelationTranslation,
    AtlasRelationType,
    AtlasVersion,
)
from apps.content.models import (
    Method,
    Profile,
    Project,
    Publication,
    ResearchTopic,
    Technology,
)

#: The public builder/fixture surface of this module. Kept explicit so the R5
#: fixture family is one import name away from any test module and so a stray
#: helper is visibly *not* part of it (an entry that stops existing fails
#: ``test_r5_fixture_functions_are_declared_once_in_the_one_builders_module``).
__all__ = [
    "CANONICAL_PAIR_DEFAULTS",
    "DAG_EDGES",
    "DAG_KEYS",
    "DAG_NODE_NAMES",
    "DEFAULT_VERSION_LABEL",
    "NODE_TYPE_DEFAULTS",
    "PUBLISHED_AT",
    "R5_FIXTURE_NAMES",
    "RELATION_TYPE_DEFAULTS",
    "SCALE_COMPOSITION",
    "SCALE_FIRST_PROJECT_KEY",
    "SCALE_GROUP_TOTAL",
    "SCALE_NODE_TOTAL",
    "SCALE_PINNED_TOTAL",
    "SCALE_PINS",
    "SCALE_RELATION_TOTAL",
    "AtlasActiveVersionFixture",
    "AtlasDagFixture",
    "AtlasScaleFixture",
    "AtlasTwoVersionsFixture",
    "AtlasV1Fixture",
    "SeedPairs",
    "atlas_active_version",
    "atlas_dag",
    "atlas_scale_fixture",
    "atlas_two_versions",
    "atlas_v1",
    "dump_atlas_version",
    "load_atlas_dump",
    "seed_pairs",
]

#: Label of the shared draft version a builder lands in when it is not given
#: ``version=``. Nodes, relations and groups of one topology must share their
#: version, so every builder without an explicit version must resolve to the
#: *same* row — hence ``get_or_create`` on this stable label rather than a fresh
#: version per call. A test that wants a second version creates one explicitly.
DEFAULT_VERSION_LABEL = "fixture"

#: Timestamp every published fixture row carries: fixed and in the past, so
#: ``objects.public()`` (status *and* ``published_at <= now``) resolves it
#: deterministically regardless of the machine clock.
PUBLISHED_AT = datetime(2026, 1, 1, tzinfo=UTC)

NODE_TYPE_DEFAULTS = {
    "label_en": "Research area",
    "label_fa": "دامنهٔ پژوهشی",
    "semantic_role": "area",
    "visual_role": "domain",
    "canonical_source": "research_topic",
}


def _node_type(key="research-area", **overrides):
    """Create an `AtlasNodeType` with spec-shaped defaults (`research-area`)."""
    row = dict(NODE_TYPE_DEFAULTS)
    row.update(overrides)
    return AtlasNodeType.objects.create(key=key, **row)


def _relation_type(key="uses", *, allowed_sources=(), allowed_targets=(), **overrides):
    """Create an `AtlasRelationType`; allowed type keys resolve to existing node types."""
    row = {
        "label_en": key,
        "label_fa": key,
        "inverse_label_en": f"inverse of {key}",
        "inverse_label_fa": key,
        "semantic_role": "utility",
    }
    row.update(overrides)
    relation_type = AtlasRelationType.objects.create(key=key, **row)
    if allowed_sources:
        relation_type.allowed_source_types.set(
            AtlasNodeType.objects.filter(key__in=allowed_sources)
        )
    if allowed_targets:
        relation_type.allowed_target_types.set(
            AtlasNodeType.objects.filter(key__in=allowed_targets)
        )
    return relation_type


def _version(status="draft", label=None, **overrides):
    """Create a fresh `AtlasVersion` row — never a second copy of the shared one.

    ``label=None`` mints a per-call label (``fixture-<8 hex>``) instead of reusing
    ``DEFAULT_VERSION_LABEL``: a second row carrying the shared ``("draft", "fixture")``
    identity would make ``_default_version()``'s ``get_or_create`` raise
    ``MultipleObjectsReturned`` for every version-less builder that runs afterwards —
    an error raised far from its cause. Pass an explicit ``label`` for a named version.
    """
    if label is None:
        label = f"{DEFAULT_VERSION_LABEL}-{uuid4().hex[:8]}"
    return AtlasVersion.objects.create(status=status, label=label, **overrides)


def _default_version():
    """The shared draft version used when a builder is not given one."""
    version, _created = AtlasVersion.objects.get_or_create(
        status="draft", label=DEFAULT_VERSION_LABEL
    )
    return version


def _default_node_type():
    """The shared `research-area` node type every plain ``_node()`` hangs off."""
    node_type, _created = AtlasNodeType.objects.get_or_create(
        key="research-area", defaults=dict(NODE_TYPE_DEFAULTS)
    )
    return node_type


def _node(version=None, *, node_type=None, public_key=None, **overrides):
    """Create an `AtlasNode`; defaults to the shared version and node type.

    ``canonical_model`` and ``importance`` default to the node type's
    ``canonical_source``/``default_importance`` (spec §5 copy-at-creation), so
    ``full_clean()`` passes unless the test changes something itself.
    """
    version = _default_version() if version is None else version
    node_type = _default_node_type() if node_type is None else node_type
    row = {
        "public_key": new_node_key(node_type.key) if public_key is None else public_key,
        "canonical_model": node_type.canonical_source,
        "importance": node_type.default_importance,
    }
    row.update(overrides)
    return AtlasNode.objects.create(version=version, node_type=node_type, **row)


#: Copy of the shared `uses` relation type a plain ``_relation()`` hangs off.
RELATION_TYPE_DEFAULTS = {
    "label_en": "uses",
    "label_fa": "استفاده می‌کند",
    "inverse_label_en": "used by",
    "inverse_label_fa": "استفاده شده توسط",
    "semantic_role": "utility",
}


def _default_relation_type():
    """The shared `uses` relation type every plain ``_relation()`` hangs off."""
    relation_type, _created = AtlasRelationType.objects.get_or_create(
        key="uses", defaults=dict(RELATION_TYPE_DEFAULTS)
    )
    return relation_type


def _relation(source=None, target=None, *, relation_type=None, version=None, **overrides):
    """Create an `AtlasRelation`; defaults to two fresh nodes of the shared version.

    ``directed`` and ``weight`` start at the relation type's
    ``directed_default``/``default_weight`` — the spec's "initialised from"
    rule, applied here by the caller because the model deliberately never
    rewrites them (a contradicting relation must stay observable for the
    publish-blocking ``DIRECTION_NOT_OVERRIDABLE`` code).
    """
    source = _node() if source is None else source
    target = _node() if target is None else target
    version = source.version if version is None else version
    relation_type = _default_relation_type() if relation_type is None else relation_type
    row = {"directed": relation_type.directed_default, "weight": relation_type.default_weight}
    row.update(overrides)
    return AtlasRelation.objects.create(
        version=version, source=source, target=target, relation_type=relation_type, **row
    )


def _group(version=None, *, public_key=None, **overrides):
    """Create an `AtlasGroup`; defaults to the shared version and a fresh group key."""
    version = _default_version() if version is None else version
    row = {"public_key": new_group_key() if public_key is None else public_key}
    row.update(overrides)
    return AtlasGroup.objects.create(version=version, **row)


def _group_translation(group, *, locale="en", label="", **overrides):
    """Create an `AtlasGroupTranslation` (blank ``label`` unless a test sets one)."""
    return AtlasGroupTranslation.objects.create(
        group=group, locale=locale, label=label, **overrides
    )


def _relation_translation(relation, *, locale="en", explanation="", **overrides):
    """Create an `AtlasRelationTranslation` (blank ``explanation`` by default)."""
    return AtlasRelationTranslation.objects.create(
        relation=relation, locale=locale, explanation=explanation, **overrides
    )


def _membership(group, node, **overrides):
    """Put ``node`` into ``group`` (they must share a version; ``clean()`` checks it)."""
    return AtlasGroupMembership.objects.create(group=group, node=node, **overrides)


# ---------------------------------------------------------------------------
# Canonical seed pairs (ruling R5) — paired EN/FA rows for all six families.
# ---------------------------------------------------------------------------

#: The text each seeded pair carries, per canonical-source key, so a resolution
#: test asserts a *known* value rather than whatever the first populated field
#: happens to be. ``profile``/``project`` use the field their model really
#: defines (``short_bio``/``objective``) — the plan's ``SUMMARY_FIELDS`` tuples
#: name the spec's earlier option as well, which contributes nothing here.
CANONICAL_PAIR_DEFAULTS: dict[str, dict[str, dict[str, Any]]] = {
    "profile": {
        "en": {"short_bio": "Profile short bio"},
        "fa": {"short_bio": "بیوی کوتاه"},
    },
    "research_topic": {
        "en": {"summary": "Research topic summary"},
        "fa": {"summary": "خلاصهٔ دامنهٔ پژوهشی"},
    },
    "project": {
        "en": {"objective": "Project objective"},
        "fa": {"objective": "هدف پروژه"},
    },
    "publication": {
        "en": {"abstract": "Publication abstract"},
        "fa": {"abstract": "چکیدهٔ انتشار"},
    },
    "method": {
        "en": {"short_description": "Method short description"},
        "fa": {"short_description": "توضیح کوتاه روش"},
    },
    "technology": {
        "en": {"short_description": "Technology short description"},
        "fa": {"short_description": "توضیح کوتاه فناوری"},
    },
}

#: The R5 fixture names. Also read by ``test_factories.py``, which asserts each is
#: declared exactly once in this module (R10: one builders module) — a renamed or
#: twice-defined fixture must not silently replace the one Tasks 9–16 import.
R5_FIXTURE_NAMES = ("seed_pairs", "atlas_v1", "atlas_active_version", "atlas_two_versions")


def _seed_pair(model, translation_key, *, status="published", en=None, fa=None):
    """Create the EN and FA rows of one logical record under one ``translation_key``.

    Returns ``(en_row, fa_row)``. The pair is the unit the Atlas references: a
    node's ``canonical_translation_key`` names it, and each locale resolves its
    own row through ``objects.public()``. A per-locale ``en``/``fa`` mapping may
    override any field — the one-sided fixtures use it to leave a row
    unpublished (``{"status": "draft", "published_at": None}``).
    """
    rows = []
    for locale, extra in (("en", en or {}), ("fa", fa or {})):
        row = {
            "locale": locale,
            "slug": f"{model._meta.model_name}-{locale}-{uuid4().hex[:8]}",
            "title": f"{model._meta.model_name} {locale}",
            "status": status,
            "published_at": PUBLISHED_AT if status == "published" else None,
            "translation_key": translation_key,
        }
        row.update(extra)
        rows.append(model.objects.create(**row))
    return tuple(rows)


@dataclass(frozen=True)
class SeedPairs:
    """Paired EN/FA rows for each canonical family, keyed by canonical source.

    Attributes are named by canonical-source key (``research_topic``), matching
    ``apps.atlas.canonical.CANONICAL_SOURCES`` — the registry vocabulary, never
    the Django model name.
    """

    profile: tuple[Any, Any]
    research_topic: tuple[Any, Any]
    project: tuple[Any, Any]
    publication: tuple[Any, Any]
    method: tuple[Any, Any]
    technology: tuple[Any, Any]

    def as_dict(self) -> dict[str, tuple[Any, Any]]:
        """The pairs by canonical-source key, in registry order."""
        return {
            "profile": self.profile,
            "research_topic": self.research_topic,
            "project": self.project,
            "publication": self.publication,
            "method": self.method,
            "technology": self.technology,
        }


@pytest.fixture
def seed_pairs():
    """One published EN/FA pair per canonical family (plan Task 8, ruling R5)."""
    pairs: dict[str, tuple[Any, Any]] = {}
    for source, model in (
        ("profile", Profile),
        ("research_topic", ResearchTopic),
        ("project", Project),
        ("publication", Publication),
        ("method", Method),
        ("technology", Technology),
    ):
        defaults = CANONICAL_PAIR_DEFAULTS[source]
        pairs[source] = _seed_pair(model, uuid4(), en=defaults["en"], fa=defaults["fa"])
    return SeedPairs(**pairs)


# ---------------------------------------------------------------------------
# The `atlas_v1` fixture family (ruling R5) — the small mirror of the current
# published Research Universe: an identity anchor, three research areas and the
# three `research-focus` relations between them.
# ---------------------------------------------------------------------------

#: `identity` is a system node type (spec §6.1): it anchors the topology, is not
#: a filter chip, and references the owner's canonical Profile record.
IDENTITY_TYPE_DEFAULTS = {
    "label_en": "Identity",
    "label_fa": "هویت",
    "semantic_role": "anchor",
    "visual_role": "anchor",
    "canonical_source": "profile",
    "default_importance": 100,
    "allow_as_root": True,
    "allow_children": True,
    "filter_visible": False,
}

#: The relation type the current published Research Universe uses for its three
#: identity → area edges (spec §6.2): directed, not a hierarchy, priority 95. Its
#: allowed pair is part of the seeded vocabulary (identity → research-area), so it
#: is set by ``_research_focus_type`` — an empty pair would mean "any active type"
#: (spec §6.2) and no fixture could express ``RELATION_TYPE_NOT_ALLOWED``.
RESEARCH_FOCUS_TYPE_DEFAULTS = {
    "label_en": "research focus",
    "label_fa": "تمرکز پژوهشی",
    "inverse_label_en": "research focus of",
    "inverse_label_fa": "تمرکز پژوهشی برای",
    "directed_default": True,
    "hierarchy_role": False,
    "visual_priority": 95,
    "self_loop_policy": "forbid",
    "semantic_role": "utility",
}

#: The undirected, any→any type of spec §6.2, used by the Task 9 direction
#: offender: ``directed_default=False`` plus ``overridable_direction=False`` makes
#: a relation authored ``directed=True`` a ``DIRECTION_NOT_OVERRIDABLE`` blocker.
RELATED_TO_TYPE_DEFAULTS = {
    "label_en": "related to",
    "label_fa": "مرتبط با",
    "inverse_label_en": "related to",
    "inverse_label_fa": "مرتبط با",
    "directed_default": False,
    "hierarchy_role": False,
    "visual_priority": 40,
    "self_loop_policy": "forbid",
    "semantic_role": "utility",
}

#: The pin the `atlas_v1` family exposes as ``pinned_x``/``pinned_y`` — a pin is
#: presentation only (spec §5.3), so the coordinates are arbitrary but fixed.
ATLAS_V1_PIN = (12.5, -4.25)

#: Placeholder coordinates every bundle writes for each visible node — all-zero on
#: purpose: Task 11's engine and Task 13's `recompute_layout` own real
#: coordinates, and the endpoint fixtures only need "a position exists".
ATLAS_PLACEHOLDER_LAYOUT_POSITION = [0.0, 0.0, 0.0]


def _identity_node_type():
    """The shared `identity` node type (spec §6.1)."""
    node_type, _created = AtlasNodeType.objects.get_or_create(
        key="identity", defaults=dict(IDENTITY_TYPE_DEFAULTS)
    )
    return node_type


def _research_focus_type():
    """The shared `research-focus` relation type (spec §6.2) — identity → research-area.

    The allowed pair is applied on every call (``get_or_create`` + ``set``): the
    fixture must not depend on some earlier builder having created the two node
    types, because a silently empty pair would turn "any active type" on and hide
    exactly the violation the Task 9 fixture is here to prove.
    """
    relation_type, _created = AtlasRelationType.objects.get_or_create(
        key="research-focus",
        defaults=dict(RESEARCH_FOCUS_TYPE_DEFAULTS),
    )
    relation_type.allowed_source_types.set([_identity_node_type()])
    relation_type.allowed_target_types.set([_default_node_type()])
    return relation_type


def _related_to_type():
    """The shared `related-to` relation type (spec §6.2): undirected, any → any."""
    relation_type, _created = AtlasRelationType.objects.get_or_create(
        key="related-to",
        defaults=dict(RELATED_TO_TYPE_DEFAULTS),
    )
    return relation_type


def _apply_placeholder_layout(version, nodes):
    """Write one all-zero position per visible node and mark the layout revision."""
    version.layout = {node.public_key: list(ATLAS_PLACEHOLDER_LAYOUT_POSITION) for node in nodes}
    version.layout_revision = 1
    version.save()
    return version


class _AtlasBundle:
    """Shared bundle plumbing: the ORM handle, overrides, draft-only nodes, layout."""

    version: AtlasVersion

    def __init__(self) -> None:
        self.overrides: dict[int, dict[str, Any]] = {}
        self._draft_version: AtlasVersion | None = None
        self._draft_only_keys: list[str] = []

    @property
    def pk(self) -> int:
        """The bundled version's primary key (the endpoint tests compare with it)."""
        return self.version.pk

    def override(self, node, *, locale, label="", summary="", aliases=None):
        """Create or update the node's per-locale override (idempotent per locale).

        The publish gate's other half (spec §5.4.3): a non-blank
        ``label_override`` makes a locale resolvable without a canonical row, so
        a parity test can clear ``MISSING_LOCALE_PROJECTION`` by overriding.
        Calling it twice updates the same row — ``(node, locale)`` is unique.
        """
        translation = node.translations.filter(locale=locale).first()
        if translation is None:
            translation = AtlasNodeTranslation(node=node, locale=locale)
        translation.label_override = label
        translation.summary_override = summary
        if aliases is not None:
            translation.aliases = list(aliases)
        translation.save()
        self.overrides.setdefault(node.pk, {})[locale] = translation
        return translation

    def _draft_for_extra_nodes(self) -> AtlasVersion:
        if self._draft_version is None:
            self._draft_version = _version(status="draft", label=f"draft-only-{uuid4().hex[:8]}")
        return self._draft_version

    def add_draft_only_node(self, *, node_type=None, visible=True, **overrides):
        """Add a visible node to a **separate draft version** and return its key.

        The draft-invisibility tests need an entity that exists in the database
        but must never be reachable from the public payload: a node of another
        version is exactly that shape, and it is *not* in the served version's
        layout either.
        """
        node_type = _default_node_type() if node_type is None else node_type
        node = _node(
            version=self._draft_for_extra_nodes(),
            node_type=node_type,
            visible=visible,
            **overrides,
        )
        self._draft_only_keys.append(node.public_key)
        return node.public_key

    @property
    def draft_only_key(self) -> str:
        """The key most recently returned by :meth:`add_draft_only_node`."""
        return self._draft_only_keys[-1]

    def corrupt_layout(self, mutate: Callable[[dict], Any]):
        """Apply ``mutate`` to a copy of the version's layout and save it.

        ``corrupt_layout(lambda layout: layout.popitem())`` removes one
        coordinate — the fail-closed test's way of making an active version
        internally invalid without touching anything else (plan Task 15).
        """
        layout = dict(self.version.layout)
        mutate(layout)
        self.version.layout = layout
        self.version.save(update_fields=["layout"])
        return layout


class AtlasV1Fixture(_AtlasBundle):
    """The draft mirror of the current published graph (ruling R5).

    The last three attributes are the Task 9 rule offenders, so the relation
    rules are proven against base fixture state instead of test-local setup:
    ``relation_type`` is the ``research-focus`` type the retirement rule flips,
    ``bad_relation`` sits outside that type's allowed pair, and
    ``direction_relation`` contradicts ``related-to``'s ``directed_default``.
    """

    def __init__(
        self,
        version: AtlasVersion,
        identity: AtlasNode,
        areas: list[AtlasNode],
        relations: list[AtlasRelation],
        pinned: AtlasNode,
        fa_missing_node: AtlasNode,
        en_missing_node: AtlasNode,
        canonical_keys: dict[str, UUID],
        relation_type: AtlasRelationType,
        bad_relation: AtlasRelation,
        direction_relation: AtlasRelation,
    ) -> None:
        super().__init__()
        self.version = version
        self.identity = identity
        self.areas = areas
        self.relations = relations
        self.pinned_node = pinned
        self.fa_missing_node = fa_missing_node
        self.en_missing_node = en_missing_node
        #: ``{label: translation_key}`` — ``identity``, ``area-0..2``,
        #: ``fa_missing``, ``en_missing``. The one-sided keys resolve in exactly
        #: one locale each, which is what the parity matrix asserts.
        self.canonical_keys = canonical_keys
        self.relation_type = relation_type
        self.bad_relation = bad_relation
        self.direction_relation = direction_relation

    @property
    def pinned_key(self) -> str:
        return self.pinned_node.public_key

    @property
    def pinned_x(self) -> float:
        return self.pinned_node.pin_x

    @property
    def pinned_y(self) -> float:
        return self.pinned_node.pin_y


class AtlasActiveVersionFixture(_AtlasBundle):
    """An *active* version of the small mirror plus a companion draft (ruling R5).

    ``pk`` is the active version's pk (the endpoint tests assert that the served
    ``version.id`` equals it), ``add_draft_only_node()`` lands in the companion
    draft version, and ``corrupt_layout`` can make the active version internally
    invalid for the fail-closed test.
    """

    def __init__(
        self,
        version: AtlasVersion,
        identity: AtlasNode,
        areas: list[AtlasNode],
        relations: list[AtlasRelation],
        canonical_keys: dict[str, UUID],
    ) -> None:
        super().__init__()
        self.version = version
        self.identity = identity
        self.areas = areas
        self.relations = relations
        self.canonical_keys = canonical_keys


class AtlasTwoVersionsFixture(_AtlasBundle):
    """``previous_active`` + ``draft`` with per-locale projection breaks (ruling R5).

    ``canonical_key`` names the one canonical pair the break/restore helpers
    toggle: ``break_fa_projection()`` unpublishes its FA row and
    ``break_en_projection()`` its EN row — exactly one locale, so the activation
    tests prove the parity gate fails closed in **both** directions (spec §20.1,
    plan Task 13).
    """

    def __init__(
        self,
        previous_active: AtlasVersion,
        draft: AtlasVersion,
        canonical_key: UUID,
    ) -> None:
        super().__init__()
        self.previous_active = previous_active
        self.draft = draft
        self.canonical_key = canonical_key
        #: The bundle's own handle is the draft: Task 16 activates it, and a
        #: bundle must never answer with the version it replaces.
        self.version = draft

    def _set_topic_status(self, locale: str, status: str) -> ResearchTopic:
        row = ResearchTopic.objects.get(translation_key=self.canonical_key, locale=locale)
        row.status = status
        if status == "published":
            row.published_at = PUBLISHED_AT
        row.save()
        return row

    def break_fa_projection(self):
        """Unpublish the FA row: FA stops resolving, EN keeps resolving."""
        return self._set_topic_status("fa", "draft")

    def restore_fa_projection(self):
        """Republish the FA row (the inverse of :meth:`break_fa_projection`)."""
        return self._set_topic_status("fa", "published")

    def break_en_projection(self):
        """Unpublish the EN row: EN stops resolving, FA keeps resolving."""
        return self._set_topic_status("en", "draft")

    def restore_en_projection(self):
        """Republish the EN row (the inverse of :meth:`break_en_projection`)."""
        return self._set_topic_status("en", "published")


def _build_identity_and_areas(version, *, with_one_sided_nodes: bool):
    """Build the 4-node core (identity + 3 areas) and its three `research-focus` edges.

    Returns ``(identity, areas, relations, canonical_keys, fa_missing, en_missing)``,
    where the last two are ``None`` unless ``with_one_sided_nodes`` is set:

    * ``fa_missing_node`` — its canonical pair resolves EN only (the FA row is
      created unpublished);
    * ``en_missing_node`` — its canonical pair resolves FA only.

    Both one-sided nodes are parked on their own `research-area` nodes behind a
    visible `research-focus` relation, because the relation must connect two
    nodes of one version and `research-focus` is the only type that accepts an
    `identity` source (spec §6.2). Each one-sided *pair* is a research-topic pair
    matching the node type's canonical source, with the opposite half created
    unpublished — so the node is refused in exactly one locale.
    """
    identity_type = _identity_node_type()
    area_type = _default_node_type()
    focus_type = _research_focus_type()

    identity_pair = _seed_pair(Profile, uuid4(), en={"short_bio": "Identity EN bio"})
    identity = _node(
        version=version,
        node_type=identity_type,
        canonical_translation_key=identity_pair[0].translation_key,
    )

    areas = []
    relations = []
    canonical_keys: dict[str, UUID] = {"identity": identity.canonical_translation_key}
    for index in range(3):
        pair = _seed_pair(
            ResearchTopic,
            uuid4(),
            en={"summary": f"Research area {index + 1} EN summary"},
            fa={"summary": f"خلاصهٔ دامنهٔ {index + 1}"},
        )
        area = _node(
            version=version,
            node_type=area_type,
            canonical_translation_key=pair[0].translation_key,
            sort_order=index,
        )
        areas.append(area)
        canonical_keys[f"area-{index}"] = pair[0].translation_key
        relations.append(
            _relation(
                source=identity,
                target=area,
                relation_type=focus_type,
                version=version,
                sort_order=index,
            )
        )

    if not with_one_sided_nodes:
        return identity, areas, relations, canonical_keys, None, None

    en_only_pair = _seed_pair(ResearchTopic, uuid4(), fa={"status": "draft", "published_at": None})
    fa_missing_node = _node(
        version=version,
        node_type=area_type,
        canonical_translation_key=en_only_pair[0].translation_key,
        sort_order=3,
    )
    fa_only_pair = _seed_pair(ResearchTopic, uuid4(), en={"status": "draft", "published_at": None})
    en_missing_node = _node(
        version=version,
        node_type=area_type,
        canonical_translation_key=fa_only_pair[0].translation_key,
        sort_order=4,
    )
    relations.append(
        _relation(
            source=identity,
            target=fa_missing_node,
            relation_type=focus_type,
            version=version,
            sort_order=3,
        )
    )
    relations.append(
        _relation(
            source=identity,
            target=en_missing_node,
            relation_type=focus_type,
            version=version,
            sort_order=4,
        )
    )
    canonical_keys["fa_missing"] = en_only_pair[0].translation_key
    canonical_keys["en_missing"] = fa_only_pair[0].translation_key
    return identity, areas, relations, canonical_keys, fa_missing_node, en_missing_node


@pytest.fixture
def atlas_v1():
    """Draft mirror of the current published graph, with both one-sided nodes (R5)."""
    version = _version(status="draft", label="atlas-v1")
    identity, areas, relations, canonical_keys, fa_missing, en_missing = (
        _build_identity_and_areas(version, with_one_sided_nodes=True)
    )
    focus_type = _research_focus_type()
    # The Task 9 rule offenders: a `research-focus` relation whose endpoints are
    # outside the type's allowed pair (area → identity, spec §6.2) and a
    # `related-to` relation authored directed against its undirected type. Both
    # are stored rows the model layer accepts on purpose — `directed` is never
    # coerced on save, and the allowed pair is a validation rule, not a constraint.
    bad_relation = _relation(
        source=areas[1],
        target=identity,
        relation_type=focus_type,
        version=version,
        sort_order=5,
    )
    direction_relation = _relation(
        source=areas[0],
        target=areas[1],
        relation_type=_related_to_type(),
        version=version,
        directed=True,
        sort_order=6,
    )
    relations = [*relations, bad_relation, direction_relation]
    pinned = areas[0]
    pinned.pin_x, pinned.pin_y, pinned.pin_z = (*ATLAS_V1_PIN, 0.0)
    pinned.save()
    _apply_placeholder_layout(version, list(version.nodes.all()))
    return AtlasV1Fixture(
        version=version,
        identity=identity,
        areas=areas,
        relations=relations,
        pinned=pinned,
        fa_missing_node=fa_missing,
        en_missing_node=en_missing,
        canonical_keys=canonical_keys,
        relation_type=focus_type,
        bad_relation=bad_relation,
        direction_relation=direction_relation,
    )


@pytest.fixture
def atlas_active_version():
    """The mirror as an **active** version plus a companion draft (R5).

    Deliberately without the one-sided nodes: an active version is servable, so
    every one of its visible nodes resolves in both locales (``nodeCount == 4``).
    """
    version = _version(status="active", label="atlas-active")
    identity, areas, relations, canonical_keys, _fa, _en = _build_identity_and_areas(
        version, with_one_sided_nodes=False
    )
    _apply_placeholder_layout(version, list(version.nodes.all()))
    return AtlasActiveVersionFixture(
        version=version,
        identity=identity,
        areas=areas,
        relations=relations,
        canonical_keys=canonical_keys,
    )


@pytest.fixture
def atlas_two_versions():
    """An active version and a fully resolvable draft beside it (R5).

    The draft is what Task 13 activates, so nothing about it may block
    publication until a ``break_*_projection()`` call says so.
    """
    previous_active = _version(status="active", label="atlas-two-active")
    _build_identity_and_areas(previous_active, with_one_sided_nodes=False)
    _apply_placeholder_layout(previous_active, list(previous_active.nodes.all()))

    draft = _version(status="draft", label="atlas-two-draft")
    identity, areas, relations, canonical_keys, _fa, _en = _build_identity_and_areas(
        draft, with_one_sided_nodes=False
    )
    _apply_placeholder_layout(draft, list(draft.nodes.all()))
    return AtlasTwoVersionsFixture(
        previous_active=previous_active,
        draft=draft,
        canonical_key=canonical_keys["area-0"],
    )


# ---------------------------------------------------------------------------
# The `atlas_dag` fixture (plan Task 10) — a legal multi-parent hierarchy plus a
# non-hierarchy relation type for the "a general cycle is legal" control.
# ---------------------------------------------------------------------------

#: Node type of the hierarchy fixture: an Atlas-only *structural* type, so its
#: nodes need no canonical record at all — each carries a non-blank override in
#: both locales instead (spec §5.4: a ``canonical_model = "none"`` node "always
#: requires an override in both locales"). That is exactly what the parity gate
#: has to level with, and it keeps the fixture free of CMS rows.
DAG_NODE_TYPE_DEFAULTS = {
    "label_en": "Structural",
    "label_fa": "ساختاری",
    "semantic_role": "area",
    "visual_role": "domain",
    "canonical_source": "none",
}

#: The hierarchy relation type (spec §5.6: hierarchy exists only through types
#: whose ``hierarchy_role`` is true). ``overridable_direction`` is on so a test
#: can author an undirected hierarchy edge without tripping Task 9's
#: ``DIRECTION_NOT_OVERRIDABLE``, and both pair tables stay empty = "any active
#: type" (spec §6.2), so no fixture edge can be a pair violation.
DAG_HIERARCHY_TYPE_DEFAULTS = {
    "label_en": "parent of",
    "label_fa": "والدِ",
    "inverse_label_en": "child of",
    "inverse_label_fa": "فرزندِ",
    "directed_default": True,
    "overridable_direction": True,
    "hierarchy_role": True,
    "visual_priority": 90,
    "self_loop_policy": "forbid",
    "semantic_role": "utility",
}

#: A *non*-hierarchy relation type, for the general-cycle control (spec §5.6:
#: "General (non-hierarchy) cycles are legal"). Directed on purpose: the two
#: edges of a two-node cycle then compose two different keys, while an undirected
#: pair composes one key and would be Task 9's ``DUPLICATE_RELATION`` instead.
DAG_GENERAL_TYPE_DEFAULTS = {
    "label_en": "relates to",
    "label_fa": "مرتبط با",
    "inverse_label_en": "relates to",
    "inverse_label_fa": "مرتبط با",
    "directed_default": True,
    "overridable_direction": True,
    "hierarchy_role": False,
    "visual_priority": 40,
    "self_loop_policy": "forbid",
    "semantic_role": "utility",
}

#: The fixture's node names in the order the diamond reads: the root, its two
#: children, then the child they share.
DAG_NODE_NAMES: tuple[str, ...] = ("area", "area-a", "area-b", "area-c")

#: The fixture's fixed public keys, ascending — ``area`` < ``area-a`` <
#: ``area-b`` < ``area-c``. The hierarchy DFS iterates ``public_key`` order
#: (plan Task 10: "iterating nodes in ``public_key`` order for determinism"), so
#: a fixture with generated keys could not state which node a cycle reports.
DAG_KEYS: dict[str, str] = {
    "area": "structural-00000001",
    "area-a": "structural-00000002",
    "area-b": "structural-00000003",
    "area-c": "structural-00000004",
}

#: The diamond's edges. ``close_cycle()`` adds ``area-c → area-a``, the back edge
#: that closes the cycle ``area-a → area-c → area-a``.
DAG_EDGES: tuple[tuple[str, str], ...] = (
    ("area", "area-a"),
    ("area", "area-b"),
    ("area-a", "area-c"),
    ("area-b", "area-c"),
)


def _dag_node_type():
    """The shared `structural` node type of the hierarchy fixture."""
    node_type, _created = AtlasNodeType.objects.get_or_create(
        key="structural", defaults=dict(DAG_NODE_TYPE_DEFAULTS)
    )
    return node_type


def _dag_hierarchy_type():
    """The shared `parent-of` relation type — the fixture's only hierarchy type."""
    relation_type, _created = AtlasRelationType.objects.get_or_create(
        key="parent-of", defaults=dict(DAG_HIERARCHY_TYPE_DEFAULTS)
    )
    return relation_type


def _dag_general_type():
    """The shared `relates-to` relation type — non-hierarchy, for cycle controls."""
    relation_type, _created = AtlasRelationType.objects.get_or_create(
        key="relates-to", defaults=dict(DAG_GENERAL_TYPE_DEFAULTS)
    )
    return relation_type


def _dag_nodes(version, *, node_type, order=DAG_NODE_NAMES) -> dict[str, AtlasNode]:
    """Create the four fixed-key nodes, each with both per-locale overrides, in ``order``.

    ``order`` exists so a test can build the same graph with a different *creation*
    order: the hierarchy rules iterate ``public_key`` order, so the node a cycle
    reports must not follow insertion order.
    """
    nodes: dict[str, AtlasNode] = {}
    for name in order:
        node = _node(
            version=version,
            node_type=node_type,
            public_key=DAG_KEYS[name],
            sort_order=DAG_NODE_NAMES.index(name),
        )
        for locale, label in (("en", f"{name} label"), ("fa", f"برچسب {name}")):
            AtlasNodeTranslation.objects.create(node=node, locale=locale, label_override=label)
        nodes[name] = node
    return nodes


def _dag_edges(version, nodes, *, hierarchy_type) -> list[AtlasRelation]:
    """Create the diamond's four hierarchy edges, in ``DAG_EDGES`` order."""
    return [
        _relation(
            source=nodes[source],
            target=nodes[target],
            relation_type=hierarchy_type,
            version=version,
            sort_order=index,
        )
        for index, (source, target) in enumerate(DAG_EDGES)
    ]


class AtlasDagFixture:
    """A legal multi-parent hierarchy (plan Task 10): ``area → {area-a, area-b} → area-c``.

    ``version`` is the bundle's handle and ``diamond`` the same object under the
    plan's snippet spelling; :meth:`node` and :meth:`parents` answer by short name,
    and the two mutations the plan's tests make are methods —
    :meth:`close_cycle` (a hierarchy back edge) and :meth:`add_related_cycle` (a
    cycle outside the hierarchy subgraph, which spec §5.6 permits).
    """

    def __init__(
        self,
        *,
        version: AtlasVersion,
        node_type: AtlasNodeType,
        hierarchy_type: AtlasRelationType,
        general_type: AtlasRelationType,
        nodes: dict[str, AtlasNode],
        relations: list[AtlasRelation],
    ) -> None:
        self.version = version
        self.node_type = node_type
        self.hierarchy_type = hierarchy_type
        self.general_type = general_type
        self.nodes = nodes
        self.relations = list(relations)

    @property
    def diamond(self) -> AtlasVersion:
        """The diamond-shaped version — the plan's Task 10 snippet reads ``.diamond``."""
        return self.version

    def node(self, name: str) -> AtlasNode:
        """The fixture node registered under ``name`` (``area``, ``area-a``, …)."""
        return self.nodes[name]

    def parents(self, name: str) -> list[AtlasNode]:
        """The visible hierarchy parents of ``name``, in ``public_key`` order."""
        return sorted(
            [
                relation.source
                for relation in AtlasRelation.objects.filter(
                    version=self.version,
                    relation_type=self.hierarchy_type,
                    target=self.node(name),
                    visible=True,
                ).select_related("source")
            ],
            key=lambda node: node.public_key,
        )

    def close_cycle(self) -> AtlasRelation:
        """Add ``area-c → area-a`` — the back edge of ``area-a → area-c → area-a``."""
        relation = _relation(
            source=self.node("area-c"),
            target=self.node("area-a"),
            relation_type=self.hierarchy_type,
            version=self.version,
            sort_order=len(DAG_EDGES) + 1,
        )
        self.relations.append(relation)
        return relation

    def add_related_cycle(self) -> list[AtlasRelation]:
        """Add ``area-a → area-b`` and ``area-b → area-a`` — a legal general cycle."""
        added = [
            _relation(
                source=self.node("area-a"),
                target=self.node("area-b"),
                relation_type=self.general_type,
                version=self.version,
                sort_order=len(DAG_EDGES),
            ),
            _relation(
                source=self.node("area-b"),
                target=self.node("area-a"),
                relation_type=self.general_type,
                version=self.version,
                sort_order=len(DAG_EDGES) + 1,
            ),
        ]
        self.relations.extend(added)
        return added


@pytest.fixture
def atlas_dag():
    """A legal four-node hierarchy with two parents for one child (plan Task 10)."""
    version = _version(status="draft", label="atlas-dag")
    node_type = _dag_node_type()
    nodes = _dag_nodes(version, node_type=node_type)
    relations = _dag_edges(version, nodes, hierarchy_type=_dag_hierarchy_type())
    _apply_placeholder_layout(version, list(version.nodes.all()))
    return AtlasDagFixture(
        version=version,
        node_type=node_type,
        hierarchy_type=_dag_hierarchy_type(),
        general_type=_dag_general_type(),
        nodes=nodes,
        relations=relations,
    )


# ---------------------------------------------------------------------------
# The `atlas_scale_fixture` family (plan Tasks 11 and 18) — 72 nodes / 136 relations.
#
# Task 18 declares the composition; Task 11 is the first *consumer* (the layout
# engine's scale tests), so the fixture is created here (rulings R4/R5/R10: one
# home for shared builders, created at the first task that needs it) and sized to
# Task 18's declared band, which then extends it with projection data.
# ---------------------------------------------------------------------------

#: The composition Task 18 declares for the scale fixture (spec §19.2's 40–80 /
#: 60–150 target band): 1 identity anchor, 12 research areas, 24 projects, 20
#: publications, 8 methods, 7 technologies = 72 visible nodes.
SCALE_COMPOSITION: tuple[tuple[str, int], ...] = (
    ("identity", 1),
    ("research-area", 12),
    ("project", 24),
    ("publication", 20),
    ("method", 8),
    ("technology", 7),
)

#: The declared totals of the default graph. The relation total is the
#: composition's own edge set — 36 ``parent-of`` + 48 ``uses`` + 24
#: ``implements`` + 20 ``cites`` + 8 ``related-to`` — not a padded number; a
#: request for another size pads or truncates that set (see
#: :func:`_scale_relation_specs`).
SCALE_NODE_TOTAL = 72
SCALE_RELATION_TOTAL = 136
SCALE_GROUP_TOTAL = 6
SCALE_PINNED_TOTAL = 4

#: The key the plan's Task 11 snippet pins by name
#: (``with_pin("project-2b3c4d5e", …)``), so the first project carries it
#: literally. The fixture leaves it **unpinned**: the snippet has to be able to
#: set a pin at test time.
SCALE_FIRST_PROJECT_KEY = "project-2b3c4d5e"

#: ``canonical_source`` per scale-only node type (spec §5.3, §6.1). ``identity``
#: (→ ``profile``) and ``research-area`` (→ ``research_topic``) reuse the shared
#: types; no canonical *row* is created for any of them — the layout engine reads
#: no copy, and Task 18 adds the projection data on top of these types.
SCALE_NODE_TYPE_SOURCES: dict[str, str] = {
    "project": "project",
    "publication": "publication",
    "method": "method",
    "technology": "technology",
}

#: Importance per type — a fixed ladder, so radii differ inside a type and the
#: relaxation has real work to do (spec §12.2 step 2 reads ``importance``).
SCALE_IMPORTANCE: dict[str, int] = {
    "identity": 100,
    "research-area": 78,
    "project": 60,
    "publication": 45,
    "method": 55,
    "technology": 70,
}

#: Four pins, spread far enough apart that no pair is closer than the sum of its
#: radii (spec §12.5). The identity anchor is one of them: a pinned anchor cannot
#: drift with the spiral, which is what the scene expects of the centre.
SCALE_PINS: dict[str, tuple[float, float]] = {
    "identity-00000001": (0.0, 0.0),
    "research-area-00000001": (40.0, 25.0),
    "research-area-00000002": (-40.0, 25.0),
    "research-area-00000003": (0.0, -45.0),
}

#: The ``related-to`` pairs of the default graph: eight *unordered* pairs over
#: the twelve areas, no repeated pair and no mirror (``related-to`` is undirected,
#: so a mirrored pair would be Task 9's ``DUPLICATE_RELATION``).
SCALE_RELATED_PAIRS: tuple[tuple[int, int], ...] = (
    (1, 2),
    (3, 4),
    (5, 6),
    (7, 8),
    (9, 10),
    (11, 12),
    (1, 7),
    (4, 10),
)

#: The two relation types the scale graph needs beyond the shared ones (``uses``
#: is the shared ``_default_relation_type``, ``related-to`` the shared undirected
#: type, ``parent-of`` the Task 10 hierarchy type).
SCALE_RELATION_TYPE_DEFAULTS: dict[str, dict[str, Any]] = {
    "implements": {
        "label_en": "implements",
        "label_fa": "پیاده‌سازی می‌کند",
        "inverse_label_en": "implemented by",
        "inverse_label_fa": "پیاده‌سازی‌شده توسط",
        "directed_default": True,
        "semantic_role": "utility",
        "visual_priority": 60,
        "self_loop_policy": "forbid",
    },
    "cites": {
        "label_en": "cites",
        "label_fa": "ارجاع می‌دهد به",
        "inverse_label_en": "cited by",
        "inverse_label_fa": "ارجاع داده‌شده توسط",
        "directed_default": True,
        "semantic_role": "utility",
        "visual_priority": 55,
        "self_loop_policy": "forbid",
    },
}


def _scale_key(prefix: str, index: int) -> str:
    """``<prefix>-<8 hex>`` from a counter — deterministic where ``new_node_key`` is not."""
    return f"{prefix}-{index:08x}"


def _scale_project_key(index: int) -> str:
    """The first project carries the key the plan's Task 11 snippet pins by name."""
    return SCALE_FIRST_PROJECT_KEY if index == 1 else _scale_key("project", index)


def _scale_node_type(key: str) -> AtlasNodeType:
    """The node type a scale node hangs off: two shared types plus four scale-only."""
    if key == "identity":
        return _identity_node_type()
    if key == "research-area":
        return _default_node_type()
    defaults = {
        "label_en": key.title(),
        "label_fa": f"برچسب {key}",
        "semantic_role": "record",
        "visual_role": "record",
        "canonical_source": SCALE_NODE_TYPE_SOURCES[key],
        "default_importance": SCALE_IMPORTANCE[key],
        "allow_as_root": False,
    }
    node_type, _created = AtlasNodeType.objects.get_or_create(key=key, defaults=defaults)
    return node_type


def _scale_relation_type(key: str) -> AtlasRelationType:
    """One scale-only relation type, from :data:`SCALE_RELATION_TYPE_DEFAULTS`."""
    relation_type, _created = AtlasRelationType.objects.get_or_create(
        key=key, defaults=dict(SCALE_RELATION_TYPE_DEFAULTS[key])
    )
    return relation_type


def _scale_relation_types() -> dict[str, AtlasRelationType]:
    """``{relation-type key: row}`` — the shared types first, the scale-only ones after."""
    return {
        "parent-of": _dag_hierarchy_type(),
        "uses": _default_relation_type(),
        "related-to": _related_to_type(),
        "implements": _scale_relation_type("implements"),
        "cites": _scale_relation_type("cites"),
    }


def _scale_overview_priority(type_key: str, index: int) -> str:
    """The deterministic ``featured`` / ``hidden`` / ``auto`` split (spec §5.3).

    One ``featured`` node (the anchor) and a ``hidden`` every ninth node, so the
    compact-overview filter has something to filter on both ends.
    """
    if type_key == "identity":
        return "featured"
    return "hidden" if index % 9 == 0 else "auto"


def _scale_node_specs(*, visible_nodes: int) -> list[tuple[str, str, int, str]]:
    """``(type key, public key, importance, mobile priority)`` per visible node.

    ``visible_nodes`` is the requested size. When it exceeds the declared
    composition the extra nodes are publications (the band's leaf type); when it
    falls short, the tail of :data:`SCALE_COMPOSITION` is dropped (technologies,
    then methods, …, only as far as the request needs). Both directions are a
    function of the request and of the composition as written, so two runs build
    the same graph and an edited composition still yields the requested size.
    """
    counts = dict(SCALE_COMPOSITION)
    delta = visible_nodes - sum(counts.values())
    if delta > 0:
        counts["publication"] += delta
    else:
        for type_key in ("technology", "method", "publication", "project", "research-area"):
            if delta == 0:
                break
            dropped = min(counts[type_key], -delta)
            counts[type_key] -= dropped
            delta += dropped

    specs: list[tuple[str, str, int, str]] = []
    for type_key, _declared in SCALE_COMPOSITION:
        for index in range(1, counts[type_key] + 1):
            key = (
                _scale_project_key(index) if type_key == "project" else _scale_key(type_key, index)
            )
            importance = min(100, SCALE_IMPORTANCE[type_key] + index % 5)
            specs.append((type_key, key, importance, _scale_overview_priority(type_key, index)))
    return sorted(specs, key=lambda spec: spec[1])


def _scale_padding_specs(
    keys: list[str], *, need: int, taken: list[tuple[str, str, str]]
) -> list[tuple[str, str, str]]:
    """Deterministic ``related-to`` triples filling a requested relation count.

    Index arithmetic over the sorted key list (``i`` → ``i + step``) for a few
    fixed steps, deduplicated as an *unordered* pair because ``related-to`` is
    undirected. Nothing here is random or time-dependent, so a padded size is as
    reproducible as the declared one.
    """
    if len(keys) < 2:
        return []
    used = {frozenset((source, target)) for source, kind, target in taken if kind == "related-to"}
    added: list[tuple[str, str, str]] = []
    for step in (5, 7, 11, 13, 17, 19):
        for index, key in enumerate(keys):
            if len(added) >= need:
                return added
            partner = keys[(index + step) % len(keys)]
            pair = frozenset((key, partner))
            if key == partner or pair in used:
                continue
            used.add(pair)
            added.append((key, "related-to", partner))
    return added


def _scale_relation_specs(
    keys_by_type: dict[str, list[str]], *, relations: int
) -> list[tuple[str, str, str]]:
    """``(source key, relation-type key, target key)`` triples in a fixed order.

    The hierarchy edges come first, then ``uses``, ``implements``, ``cites`` and
    the decorative ``related-to`` pairs, so a smaller request truncates the
    graph's periphery and never its structure.
    """
    identity = keys_by_type.get("identity", [])
    areas = keys_by_type.get("research-area", [])
    projects = keys_by_type.get("project", [])
    publications = keys_by_type.get("publication", [])
    methods = keys_by_type.get("method", [])
    technologies = keys_by_type.get("technology", [])

    specs: list[tuple[str, str, str]] = []
    if areas and identity:
        specs += [(identity[0], "parent-of", key) for key in areas]
    if areas:
        # The hierarchy reads parent → child: an area is the parent of its projects.
        specs += [
            (areas[index % len(areas)], "parent-of", key) for index, key in enumerate(projects)
        ]
    if technologies:
        for index, key in enumerate(projects):
            specs.append((key, "uses", technologies[index % len(technologies)]))
            specs.append((key, "uses", technologies[(index + 3) % len(technologies)]))
    if methods:
        specs += [
            (key, "implements", methods[index % len(methods)]) for index, key in enumerate(projects)
        ]
    if projects:
        specs += [
            (key, "cites", projects[index % len(projects)])
            for index, key in enumerate(publications)
        ]
    for first, second in SCALE_RELATED_PAIRS:
        if first <= len(areas) and second <= len(areas):
            specs.append((areas[first - 1], "related-to", areas[second - 1]))

    if len(specs) > relations:
        return specs[:relations]
    if len(specs) < relations:
        keys = sorted(key for group in keys_by_type.values() for key in group)
        specs += _scale_padding_specs(keys, need=relations - len(specs), taken=specs)
    return specs


def _scale_topology(
    version,
    *,
    visible_nodes: int = SCALE_NODE_TOTAL,
    relations: int = SCALE_RELATION_TOTAL,
    groups: int = SCALE_GROUP_TOTAL,
):
    """Build one scale graph: its node rows, its relation rows and its group memberships.

    Returns ``(nodes, relations, groups)`` — the same three values
    :meth:`AtlasScaleFixture.parts` hands to ``compute_layout``; ``groups`` is the
    ``{group key: member node keys}`` mapping that engine consumes (member keys
    sorted, so the mapping carries no row order).
    """
    specs = _scale_node_specs(visible_nodes=visible_nodes)
    nodes = [
        _node(
            version=version,
            node_type=_scale_node_type(type_key),
            public_key=key,
            importance=importance,
            mobile_overview_priority=priority,
        )
        for type_key, key, importance, priority in specs
    ]
    by_key = {node.public_key: node for node in nodes}
    keys_by_type: dict[str, list[str]] = {}
    for type_key, key, _importance, _priority in specs:
        keys_by_type.setdefault(type_key, []).append(key)

    relation_types = _scale_relation_types()
    relation_rows = []
    for index, (source, kind, target) in enumerate(
        _scale_relation_specs(keys_by_type, relations=relations)
    ):
        relation_rows.append(
            _relation(
                source=by_key[source],
                target=by_key[target],
                relation_type=relation_types[kind],
                version=version,
                sort_order=index,
            )
        )

    for key, (x, y) in SCALE_PINS.items():
        node = by_key.get(key)
        if node is not None:
            node.pin_x, node.pin_y, node.pin_z = x, y, None
            node.save(update_fields=["pin_x", "pin_y", "pin_z"])

    members: dict[str, tuple[str, ...]] = {}
    areas = keys_by_type.get("research-area", [])
    projects = keys_by_type.get("project", [])
    for index in range(groups):
        group = _group(version=version, public_key=_scale_key("group", index + 1), sort_order=index)
        group_members = list(projects[index * 4 : index * 4 + 4])
        if areas:
            group_members.append(areas[index % len(areas)])
            group_members.append(areas[(index + 1) % len(areas)])
        for offset, key in enumerate(dict.fromkeys(group_members)):
            _membership(group, by_key[key], sort_order=offset)
        members[group.public_key] = tuple(sorted(set(group_members)))

    return nodes, relation_rows, members


class AtlasScaleFixture:
    """The 72-node / 136-relation scale graph the layout engine measures (Tasks 11, 18).

    The plan's Task 11 snippet spells the surface as ``parts`` (splatted into
    ``compute_layout``), ``parts_reversed()`` (the same three collections with
    every element — and every group's member list — reversed),
    ``with_pin(key, x=…, y=…)``, ``is_root(key)``, ``z_by_depth(layout)`` and
    ``blueprint()``; Task 12 adds ``version(visible_nodes=…)`` and Task 18
    ``version_obj`` / ``assert_same_bytes_on_rebuild()``.
    """

    def __init__(
        self,
        *,
        version: AtlasVersion,
        nodes: list[AtlasNode],
        relations: list[AtlasRelation],
        groups: dict[str, tuple[str, ...]],
        hierarchy_type: AtlasRelationType,
        related_type: AtlasRelationType,
        node_types: dict[str, AtlasNodeType],
        pinned_keys: list[str],
    ) -> None:
        self.version_obj = version
        self.relations = list(relations)
        self.groups = dict(groups)
        self.hierarchy_type = hierarchy_type
        self.related_type = related_type
        self.node_types = dict(node_types)
        self.pinned_keys = tuple(pinned_keys)
        self._parts: dict[int, tuple[list[AtlasNode], list[AtlasRelation], dict]] = {
            version.pk: (list(nodes), list(relations), dict(groups))
        }
        self._requests: dict[int, tuple[int, int]] = {
            version.pk: (SCALE_NODE_TOTAL, SCALE_RELATION_TOTAL)
        }
        self._variants: dict[tuple[int, int], AtlasVersion] = {}

    # --- the graph -------------------------------------------------------

    @property
    def parts(self):
        """``(nodes, relations, groups)`` for the default graph, ready to splat."""
        return self.parts_of(self.version_obj)

    def parts_of(self, version: AtlasVersion):
        """The three engine inputs of ``version`` (a variant is built on demand)."""
        if version.pk not in self._parts:
            visible_nodes, relations = self._requests[version.pk]
            built = _scale_topology(version, visible_nodes=visible_nodes, relations=relations)
            _apply_placeholder_layout(version, built[0])
            self._parts[version.pk] = built
        return self._parts[version.pk]

    def parts_reversed(self):
        """The same three collections with every element in the opposite order."""
        nodes, relations, groups = self.parts
        return (
            list(reversed(nodes)),
            list(reversed(relations)),
            {key: tuple(reversed(value)) for key, value in groups.items()},
        )

    def version(self, *, visible_nodes: int | None = None, relations: int | None = None):
        """The default version, or a deterministic variant of the requested size.

        Task 12 asks for more than 100 visible nodes and the §19.2 timing test for
        80 / 150; both are this one builder with another request, cached per size.
        """
        requested = (
            SCALE_NODE_TOTAL if visible_nodes is None else visible_nodes,
            SCALE_RELATION_TOTAL if relations is None else relations,
        )
        if requested == (SCALE_NODE_TOTAL, SCALE_RELATION_TOTAL):
            return self.version_obj
        if requested not in self._variants:
            version = _version(status="draft", label=f"atlas-scale-{requested[0]}-{requested[1]}")
            self._variants[requested] = version
            self._requests[version.pk] = requested
            self.parts_of(version)
        return self._variants[requested]

    def node(self, public_key: str) -> AtlasNode:
        """The node row registered under ``public_key``."""
        return AtlasNode.objects.get(version=self.version_obj, public_key=public_key)

    @property
    def node_keys(self) -> tuple[str, ...]:
        """Every node key of the default graph, sorted."""
        return tuple(node.public_key for node in self.parts[0])

    def with_pin(self, public_key: str, *, x: float, y: float, z: float | None = None):
        """Pin one node and hand back the node rows ``compute_layout`` consumes.

        The rows handed out are the *same objects* the fixture cached, so the pin
        reaches the caller's next ``compute_layout`` call without a re-query — the
        plan's snippet reassigns ``nodes = fixture.with_pin(…)`` and expects the
        engine to see the pin.
        """
        node = next(row for row in self.parts[0] if row.public_key == public_key)
        node.pin_x, node.pin_y, node.pin_z = x, y, z
        node.save(update_fields=["pin_x", "pin_y", "pin_z"])
        self.pinned_keys = tuple(sorted({*self.pinned_keys, public_key}))
        return self.parts[0]

    # --- reading the result ----------------------------------------------

    def is_root(self, public_key: str) -> bool:
        """True when the node has no visible hierarchy parent (spec §12.2 step 3)."""
        children = {
            relation.target.public_key
            for relation in self.relations
            if relation.visible and relation.relation_type.hierarchy_role
        }
        return public_key not in children

    def z_by_depth(self, layout: dict[str, tuple[float, float, float]]):
        """``{z: (node keys…)}`` — the depth layers the *coordinates* express.

        Derived from the layout alone (nothing here consults ``z_by_depth``'s own
        idea of a depth), so ``len(set(result)) > 1`` is a statement about the
        engine's output: hierarchy hints produced more than one layer.
        """
        layers: dict[float, list[str]] = {}
        for key in sorted(layout):
            layers.setdefault(round(layout[key][2], 3), []).append(key)
        return {z: tuple(keys) for z, keys in sorted(layers.items())}

    def blueprint(self):
        """``[(stage, what it does)]`` — the engine's declared pipeline, in order.

        Read from the implementation rather than copied here, so the test that
        freezes the order (spec §12.2) freezes the order the engine really runs.
        """
        from apps.atlas.layout import LAYOUT_BLUEPRINT

        return [(stage.name, stage.description) for stage in LAYOUT_BLUEPRINT]

    def apply_layout(self):
        """Run the layout engine over the default graph and store the result.

        The plan's Task 11 snippet runs the engine through the fixture's parts;
        Task 18's dump tests apply the layout *before* dumping so the dump's
        ``layout`` block carries the engine's real coordinates, not the
        all-zero placeholders the builders write. Delegates to
        :func:`apps.atlas.layout.apply_layout` — the one layout authority (§12.1).
        """
        from apps.atlas.layout import apply_layout as _engine_apply_layout

        return _engine_apply_layout(self.version_obj)

    # --- dump / load (Task 18's dual serialization) -----------------------

    def dump(self) -> str:
        """This fixture's default graph as a deterministic dump.

        Routes through :func:`dump_atlas_version` — the one serialization home
        (the schema-freeze test asserts the two spellings are the same bytes),
        so the fixture's ``dump()`` is never a second spelling of the freeze.
        """
        return dump_atlas_version(self.version_obj)

    def load_dump(self, path) -> AtlasScaleFixture:
        """Read the dump file at ``path`` and reload it.

        The file form of :func:`load_atlas_dump` — the surface a second
        process holds is the path, not the string. Validation (digest, taxonomy,
        canonical, layout slots) is the loader's, so a tampered file is refused
        exactly as a tampered string.
        """
        text = Path(path).read_text(encoding="utf-8")
        return load_atlas_dump(text)


@pytest.fixture
def atlas_scale_fixture():
    """The 72-node / 136-relation scale graph (plan Tasks 11 and 18)."""
    version = _version(status="draft", label="atlas-scale")
    nodes, relations, groups = _scale_topology(version)
    _apply_placeholder_layout(version, nodes)
    # ONE canonical reference in the whole graph, on the identity anchor: the
    # dump/loader's canonical gate (``CANONICAL_SOURCE_MISSING``) needs a node
    # whose ``canonicalTranslationKey`` the document actually carries — spec §5.4
    # resolution is exercised by resolving it, once, against a live profile pair.
    en_profile, fa_profile = _seed_pair(Profile, uuid4(), en={}, fa={})
    anchor = next(node for node in nodes if node.node_type.key == "identity")
    anchor.canonical_translation_key = en_profile.translation_key
    anchor.save(update_fields=["canonical_translation_key"])
    return AtlasScaleFixture(
        version=version,
        nodes=nodes,
        relations=relations,
        groups=groups,
        hierarchy_type=_dag_hierarchy_type(),
        related_type=_related_to_type(),
        node_types={key: _scale_node_type(key) for key, _count in SCALE_COMPOSITION},
        pinned_keys=sorted(SCALE_PINS),
    )


# ---------------------------------------------------------------------------
# The `atlas_scale_fixture` family — dump/reload dual serialization (Task 18).
#
# Task 18's remaining obligation: ``dump_atlas_version(version)`` captures the
# stored whole-graph state of one version as a JSON string, and
# ``load_atlas_dump(text)`` rebuilds it — every reference resolved **exactly**
# or refused. Test-file anchors: ``apps/atlas/tests/test_scale_dump.py``
# ("The scale fixture's dump/reload surface") and
# ``docs/contracts/ATLAS-PAYLOAD-CONTRACT.md`` (ATLAS-PAYLOAD-CONTRACT: the
# frozen dump one home / frozen-keys rule).
#
# The trade-off, stated in plain view (the schema-freeze test looks for these
# two phrases here): a dump is a **secondary cache** — Plan C's frontend
# fixtures can rebuild the §19.2 graph from it without re-running the ORM
# builders, and a test process can round-trip a graph through a fresh primary-key
# space. The cache can go stale: that is why the loader validates every
# reference against the *live* database (taxonomy rows, canonical triples, the
# layout's slot uniqueness) and fails closed on any mismatch, instead of
# trusting the file. The dump is rebuilt from the ORM, never the reverse.
#
# Determinism (ruling R11): all rows are read in ``public_key`` order and the
# JSON is sorted-key, so a dump is a pure function of the stored graph and is
# byte-identical across processes. The loader is the mirror: it re-verifies the
# digest, re-inserts every row in dump order (or reversed — the projection is a
# pure function of the stored rows, so the rebuilt projection is byte-identical
# either way), and each verification failure raises with the same named prefix
# the live gates use.
#
# A canonical ``AtlasNodeTranslation`` override written by the dump's *loader*
# keeps the node's canonical reference resolvable in both locales, so a reload
# satisfies the locale-parity gate for both directions (broken only by real
# corruption, never by the round-trip itself).
# ---------------------------------------------------------------------------

#: The schema version of the dump document format.
DUMP_MODEL_VERSION = 1

#: Prefix of the digest value — names the algorithm and the shape it signs.
DUMP_DIGEST_PREFIX = "atlas-dump-v1:"

#: Locales the loader pins: the dumped copy minus its canonical row must not
#: leave a node unresolvable in either projected locale (spec §10.1 closes the
#: locale set; the loader keeps it closed on import too).
DUMP_LOCALES: tuple[str, ...] = ("en", "fa")


#: The document's frozen key set — the surface Tasks 19/20 consume; it is also
#: the loader's schema gate (an unexpected key set is not the digest's business,
#: so it has its own named refusal).
DUMP_DOCUMENT_KEYS: tuple[str, ...] = (
    "digest",
    "groups",
    "layout",
    "modelVersion",
    "nodeTypes",
    "nodes",
    "relationItems",
    "relationTypes",
    "layoutRevision",
    "canonicalSources",
)


def _dump_text(document: dict) -> str:
    """The one canonical JSON spelling this module's serialization uses.

    Every dump and every re-sign goes through here: the corruption tests'
    ``_resign`` replicates these kwargs exactly, so all three paths — a fresh
    dump, an edited document re-signed in a test, the loader's own digest
    check — hash the same spelling of the same document.
    """
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _dump_digest(six_payload: dict) -> str:
    """SHA-256 of the canonical JSON of the document's six payload keys."""
    payload_bytes = _dump_text(six_payload).encode("utf-8")
    return f"{DUMP_DIGEST_PREFIX}{hashlib.sha256(payload_bytes).hexdigest()}"


def _uuid(text: str | None) -> UUID | None:
    """The document's nullable uuid string as a ``UUID``, or ``None``."""
    if text is None:
        return None
    return UUID(str(text))


def _dump_node_entry(node: AtlasNode) -> dict:
    """One ``nodes`` entry: identity, type, copy reference, stored flags."""
    stored_override = None
    for translation in node.translations.order_by("locale"):
        if translation.label_override.strip():
            stored_override = translation
            break
    return {
        "publicKey": node.public_key,
        "type": node.node_type.key,
        "label": stored_override.label_override if stored_override else node.node_type.label_en,
        "overrides": {  # per-locale stored overrides (may be empty)
            translation.locale: {
                "labelOverride": translation.label_override,
                "summaryOverride": translation.summary_override,
                "accessibleLabelOverride": translation.accessible_label_override,
                "aliases": list(translation.aliases or []),
            }
            for translation in node.translations.order_by("locale")
        },
        "canonicalTranslationKey": (
            str(node.canonical_translation_key) if node.canonical_translation_key else None
        ),
        "importance": node.importance,
        "visible": node.visible,
        "mobileOverviewPriority": node.mobile_overview_priority,
        "pinX": node.pin_x,
        "pinY": node.pin_y,
        "pinZ": node.pin_z,
        "sortOrder": node.sort_order,
    }


def _dump_relation_entry(relation: AtlasRelation) -> dict:
    """One ``relationItems`` entry — every authored column plus per-locale copy."""
    return {
        "sourceKey": relation.source.public_key,
        "targetKey": relation.target.public_key,
        "relationType": relation.relation_type.key,
        "directed": relation.directed,
        "weight": relation.weight,
        "visible": relation.visible,
        "sortOrder": relation.sort_order,
        "translations": {
            translation.locale: translation.explanation
            for translation in relation.translations.order_by("locale")
        },
    }


def _dump_node_type_entry(node_type: AtlasNodeType) -> dict:
    """One ``nodeTypes`` entry — the taxonomy row the nodes reference by key."""
    return {
        "labelEn": node_type.label_en,
        "labelFa": node_type.label_fa,
        "semanticRole": node_type.semantic_role,
        "visualRole": node_type.visual_role,
        "defaultImportance": node_type.default_importance,
        "allowAsRoot": node_type.allow_as_root,
        "allowChildren": node_type.allow_children,
        "canonicalSource": node_type.canonical_source,
        "filterVisible": node_type.filter_visible,
        "active": node_type.active,
        "sortOrder": node_type.sort_order,
    }


def _dump_relation_type_entry(relation_type: AtlasRelationType) -> dict:
    """One ``relationTypes`` entry — the taxonomy row the relations reference by key."""
    return {
        "labelEn": relation_type.label_en,
        "labelFa": relation_type.label_fa,
        "inverseLabelEn": relation_type.inverse_label_en,
        "inverseLabelFa": relation_type.inverse_label_fa,
        "directedDefault": relation_type.directed_default,
        "overridableDirection": relation_type.overridable_direction,
        "semanticRole": relation_type.semantic_role,
        "hierarchyRole": relation_type.hierarchy_role,
        "defaultWeight": relation_type.default_weight,
        "visualPriority": relation_type.visual_priority,
        "selfLoopPolicy": relation_type.self_loop_policy,
        "active": relation_type.active,
        "sortOrder": relation_type.sort_order,
        "allowedSourceTypes": sorted(
            relation_type.allowed_source_types.values_list("key", flat=True)
        ),
        "allowedTargetTypes": sorted(
            relation_type.allowed_target_types.values_list("key", flat=True)
        ),
    }


#: Group entry shape = the surface the corruption test shuffles:
#: ``{"key", "label", "members"}`` with members as a plain list of node keys.
def _dump_group_entry(group: AtlasGroup) -> dict:
    """One ``groups`` entry: key, stored copy, stored membership, stored flags."""
    return {
        "key": group.public_key,
        "label": next(
            (row.label for row in group.translations.order_by("locale") if row.label.strip()),
            group.public_key,
        ),
        "members": sorted(
            AtlasGroupMembership.objects.filter(group=group).values_list(
                "node__public_key", flat=True
            )
        ),
        "sortOrder": group.sort_order,
        "active": group.active,
        "translations": {
            row.locale: {"label": row.label, "description": row.description}
            for row in group.translations.order_by("locale")
        },
    }


def dump_atlas_version(version: AtlasVersion) -> str:
    """The stored whole-graph state of ``version`` as deterministic JSON.

    One function, the statement of the freeze: sorted-key canonical JSON over
    rows read in ``public_key`` order, signed with the digest of the six
    payload keys. Not a projection, not a served payload — the dump is the
    *stored* graph: includes hidden rows, uses stored overrides, and preserves
    the layout dict as committed. Might be stale the moment the stored rows
    change; the loader validates against the live state for that reason.
    """
    version_row = AtlasVersion.objects.get(pk=version.pk)
    nodes = list(
        version_row.nodes.select_related("node_type")
        .prefetch_related("translations")
        .order_by("public_key")
    )
    relations = list(
        version_row.relations.select_related("source", "target", "relation_type")
        .prefetch_related("translations")
        .order_by("pk")
    )
    dump_groups = list(version_row.groups.prefetch_related("translations").order_by("public_key"))
    layout = dict(version_row.layout or {})
    used_node_types = sorted({node.node_type for node in nodes}, key=lambda row: row.key)
    used_relation_types = sorted(
        {relation.relation_type for relation in relations}, key=lambda row: row.key
    )
    six = {
        "nodeTypes": {
            node_type.key: _dump_node_type_entry(node_type) for node_type in used_node_types
        },
        "relationTypes": {
            relation_type.key: _dump_relation_type_entry(relation_type)
            for relation_type in used_relation_types
        },
        "nodes": [_dump_node_entry(node) for node in nodes],
        "relationItems": [_dump_relation_entry(relation) for relation in relations],
        "groups": [_dump_group_entry(group) for group in dump_groups],
        "layout": {key: list(coordinate) for key, coordinate in sorted(layout.items())},
        "layoutRevision": version_row.layout_revision,
    }
    document = {"modelVersion": DUMP_MODEL_VERSION, **six}
    document["canonicalSources"] = _dump_canonical_sources(version_row)
    # The registry joins the digested payload: canonical semantics are part of
    # the signed document — tampering with a slug must move the digest too.
    document["digest"] = _dump_digest(
        {key: value for key, value in document.items() if key not in ("modelVersion", "digest")}
    )
    return _dump_text(document)


def _verify_layout_slots(layout: Mapping[str, Any], nodes: Iterable[Mapping[str, Any]]) -> None:
    """Fail closed on any coordinate two nodes share (``LAYOUT_SLOT_COLLISION``).

    §12.1's one-layout-authority rule stated in the negative: a stored
    coordinate is one node's slot. A collision is corruption the digest cannot
    distinguish from an authored graph (both could be re-signed), so the
    loader checks the shape itself rather than trusting the signature.
    """
    seen: dict[tuple, str] = {}
    node_keys = {entry["publicKey"] for entry in nodes}
    for public_key in sorted(layout):
        coordinate = tuple(layout[public_key])
        if public_key not in node_keys:
            raise ValueError(
                f"LAYOUT_SLOT_COLLISION: layout slot {public_key!r} names no node "
                "of the dumped graph (a coordinate belongs to a node)."
            )
        owner = seen.setdefault(coordinate, public_key)
        if owner != public_key:
            raise ValueError(
                f"LAYOUT_SLOT_COLLISION: {public_key!r} sits at the exact stored "
                f"coordinate of {owner!r}; every node's coordinate must sit in "
                "its own slot."
            )


def load_atlas_dump(text: str, *, insert_reversed: bool = False) -> AtlasScaleFixture:
    """Rebuild the dumped version — every reference exactly, or refuse.

    The loader is the dump's mirror: digest re-verified (``DIGEST_MISMATCH``),
    taxonomy keys resolved against the live vocabulary (``TAXONOMY_TYPE_MISSING``),
    canonical triples seeded so a node resolves in both locales
    (``CANONICAL_SOURCE_MISSING`` only when the document names a canonical
    family the registry does not allow), layout slots uniqueness-checked
    (``LAYOUT_SLOT_COLLISION``). Every gate is a hard ``ValueError`` — a
    corrupted dump is a refused load, never a repaired graph.

    ``insert_reversed=True`` arrives from ``fixture.parts_reversed()``: every
    row list is inserted in the opposite order, proving the rebuilt projection
    is a property of the stored rows, not of the arrival sequence.
    """
    # Fail closed before anything is written: the digest is the first gate a
    # tampered-but-well-formed document fails.
    document = _parsed_dump(text)

    node_types, relation_types = _dump_taxonomy_rows(document)
    _verify_layout_slots(document["layout"], document["nodes"])
    _dump_canonical_rows(document, node_types)

    version = _reload_version_row(document)
    nodes, relations = _write_topology_rows(
        version, document, node_types, relation_types, reversed_order=insert_reversed
    )
    return _assemble_reloaded(
        version,
        nodes,
        relations,
        groups=document,
        pinned_keys=sorted(
            entry["publicKey"] for entry in document["nodes"] if entry.get("pinX") is not None
        ),
    )


def _parsed_dump(text: str) -> dict:
    """A validated dump document — every structural gate fired, in order.
    The gates, each a hard ``ValueError`` and never a repair:

    1. readable JSON (anything else is a lossy carrier: ``DIGEST_MISMATCH``);
    2. the frozen key set and the supported ``modelVersion``
       (``SCHEMA_MISMATCH`` — the corruption class the digest cannot name);
    3. the digest over the six payload keys, re-computed on this machine
       (``DIGEST_MISMATCH``) — an edited document cannot pass this unless it
       was re-signed, which is how each later rule's own corruption test
       reaches its own gate.
    """
    try:
        document = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(
            f"DIGEST_MISMATCH: the dump is not readable JSON ({error}); a dump is "
            "canonical bytes, so a parsing failure is a lossy carrier."
        ) from error
    if not isinstance(document, dict) or set(document) != set(DUMP_DOCUMENT_KEYS):
        keys = sorted(document) if isinstance(document, dict) else document
        raise ValueError(
            f"SCHEMA_MISMATCH: dump keys {keys!r} do not match the frozen schema "
            f"{sorted(DUMP_DOCUMENT_KEYS)} (docs/contracts/ATLAS-PAYLOAD-CONTRACT.md)."
        )
    if document["modelVersion"] != DUMP_MODEL_VERSION:
        raise ValueError(
            f"SCHEMA_MISMATCH: modelVersion {document['modelVersion']!r} is not "
            f"the supported schema version {DUMP_MODEL_VERSION}."
        )
    payload = {
        key: document[key] for key in DUMP_DOCUMENT_KEYS if key not in ("modelVersion", "digest")
    }
    if document["digest"] != _dump_digest(payload):
        raise ValueError(
            "DIGEST_MISMATCH: the dump fails its own digest — the document moved "
            "after it was signed; a reload never repairs a tampered dump."
        )
    return document


def _dump_taxonomy_rows(
    document: Mapping[str, Any],
) -> tuple[dict[str, AtlasNodeType], dict[str, AtlasRelationType]]:
    """The live taxonomy rows the document references, exactly as named.

    A type key the document names that the database does not carry is
    ``TAXONOMY_TYPE_MISSING`` — a hard stop, never a silent re-mint into a
    second source of truth (spec §5.3: the taxonomy is admin-managed). Below
    every named row's columns the document carries is *exactly what the dump
    read*; the loader trusts rows that exist and refuses those that do not,
    which is the whole taxonomy gate.
    """
    node_types: dict[str, AtlasNodeType] = {}
    relation_types: dict[str, AtlasRelationType] = {}
    # `_base_manager` — the taxonomy gate judges the stored vocabulary, not a
    # manager's projection of it. A type key the database does not carry at all
    # is refused; one that *exists* but is inactive is not this gate's business
    # (that judgement is the publish gate's ``*_TYPE_INACTIVE`` rows).
    for entry in document["nodes"]:
        key = entry["type"]
        if key in node_types:
            continue
        row = AtlasNodeType._base_manager.filter(key=key).first()
        if row is None:
            raise ValueError(
                f"TAXONOMY_TYPE_MISSING: the dump names node type {key!r} that the "
                "live database does not carry (spec §5.3: the taxonomy is "
                "admin-managed, never re-minted by a loader)."
            )
        node_types[key] = row
    for item in document["relationItems"]:
        key = item["relationType"]
        if key in relation_types:
            continue
        row = AtlasRelationType._base_manager.filter(key=key).first()
        if row is None:
            raise ValueError(
                f"TAXONOMY_TYPE_MISSING: the dump names relation type {key!r} that "
                "the live database does not carry (spec §5.3: same refusal)."
            )
        relation_types[key] = row
    return node_types, relation_types


def _dump_canonical_sources(version_row: AtlasVersion) -> list[dict]:
    """The semantic canonical rows the graph references, as the loader reads them.

    One entry per referenced translation key per family: ``family``,
    ``translationKey``, and per-locale ``locale``/``slug``/``title``/``status``
    — everything :func:`resolve_canonical` reads to answer §5.4. The dump is
    the *stored* graph side of the §5.3 "nodes are references, never copies"
    rule: it still records the copy the reference resolves to, because the
    loader must reconstruct resolution from the document, not mint records.
    """
    pairs: dict[tuple[str, UUID], dict] = {}
    for entry in _dump_referencing_nodes(version_row):
        source = entry.canonical_model
        key = entry.canonical_translation_key
        if source not in _CANONICAL_MODEL or key is None:
            continue
        record = pairs.setdefault(
            (source, key),
            {"family": source, "translationKey": str(key), "locales": {}},
        )
        for locale in DUMP_LOCALES:
            row = _canonical_row(entry, key, locale)
            if row is not None:
                record["locales"][locale] = {
                    "slug": str(row.slug or "").strip(),
                    "title": str(row.title or "").strip(),
                    "status": row.status,
                    "publishedAt": (
                        row.published_at.isoformat() if row.published_at else None
                    ),
                }
    return [
        {"family": family, "translationKey": str(key), "locales": record["locales"]}
        for (family, key), record in sorted(pairs.items())
    ]


def _dump_referencing_nodes(version_row: AtlasVersion):
    """Nodes of ``version_row`` carrying a canonical reference (dump side)."""
    return [
        node
        for node in version_row.nodes.select_related("node_type").prefetch_related(
            "translations"
        )
        if node.canonical_model in _CANONICAL_MODEL and node.canonical_translation_key
    ]


def _canonical_row(entry, key, locale):
    model = _CANONICAL_MODEL[entry.canonical_model]
    return (
        model._base_manager.filter(translation_key=key, locale=locale).first()
    )


def _from_iso(text: str):
    """A stored ISO stamp as an aware ``datetime`` (the loader never re-times)."""
    return parse_datetime(text)


def _dump_canonical_rows(document: dict, node_types: Mapping[str, AtlasNodeType]) -> None:
    """Reconstruct the canonical registry the document carries — restore, not mint.

    The dump's ``canonicalSources`` section carries per-locale ``slug``/``title``
    /``status`` for every referenced pair; the loader recreates exactly those
    semantic values (fresh DB pks are fine — that is the only allowed drift).
    A node reference with no entry in the registry is the
    ``CANONICAL_SOURCE_MISSING`` refusal: a corrupted reference must NOT heal
    itself out of node labels, type fallbacks or fixture factories.
    """
    registry = {
        (entry["family"], _uuid(entry["translationKey"])): entry["locales"]
        for entry in document.get("canonicalSources", [])
    }
    for entry in document["nodes"]:
        translation_key_text = entry.get("canonicalTranslationKey")
        if not translation_key_text:
            continue
        translation_key = _uuid(translation_key_text)
        if translation_key is None:
            raise ValueError(
                "CANONICAL_SOURCE_MISSING: node "
                f"{entry['publicKey']!r} carries a malformed canonical reference "
                f"({translation_key_text!r})."
            )
        node_type = node_types[entry["type"]]
        family = node_type.canonical_source
        if family not in _CANONICAL_MODEL:
            raise ValueError(
                f"CANONICAL_SOURCE_MISSING: node {entry['publicKey']!r} references "
                f"canonical family {family!r}, which resolves no published record "
                "in either locale."
            )
        if (family, translation_key) not in registry:
            raise ValueError(
                "CANONICAL_SOURCE_MISSING: node "
                f"{entry['publicKey']!r} references ({family!r}, "
                f"{translation_key_text!r}), which the dump's canonical registry "
                "does not carry."
            )
        model = _CANONICAL_MODEL[family]
        if model._base_manager.filter(translation_key=translation_key).exists():
            continue
        for locale in DUMP_LOCALES:
            stored = registry[(family, translation_key)].get(locale)
            if stored is None:
                continue
            model._base_manager.create(
                locale=locale,
                slug=stored["slug"],
                title=stored["title"],
                status=stored["status"],
                published_at=_from_iso(stored["publishedAt"]) if stored["publishedAt"] else None,
                translation_key=translation_key,
            )


def _reload_version_row(document: Mapping[str, Any]) -> AtlasVersion:
    """The rebuilt version: a fresh row, never an update of a live one.

    The reload lands wherever the caller's test state is (usually an empty
    database), so the row is created with a minted label and the stored layout
    dict copied verbatim; activation is the publish gate's business, not the
    loader's.
    """
    return AtlasVersion.objects.create(
        status="draft",
        label=f"reload-{uuid4().hex[:8]}",
        layout=dict(document["layout"]),
        layout_revision=document["layoutRevision"],
    )


def _write_topology_rows(
    version: AtlasVersion,
    document: Mapping[str, Any],
    node_types: Mapping[str, AtlasNodeType],
    relation_types: Mapping[str, AtlasRelationType],
    *,
    reversed_order: bool,
) -> tuple[list[AtlasNode], list[AtlasRelation]]:
    """Insert every dumped row — in dump order, or with every list reversed.

    Called with ``reversed_order=True`` the same loop rebuilds the graph from
    the opposite arrival order: the property the round-trip test is here to
    prove is that the projection is a function of the stored rows, so the
    arrival order must not leak into the payload. The per-locale overrides
    travel exactly as dumped, and a node the dump carries no override for gets
    the dumped ``label`` in one locale — the stored state, never an invention.
    """
    node_entries = list(document["nodes"])
    relation_entries = list(document["relationItems"])
    group_entries = list(document["groups"])
    for collection in (node_entries, relation_entries, group_entries):
        if reversed_order:
            collection.reverse()
        elif collection is group_entries:
            for entry in collection:
                entry["members"] = sorted(entry["members"])

    by_key: dict[str, AtlasNode] = {}
    for entry in node_entries:
        node = _node(
            version=version,
            node_type=node_types[entry["type"]],
            public_key=entry["publicKey"],
            canonical_model=node_types[entry["type"]].canonical_source,
            canonical_translation_key=_uuid(entry.get("canonicalTranslationKey")),
            importance=entry["importance"],
            visible=entry["visible"],
            mobile_overview_priority=entry["mobileOverviewPriority"],
            pin_x=entry.get("pinX"),
            pin_y=entry.get("pinY"),
            pin_z=entry.get("pinZ"),
            sort_order=entry.get("sortOrder", 0),
        )
        by_key[node.public_key] = node
        for locale, stored in entry["overrides"].items():
            AtlasNodeTranslation.objects.create(
                node=node,
                locale=locale,
                label_override=stored.get("labelOverride", ""),
                summary_override=stored.get("summaryOverride", ""),
                accessible_label_override=stored.get("accessibleLabelOverride", ""),
                aliases=stored.get("aliases", []),
            )
        # No synthesized translation: a node the dump carries no override for
        # stored none — its served label came from the type catalogue's fallback
        # (spec §10.2/§10.3), and re-minting an en override here would make the
        # reloaded payload sprout an ``accessibleLabel`` the original never had.

    relations: list[AtlasRelation] = []
    for item in relation_entries:
        relation = _relation(
            source=by_key[item["sourceKey"]],
            target=by_key[item["targetKey"]],
            relation_type=relation_types[item["relationType"]],
            version=version,
            directed=item["directed"],
            weight=item["weight"],
            visible=item["visible"],
            sort_order=item.get("sortOrder", 0),
        )
        for locale, explanation in item.get("translations", {}).items():
            AtlasRelationTranslation.objects.create(
                relation=relation, locale=locale, explanation=explanation
            )
        relations.append(relation)

    for entry in group_entries:
        group = AtlasGroup.objects.create(
            version=version,
            public_key=entry["key"],
            sort_order=entry.get("sortOrder", 0),
            active=entry.get("active", True),
        )
        for locale, stored in entry.get("translations", {}).items():
            AtlasGroupTranslation.objects.create(
                group=group,
                locale=locale,
                label=stored.get("label", ""),
                description=stored.get("description", ""),
            )
        for member in entry["members"]:
            AtlasGroupMembership.objects.create(group=group, node=by_key[member])

    return list(by_key.values()), relations


def _assemble_reloaded(
    version: AtlasVersion,
    nodes: list[AtlasNode],
    relations: list[AtlasRelation],
    *,
    groups: Mapping[str, Any],
    pinned_keys: Iterable[str],
) -> AtlasScaleFixture:
    """Wrap the rebuilt rows in the ``AtlasScaleFixture`` shape the tests read.

    The rebuilt fixture carries the cached topology of the reload (its ``parts``
    are the rows that arrived), so callers can assert against it exactly as
    against a built one — without going through the builder path again.
    """
    return AtlasScaleFixture(
        version=version,
        nodes=nodes,
        relations=relations,
        groups={
            entry["key"]: tuple(sorted(entry["members"])) for entry in groups["groups"]
        },
        hierarchy_type=_dag_hierarchy_type(),
        related_type=_related_to_type(),
        node_types={key: _scale_node_type(key) for key, _count in SCALE_COMPOSITION},
        pinned_keys=list(pinned_keys),
    )
