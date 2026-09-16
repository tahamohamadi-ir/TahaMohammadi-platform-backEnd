"""Deterministic layout engine (spec §12) — plan Task 11.

The engine is a **pure** module: :func:`compute_layout` receives rows and returns
coordinates, :func:`hierarchy_depths` reports the hierarchy's shape, and
:func:`apply_layout` is the one function that reads and writes the database (one
version, one query set, one save).

The four properties spec §12.3 makes binding are each pinned by the test that
can express them:

* **determinism / ordering** — the same graph laid out twice is byte-identical,
  and the *arrival order* of the rows does not matter (the fixture can hand the
  same three collections over with every element reversed);
* **quantisation** — every stored coordinate is a 3-decimal multiple (spec §12.2
  step 9), asserted on the stored JSON rather than on an intermediate value;
* **pins are inputs** — a pinned coordinate is copied, never derived, and it
  survives the final collision pass (spec §12.2 steps 7/8);
* **revision and key source** — ``layout_revision`` moves on every apply
  (spec §8.3, the plan's ``recompute_layout`` contract) and the stored dict is
  keyed by ``public_key``, so it survives a clone that copies its source's keys
  (ruling R9).

The small graph is the ruling-R5 ``atlas_v1`` fixture; the scale graph is
``atlas_scale_fixture``, created by this task inside the one builders module
(rulings R4/R5/R10) because the plan's Task 11 snippet — and Task 18 after it —
consumes it.

Fixtures are imported by name (``factories.py`` is not a conftest), so ``__all__``
marks them as used-by-design: pyflakes cannot see a test parameter as a use of a
module-level binding.
"""

from __future__ import annotations

import json
import math
import time
from decimal import Decimal

import pytest

from apps.atlas.layout import (
    LAYOUT_CONSTANTS,
    apply_layout,
    compute_layout,
    hierarchy_depths,
)
from apps.atlas.tests.factories import (
    SCALE_FIRST_PROJECT_KEY,
    SCALE_GROUP_TOTAL,
    SCALE_NODE_TOTAL,
    SCALE_PINNED_TOTAL,
    SCALE_PINS,
    SCALE_RELATION_TOTAL,
    _node,
    _relation,
    _version,
    atlas_scale_fixture,
    atlas_v1,
)

__all__ = ["atlas_scale_fixture", "atlas_v1"]

pytestmark = pytest.mark.django_db

#: Spec §19.2's budget for the largest target scale: "Layout computation on
#: activation (80 / 150) | ≤ 2 s | Backend test timing". The ceiling stays a
#: **runaway guard**: the measured cost of this graph is a small fraction of a
#: second, so the assertion cannot fail for machine speed — it fails when the
#: engine does per-pair work it must not (the probe proves that with a mutation).
SCALE_TIMING_CEILING_SECONDS = 2.0

#: The 3-decimal rule of spec §12.3 ("rounding happens once, at step 9"), spelled
#: as the smallest decimal place a stored coordinate may have.
COORDINATE_EXPONENT = -3

#: The three depth layers the scale graph's hierarchy produces: identity (0) →
#: research areas (1) → projects (2); the maximum depth fixes the spread.
SCALE_DEPTH_LAYERS = 3

#: The scale graph's roots: every node with no hierarchy parent — the identity
#: anchor, the 20 publications, the 8 methods and the 7 technologies. Only the
#: identity anchor and the twelve areas form the hierarchy; a sideline node's
#: relationships are non-hierarchy types, so (§12.2 step 3) it is seeded on the
#: same spiral as the anchor. The composition test pins this count, so an edit
#: that quietly moves a node between hierarchy levels is visible.
SCALE_ROOT_TOTAL = 36


def _visible_keys(version) -> set[str]:
    """The public keys of the version's visible nodes, read back from the database."""
    return set(version.nodes.filter(visible=True).values_list("public_key", flat=True))


def _stored_exponent(value: float) -> int:
    """The exponent of ``value`` as written out — ``-4`` for ``0.0001``, ``-1`` for ``12.5``."""
    return Decimal(str(value)).as_tuple().exponent


def _node_state(nodes) -> list[tuple]:
    """``(key, importance, pins)`` per row — the input state a layout may never change."""
    return [
        (node.public_key, node.importance, node.pin_x, node.pin_y, node.pin_z) for node in nodes
    ]


def _pin_separation(atlas_scale_fixture, layout: dict) -> float:
    """The tightest pinned↔unpinned slack: distance minus the two drawn radii.

    Radii follow the spec's own formula (§12.2 step 2, with the anchor ratio),
    so a negative value means the final collision pass (step 8) left an *unpinned*
    neighbour overlapping a pinned node — the pass's whole purpose. Pinned↔pinned
    pairs are excluded on purpose: neither endpoint may move, so an overlap there
    is spec §12.5's ``OVERLAPPING_PINS`` warning (Task 12's), not a layout defect.
    """
    low, high = LAYOUT_CONSTANTS["r_min"], LAYOUT_CONSTANTS["r_max"]
    rows = {node.public_key: node for node in atlas_scale_fixture.parts[0]}
    radius = {key: low + (high - low) * (row.importance / 100) for key, row in rows.items()}
    anchor = next((key for key, row in rows.items() if row.node_type.key == "identity"), None)
    if anchor is not None:
        widest = max(value for key, value in radius.items() if key != anchor)
        radius[anchor] = LAYOUT_CONSTANTS["anchor_ratio"] * widest

    pinned = set(atlas_scale_fixture.pinned_keys)
    slack = [
        math.dist(layout[pinned_key][:2], layout[key][:2]) - (radius[pinned_key] + radius[key])
        for pinned_key in pinned
        for key in layout
        if key not in pinned
    ]
    assert slack, "the fixture pins at least one node and lays out more"
    return min(slack)


# ---------------------------------------------------------------------------
# The plan's Task 11 snippets
# ---------------------------------------------------------------------------


def test_layout_is_deterministic_for_identical_input(atlas_scale_fixture):
    """Same graph, twice, and in the opposite arrival order — identical coordinates.

    The spec's §12.3 rule ("recomputes for a fixed fixture twice and asserts
    identical coordinates") plus the plan's stronger half: the *arrival order* of
    the rows must not change the result, which is why the fixture can reverse
    every collection it hands over.
    """
    first = compute_layout(*atlas_scale_fixture.parts)
    second = compute_layout(*atlas_scale_fixture.parts)
    assert first == second
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)

    reversed_input = atlas_scale_fixture.parts_reversed()
    third = compute_layout(*reversed_input)
    assert third == first, "order of arrival must not change the result"
    assert json.dumps(third, sort_keys=True) == json.dumps(first, sort_keys=True)


def test_pins_win_over_every_derived_coordinate(atlas_scale_fixture):
    """A pin is an input: the pinned node's coordinate is copied, and never moved again.

    The plan's snippet pins ``project-2b3c4d5e`` and expects exactly that
    coordinate back; the rest of the test extends the same rule to the fixture's
    own four pins, to a pinned ``z`` (spec §12.2 step 7: "x/y, and z when
    ``pin_z`` is set"), and to the storage path — where the pin must survive as a
    *value*, whatever key the dict uses.
    """
    nodes = atlas_scale_fixture.with_pin(SCALE_FIRST_PROJECT_KEY, x=12.5, y=-4.25)
    layout = compute_layout(nodes, atlas_scale_fixture.relations, atlas_scale_fixture.groups)
    assert layout[SCALE_FIRST_PROJECT_KEY][:2] == (12.5, -4.25)

    for key in atlas_scale_fixture.pinned_keys:
        pinned = atlas_scale_fixture.node(key)
        assert layout[key][:2] == (pinned.pin_x, pinned.pin_y), f"{key} is pinned"

    pinned_with_z = atlas_scale_fixture.with_pin(SCALE_FIRST_PROJECT_KEY, x=12.5, y=-4.25, z=7.5)
    layout = compute_layout(
        pinned_with_z, atlas_scale_fixture.relations, atlas_scale_fixture.groups
    )
    assert layout[SCALE_FIRST_PROJECT_KEY] == (12.5, -4.25, 7.5), "a pinned z is honoured"

    stored = apply_layout(atlas_scale_fixture.version_obj)
    assert [12.5, -4.25, 7.5] in stored.values(), "the storage path keeps the pin"

    assert _pin_separation(atlas_scale_fixture, layout) >= 0.0, (
        "the final collision pass must push unpinned neighbours clear of a pin"
    )


def test_domains_are_spread_unequally_and_hierarchy_layers_separate(atlas_scale_fixture):
    """Roots do not collapse onto one radius, and the hierarchy produces its layers.

    ``z`` is authored by the depth rule alone (spec §12.2 step 6), so inverting
    the *coordinates* into ``{z: keys}`` and counting the layers is a statement
    about the engine's output rather than about the fixture's own idea of a depth.
    """
    layout = compute_layout(*atlas_scale_fixture.parts)
    radii = sorted(
        math.hypot(x, y) for key, (x, y, _z) in layout.items() if atlas_scale_fixture.is_root(key)
    )
    assert radii[-1] - radii[0] > 5, "deterministic spiral must not collapse to one radius"

    depths = atlas_scale_fixture.z_by_depth(layout)
    assert len(depths) > 1, "hierarchy hints must produce more than one depth layer"
    assert len(depths) == SCALE_DEPTH_LAYERS
    assert set(depths) == {
        0.0,
        round(LAYOUT_CONSTANTS["depth_spread"] / 2, LAYOUT_CONSTANTS["coordinate_decimals"]),
        round(LAYOUT_CONSTANTS["depth_spread"], LAYOUT_CONSTANTS["coordinate_decimals"]),
    }


def test_blueprint_matches_the_spec_pipeline_order(atlas_scale_fixture):
    """The nine stages of spec §12.2, in the order the engine really runs them."""
    assert [stage for stage, _ in atlas_scale_fixture.blueprint()] == [
        "hierarchy-depth",
        "importance-radius",
        "seed-placement",
        "group-attraction",
        "collision-relaxation",
        "depth-layering",
        "pins",
        "final-collision",
        "rounding",
    ]


# ---------------------------------------------------------------------------
# The rules the plan's snippets do not reach
# ---------------------------------------------------------------------------


def test_layout_constants_are_the_frozen_numbers():
    """The plan's recorded numbers, so implementation and tests cannot drift apart."""
    assert LAYOUT_CONSTANTS == {
        "r_min": 6.0,
        "r_max": 18.0,
        "anchor_ratio": 1.45,
        "golden_angle": 2.399963229728653,
        "seed_spacing": 14.0,
        "child_seed_scale": 0.55,
        "group_attraction": 0.18,
        "relaxation_iterations": 120,
        "relaxation_damping": 0.5,
        "max_step_per_iteration": 1.0,
        "gap": 3.0,
        "depth_spread": 9.0,
        "coordinate_decimals": 3,
    }


def test_scale_fixture_matches_the_declared_composition(atlas_scale_fixture):
    """The fixture's own contract: the band, the three levels, the six groups, four pins."""
    nodes, relations, groups = atlas_scale_fixture.parts

    assert len(nodes) == SCALE_NODE_TOTAL == 72
    assert len(relations) == SCALE_RELATION_TOTAL == 136
    assert len(groups) == SCALE_GROUP_TOTAL
    assert sum(len(members) for members in groups.values()) == 36
    assert all(node.visible for node in nodes)
    assert {relation.relation_type.key for relation in relations} == {
        "parent-of",
        "uses",
        "implements",
        "cites",
        "related-to",
    }
    assert max(hierarchy_depths(nodes, relations).values()) == SCALE_DEPTH_LAYERS - 1
    hierarchy_children = {
        relation.target.public_key
        for relation in relations
        if relation.visible and relation.relation_type.hierarchy_role
    }
    assert len(nodes) - len(hierarchy_children) == SCALE_ROOT_TOTAL, (
        "the roots the spiral seeds: the anchor plus every node with no hierarchy parent"
    )
    assert atlas_scale_fixture.node_keys == tuple(sorted(atlas_scale_fixture.node_keys))
    assert len(atlas_scale_fixture.pinned_keys) == SCALE_PINNED_TOTAL == len(SCALE_PINS)

    project = atlas_scale_fixture.node(SCALE_FIRST_PROJECT_KEY)
    assert project.public_key == SCALE_FIRST_PROJECT_KEY
    assert project.pin_x is None, "the snippet's key stays unpinned so a test can pin it"


def test_hierarchy_depths_use_the_longest_path_and_zero_for_the_unreachable(atlas_scale_fixture):
    """§12.2 step 1: the *longest* path from a root, depth 0 when no root reaches a node."""
    nodes, relations, _groups = atlas_scale_fixture.parts
    depths = hierarchy_depths(nodes, relations)
    keys = atlas_scale_fixture.node_keys

    assert depths[next(key for key in keys if key.startswith("identity-"))] == 0
    assert {depths[key] for key in keys if key.startswith("research-area-")} == {1}
    assert {depths[key] for key in keys if key.startswith("project-")} == {2}
    assert max(depths.values()) == 2, "a three-level hierarchy, never a longer chain"

    sideline = ("publication-", "method-", "technology-")
    assert {depths[key] for key in keys if key.startswith(sideline)} == {0}

    # An unequal-path diamond: the leaf must be 2 (a → b → c), not 1 (d → c).
    # ``d`` is a second *root* and sorts after ``b``, so a depth rule that let the
    # last parent processed overwrite the value would report 1 — the reason the
    # keys are fixed here rather than generated.
    version = _version(status="draft", label="atlas-depth-diamond")
    node_type = atlas_scale_fixture.node_types["research-area"]
    root = _node(version=version, node_type=node_type, public_key="research-area-0000000a")
    mid = _node(version=version, node_type=node_type, public_key="research-area-0000000b")
    leaf = _node(version=version, node_type=node_type, public_key="research-area-0000000c")
    shallow = _node(version=version, node_type=node_type, public_key="research-area-0000000d")
    lone = _node(version=version, node_type=node_type, public_key="research-area-0000000e")
    diamond = [
        _relation(
            source=root,
            target=mid,
            relation_type=atlas_scale_fixture.hierarchy_type,
            version=version,
        ),
        _relation(
            source=mid,
            target=leaf,
            relation_type=atlas_scale_fixture.hierarchy_type,
            version=version,
        ),
        _relation(
            source=shallow,
            target=leaf,
            relation_type=atlas_scale_fixture.hierarchy_type,
            version=version,
        ),
        _relation(
            source=root,
            target=lone,
            relation_type=atlas_scale_fixture.related_type,
            version=version,
        ),
    ]
    assert hierarchy_depths([root, mid, leaf, shallow, lone], diamond) == {
        "research-area-0000000a": 0,
        "research-area-0000000b": 1,
        "research-area-0000000c": 2,
        "research-area-0000000d": 0,
        "research-area-0000000e": 0,
    }


def test_apply_layout_stores_public_keyed_quantised_coordinates(atlas_v1):
    """The stored dict: keyed by ``public_key``, three 3-decimal floats per node."""
    version = atlas_v1.version
    layout = apply_layout(version)

    assert set(layout) == _visible_keys(version)
    assert all(isinstance(key, str) and "~" not in key for key in layout)
    for key, coordinate in layout.items():
        assert isinstance(coordinate, list) and len(coordinate) == 3, key
        for value in coordinate:
            assert isinstance(value, float), f"{key}: {value!r} must stay a float"
            assert _stored_exponent(value) >= COORDINATE_EXPONENT, f"{key}: {value!r}"

    assert json.dumps(layout)  # the storage field is JSON, not a float repr
    version.refresh_from_db()
    assert version.layout == layout


def test_apply_layout_increments_the_revision_on_every_apply(atlas_v1):
    """§12.2 step 9: each (re)computation is a new revision — never a silent rewrite."""
    version = atlas_v1.version
    first = apply_layout(version)
    version.refresh_from_db()
    assert version.layout_revision == 2, "the fixture's placeholder layout was revision 1"
    assert version.layout == first

    apply_layout(version)
    version.refresh_from_db()
    assert version.layout_revision == 3, "an unchanged graph still records the recomputation"


def test_reapplying_layout_with_unchanged_rows_moves_nothing(atlas_v1):
    """§12.3's second half: recomputing with unchanged input changes no coordinate."""
    version = atlas_v1.version
    first = apply_layout(version)
    second = apply_layout(version)

    assert second == first, "a recomputation must be a no-op, coordinate for coordinate"
    assert json.dumps(second, sort_keys=True) == json.dumps(first, sort_keys=True)
    version.refresh_from_db()
    assert version.layout == first


def test_invisible_nodes_get_no_coordinate(atlas_v1):
    """Spec §20.1's ``MISSING_LAYOUT`` is about *visible* nodes: a hidden node is not laid out."""
    version = atlas_v1.version
    hidden = _node(version=version, public_key="research-area-0000dead", visible=False)

    layout = compute_layout(list(version.nodes.all()), list(version.relations.all()), [])
    assert hidden.public_key not in layout, "layout covers the projection, not the table"
    assert set(layout) | {hidden.public_key} == _visible_keys(version) | {hidden.public_key}

    stored = apply_layout(version)
    version.refresh_from_db()
    assert hidden.public_key not in stored
    assert hidden.public_key not in version.layout


@pytest.mark.slow
def test_scale_layout_stays_within_the_spec_budget(atlas_scale_fixture):
    """Spec §19.2's measurement shape (80 nodes / 150 relations) and its 2 s budget.

    The ceiling is generous by two orders of magnitude on purpose — the load this
    test really carries is structural: the graph is built at the declared size,
    every node gets a coordinate, the input rows are read and never written, and
    the same rows recompute to the stored layout.
    """
    version = atlas_scale_fixture.version(visible_nodes=80, relations=150)
    nodes, relations, groups = atlas_scale_fixture.parts_of(version)
    assert len(nodes) == 80 and len(relations) == 150
    assert len(hierarchy_depths(nodes, relations)) == 80

    before = _node_state(nodes)
    started = time.perf_counter()
    layout = compute_layout(nodes, relations, groups)
    elapsed = time.perf_counter() - started

    assert len(layout) == 80
    assert _node_state(nodes) == before, (
        "the engine reads its inputs and never writes back to them (spec §12.1)"
    )
    assert compute_layout(nodes, relations, groups) == layout
    assert len(apply_layout(version)) == len(nodes)
    assert elapsed < SCALE_TIMING_CEILING_SECONDS, (
        f"80 nodes / 150 relations took {elapsed:.2f}s; spec §19.2 allows "
        f"{SCALE_TIMING_CEILING_SECONDS}s"
    )
