"""Shared ORM builders and fixtures for the Atlas test suite (plan Tasks 6–18).

One home for the builders the plan's Task 6/7/8 test snippets call by name —
``_node_type``, ``_relation_type``, ``_node``, ``_relation``, ``_group`` and the
two translation builders — plus, from Task 8 (ruling R5), the canonical
``seed_pairs`` fixture and the ``atlas_v1`` / ``atlas_active_version`` /
``atlas_two_versions`` fixture family that Tasks 9–16 consume. Task 18 adds the
scale fixture. Nothing here is production code: a builder exists so a test
states only the fields the behaviour under test depends on.

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

Every bundle writes a placeholder ``layout`` (all-zero coordinates) for each of
its visible nodes, because the endpoints of Tasks 15/16 fail closed on a missing
position: real coordinates are Task 11's engine and Task 13's
``recompute_layout``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

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
    "DEFAULT_VERSION_LABEL",
    "NODE_TYPE_DEFAULTS",
    "PUBLISHED_AT",
    "R5_FIXTURE_NAMES",
    "RELATION_TYPE_DEFAULTS",
    "AtlasActiveVersionFixture",
    "AtlasTwoVersionsFixture",
    "AtlasV1Fixture",
    "SeedPairs",
    "atlas_active_version",
    "atlas_two_versions",
    "atlas_v1",
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
