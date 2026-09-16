"""The shared fixture family of ruling R5 — ``seed_pairs`` and the ``atlas_v1`` bundles.

Ruling R5: ``atlas_v1``, ``atlas_active_version`` and ``atlas_two_versions`` are
created here (on top of ``factories.py``) and reused **unchanged** by Tasks 9,
10, 12, 13, 15 and 16. This file is their gate: it pins the surface those tasks
consume, so a Task 9–15 implementer reads the expected attributes from a test
rather than from a plan snippet.

``factories.py`` is not a conftest, so the fixtures are imported by name::

    from apps.atlas.tests.factories import atlas_v1, seed_pairs

The declared R5 surface:

* ``seed_pairs`` — paired EN/FA rows for profile, research_topic, project,
  publication, method and technology;
* ``atlas_v1`` — draft version, 4 visible core nodes (identity + 3 areas), 3
  ``research-focus`` relations, one pinned node, ``fa_missing_node`` (canonical
  EN only) / ``en_missing_node`` (canonical FA only), ``override(node, *,
  locale, label)``, ``add_draft_only_node()`` / ``draft_only_key``;
* ``atlas_active_version`` — the same mirror with ``status="active"`` (no
  one-sided nodes; ``nodeCount == 4``), plus ``corrupt_layout`` and a companion
  draft version for ``add_draft_only_node()``;
* ``atlas_two_versions`` — ``draft`` + ``previous_active`` with
  ``break_fa_projection()`` / ``restore_fa_projection()`` and
  ``break_en_projection()`` / ``restore_en_projection()``.
"""

import ast
import re
from pathlib import Path

import pytest

from apps.atlas.canonical import CANONICAL_SOURCES, resolve_canonical
from apps.atlas.models import AtlasNode, AtlasNodeTranslation, AtlasNodeType, AtlasVersion
from apps.atlas.tests.factories import (
    R5_FIXTURE_NAMES,
    atlas_active_version,
    atlas_two_versions,
    atlas_v1,
    seed_pairs,
)

#: The four names below are the pytest fixtures this module consumes from
#: ``factories.py``; ``__all__`` marks those imports as used-by-design (pyflakes
#: cannot see a test parameter as a use of a module-level binding, so a bare
#: import would be reported as unused and its parameter as a redefinition).
__all__ = ["atlas_active_version", "atlas_two_versions", "atlas_v1", "seed_pairs"]

pytestmark = pytest.mark.django_db

FACTORIES_PATH = Path(__file__).with_name("factories.py")


def test_seed_pairs_provides_paired_rows_for_every_family(seed_pairs):
    """One EN/FA pair per canonical source, published and resolvable in its exact locale."""
    assert set(seed_pairs.as_dict()) == set(CANONICAL_SOURCES)
    for source, model in CANONICAL_SOURCES.items():
        en_row, fa_row = getattr(seed_pairs, source)
        assert isinstance(en_row, model) and isinstance(fa_row, model)
        assert en_row.locale == "en" and fa_row.locale == "fa"
        assert en_row.translation_key == fa_row.translation_key
        assert resolve_canonical(source, en_row.translation_key, "en").row.pk == en_row.pk
        assert resolve_canonical(source, en_row.translation_key, "fa").row.pk == fa_row.pk


def test_atlas_v1_topology_surface(atlas_v1):
    assert atlas_v1.version.status == "draft"
    assert atlas_v1.version.nodes.count() == 6  # 4 core nodes + 2 one-sided ones
    visible = list(atlas_v1.version.nodes.filter(visible=True))
    assert len(visible) == 6
    keys = {node.public_key for node in visible}
    assert atlas_v1.identity.public_key in keys
    assert {node.public_key for node in atlas_v1.areas} <= keys
    assert atlas_v1.identity.node_type.key == "identity"
    assert all(node.node_type.key == "research-area" for node in atlas_v1.areas)

    relations = list(atlas_v1.version.relations.all())
    # 3 `research-focus` edges of the core mirror + 1 per one-sided node.
    assert len(relations) == 5
    assert {relation.relation_type.key for relation in relations} == {"research-focus"}
    # The view fixtures depend on a valid layout for every served node (spec §10.3:
    # "every visible node has one"); the coordinates are Task 11/13's, a placeholder here.
    assert atlas_v1.version.layout == {node.public_key: [0.0, 0.0, 0.0] for node in visible}


def test_atlas_v1_pins_are_exposed_for_the_clone_test(atlas_v1):
    pinned = atlas_v1.version.nodes.get(public_key=atlas_v1.pinned_key)
    assert pinned.pin_x == atlas_v1.pinned_x
    assert pinned.pin_y is not None and pinned.pin_z is not None
    assert atlas_v1.pinned_key in atlas_v1.version.layout


def test_atlas_v1_one_sided_canonical_nodes_and_their_relations(atlas_v1):
    """The parity matrix needs both directions, each behind a *visible* relation.

    ``fa_missing_node``'s pair resolves EN only, ``en_missing_node``'s FA only —
    the one-sided pair is a real pair with an unpublished half, and each node is
    an endpoint of a visible relation so hiding it also produces a dangling edge.
    """
    fa_missing, en_missing = atlas_v1.fa_missing_node, atlas_v1.en_missing_node
    assert fa_missing.visible and en_missing.visible
    assert fa_missing.canonical_model == "research_topic"
    assert en_missing.canonical_model == "research_topic"
    en_key = atlas_v1.canonical_keys["fa_missing"]
    assert resolve_canonical("research_topic", en_key, "en") is not None
    assert resolve_canonical("research_topic", en_key, "fa") is None
    fa_key = atlas_v1.canonical_keys["en_missing"]
    assert resolve_canonical("research_topic", fa_key, "fa") is not None
    assert resolve_canonical("research_topic", fa_key, "en") is None

    endpoints = {
        node.public_key
        for relation in atlas_v1.version.relations.all()
        for node in (relation.source, relation.target)
    }
    assert {fa_missing.public_key, en_missing.public_key} <= endpoints


def test_atlas_v1_override_creates_then_updates_one_row_per_locale(atlas_v1):
    node = atlas_v1.fa_missing_node
    translation = atlas_v1.override(node, locale="fa", label="برچسب")
    assert isinstance(translation, AtlasNodeTranslation)
    assert (translation.node_id, translation.locale) == (node.pk, "fa")
    assert translation.label_override == "برچسب"

    again = atlas_v1.override(node, locale="fa", label="برچسب دوم", summary="خلاصه")
    assert again.pk == translation.pk  # updated, not duplicated
    assert again.label_override == "برچسب دوم"
    assert again.summary_override == "خلاصه"
    assert node.translations.count() == 1
    assert atlas_v1.overrides[node.pk] == {"fa": again}


def test_atlas_v1_draft_only_node_lands_in_a_draft_version(atlas_v1):
    key = atlas_v1.add_draft_only_node()
    assert atlas_v1.draft_only_key == key
    node = AtlasNode.objects.get(public_key=key)
    assert node.visible and node.version_id != atlas_v1.version.pk
    assert node.version.status == "draft"
    assert key not in atlas_v1.version.layout
    second = atlas_v1.add_draft_only_node()
    assert second != key and atlas_v1.draft_only_key == second


def test_atlas_active_version_surface(atlas_active_version):
    version = atlas_active_version.version
    assert version.status == "active" and version.pk == atlas_active_version.pk
    visible = list(version.nodes.filter(visible=True))
    assert len(visible) == 4  # Task 15 asserts nodeCount == 4 for exactly these
    assert version.relations.count() == 3
    assert set(version.layout) == {node.public_key for node in visible}
    assert all(part == 0.0 for position in version.layout.values() for part in position)
    assert AtlasVersion.objects.filter(status="active").count() == 1


def test_atlas_active_version_draft_only_node_never_enters_the_active_version(atlas_active_version):
    key = atlas_active_version.add_draft_only_node()
    assert atlas_active_version.draft_only_key == key
    node = AtlasNode.objects.get(public_key=key)
    assert node.version.status == "draft" and node.visible
    assert node.version_id != atlas_active_version.pk
    assert not AtlasNode.objects.filter(version_id=atlas_active_version.pk, public_key=key).exists()
    assert key not in atlas_active_version.version.layout


def test_atlas_active_version_corrupt_layout_removes_a_coordinate(atlas_active_version):
    before = dict(atlas_active_version.version.layout)
    atlas_active_version.corrupt_layout(lambda layout: layout.popitem())
    refreshed = AtlasVersion.objects.get(pk=atlas_active_version.pk)
    assert len(refreshed.layout) == len(before) - 1
    assert set(refreshed.layout) < set(before)


def test_atlas_two_versions_break_and_restore_both_locale_directions(atlas_two_versions):
    assert atlas_two_versions.previous_active.status == "active"
    assert atlas_two_versions.draft.status == "draft"
    key = atlas_two_versions.canonical_key
    assert resolve_canonical("research_topic", key, "en") is not None
    assert resolve_canonical("research_topic", key, "fa") is not None

    atlas_two_versions.break_fa_projection()
    assert resolve_canonical("research_topic", key, "fa") is None  # exact-locale break…
    assert resolve_canonical("research_topic", key, "en") is not None  # …in one direction only
    atlas_two_versions.restore_fa_projection()
    assert resolve_canonical("research_topic", key, "fa") is not None

    atlas_two_versions.break_en_projection()
    assert resolve_canonical("research_topic", key, "en") is None
    assert resolve_canonical("research_topic", key, "fa") is not None
    atlas_two_versions.restore_en_projection()
    assert resolve_canonical("research_topic", key, "en") is not None


def test_atlas_two_versions_draft_is_publishable_shaped(atlas_two_versions):
    """Task 13 activates this draft, so it must mirror a valid topology."""
    for version in (atlas_two_versions.previous_active, atlas_two_versions.draft):
        visible = list(version.nodes.filter(visible=True))
        assert len(visible) == 4
        assert set(version.layout) == {node.public_key for node in visible}
        assert version.relations.count() == 3
        for relation in version.relations.all():
            assert relation.source.visible and relation.target.visible
        for node in visible:
            for locale in ("en", "fa"):
                assert (
                    resolve_canonical(node.canonical_model, node.canonical_translation_key, locale)
                    is not None
                ), (node.public_key, locale)


def test_r5_fixture_functions_are_declared_once_in_the_one_builders_module():
    """R10: one builders module; R5: the four fixture names live in it, each once.

    The plan's Task 18 adds the scale fixture and may reuse ``atlas_v1``, both by
    importing from ``factories.py`` — this scan is why a second module cannot quietly
    satisfy a consumer while the real fixture rots.
    """
    tree = ast.parse(FACTORIES_PATH.read_text(encoding="utf-8"))
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
    names = [node.name for node in functions]
    assert len(names) == len(set(names)), "a duplicate builder silently replaces the original"
    for name in R5_FIXTURE_NAMES:
        assert names.count(name) == 1, name
    by_name = {node.name: node for node in functions}
    for name in R5_FIXTURE_NAMES:
        decorators = {
            getattr(decorator, "attr", None) for decorator in by_name[name].decorator_list
        }
        assert "fixture" in decorators, name
    text = FACTORIES_PATH.read_text(encoding="utf-8")
    assert re.search(r"__all__\s*=", text), "__all__ keeps the public builder surface explicit"

    import apps.atlas.tests.factories as factories_module

    missing = [n for n in factories_module.__all__ if not hasattr(factories_module, n)]
    assert missing == [], f"__all__ names an object that does not exist: {missing}"


def test_the_one_sided_nodes_belong_to_real_owned_types(atlas_v1):
    """The two one-sided nodes are not hand-rolled: their types resolve through the registry."""
    for node in (atlas_v1.fa_missing_node, atlas_v1.en_missing_node):
        assert node.node_type.canonical_source == node.canonical_model
        assert AtlasNodeType.objects.get(pk=node.node_type_id).key in {
            "research-area",
            "identity",
        }
