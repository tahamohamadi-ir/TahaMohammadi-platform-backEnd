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
* **pins are inputs** — a pinned coordinate is copied, never derived, it survives
  the final collision pass and no unpinned neighbour is left inside it (spec §12.2
  steps 7/8, whose guarantee the expulsion post-pass has to enforce), and that pass
  is **global**: it may not trade a pin overlap for any other overlap, so step 8
  cycles `relax → expel` until neither moves and no pair it touches ends slacker
  than it found it (ledger ruling R12);
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
import random
import time
from decimal import Decimal
from itertools import combinations

import pytest

from apps.atlas import layout as layout_module
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

#: The counterexample of the expulsion rule: a pinned research area placed on the
#: project's own unpinned coordinate (``baseline[project][:2]``). The project is
#: the one whose spot the final relaxation cannot repair, and the area is the one
#: the reviewer pinned there.
SCALE_EXPULSION_AREA_KEY = "research-area-00000002"
SCALE_EXPULSION_PROJECT_KEY = "project-0000000e"

# ---------------------------------------------------------------------------
# The pin-expulsion counterexamples of ledger ruling R12 (the `11-fix2` fix list)
# ---------------------------------------------------------------------------

#: C1 — the legal two-pin lens: two ``project`` pins (r = 13.2, importance 60)
#: ``LENS_PIN_ARM`` either side of an unpinned ``technology`` mover (r = 14.4,
#: importance 70). The pins are 40 apart — pin-to-pin slack **+13.600**, legal per
#: §12.5 — and each clearance (``r_mover + r_pin`` + the rounding guard = 27.601)
#: is larger than the 20 units to the midpoint, so the mover starts *inside* both
#: and the two pushes cancel: the shape the old round loop ping-ponged between,
#: ending 15.2 units inside a pin.
LENS_PIN_ARM = 20.0
LENS_PIN_KEYS = ("project-0000000a", "project-0000000b")
LENS_MOVER_KEY = "technology-00000001"
LENS_PIN_IMPORTANCE = 60
LENS_MOVER_IMPORTANCE = 70

#: C3 — twelve coincident movers on one pin's own centre (the degenerate input a
#: legal graph can hand the engine). The old pass placed them all at the pin's
#: clearance radius along the golden-angle turn of their index: deterministic, and
#: overlapping — the worst pair 9.245926 apart against two 13.34 radii in the
#: reviewer's reproduction (slack −17.434).
COINCIDENT_PIN_KEY = "project-0000000a"
COINCIDENT_PIN_IMPORTANCE = 60
COINCIDENT_MOVER_TOTAL = 12
COINCIDENT_MOVER_IMPORTANCE = 70

#: C2 — the seeded sweep: the reviewer's 40 000-geometry sweep is what found the
#: defect (21.8% of legal pin sets ended with a mover inside a pin, 13.2% of all
#: trials were not fixed points), and no test exercised the round bound at all.
#: The sweep below is the same family, bounded and seeded, so a regression in the
#: shape space fails a test instead of a review.
SWEEP_SEED = "atlas-layout-11-fix2-sweep"
SWEEP_TRIALS = 240
SWEEP_MAX_MOVERS = 3
SWEEP_MIDPOINT_SHARE = 1.0 / 3.0


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


def _drawn_radii(atlas_scale_fixture) -> dict[str, float]:
    """The spec's own radius formula (§12.2 step 2, anchor ratio included) per node."""
    low, high = LAYOUT_CONSTANTS["r_min"], LAYOUT_CONSTANTS["r_max"]
    rows = {node.public_key: node for node in atlas_scale_fixture.parts[0]}
    radius = {key: low + (high - low) * (row.importance / 100) for key, row in rows.items()}
    anchor = next((key for key, row in rows.items() if row.node_type.key == "identity"), None)
    if anchor is not None:
        widest = max(value for key, value in radius.items() if key != anchor)
        radius[anchor] = LAYOUT_CONSTANTS["anchor_ratio"] * widest
    return radius


def _pin_separation(atlas_scale_fixture, layout: dict) -> float:
    """The tightest pinned↔unpinned slack: distance minus the two drawn radii.

    Radii follow the spec's own formula (§12.2 step 2, with the anchor ratio),
    so a negative value means the final collision pass (step 8) left an *unpinned*
    neighbour overlapping a pinned node — the pass's whole purpose. Pinned↔pinned
    pairs are excluded on purpose: neither endpoint may move, so an overlap there
    is spec §12.5's ``OVERLAPPING_PINS`` warning (Task 12's), not a layout defect.
    """
    radius = _drawn_radii(atlas_scale_fixture)
    pinned = set(atlas_scale_fixture.pinned_keys)
    slack = [
        math.dist(layout[pinned_key][:2], layout[key][:2]) - (radius[pinned_key] + radius[key])
        for pinned_key in pinned
        for key in layout
        if key not in pinned
    ]
    assert slack, "the fixture pins at least one node and lays out more"
    return min(slack)


def _pin_to_pin_separation(atlas_scale_fixture, layout: dict) -> float:
    """The tightest pinned↔pinned slack — spec §12.5's ``OVERLAPPING_PINS`` boundary.

    A pinned spot that measures below zero here is not a legal pin at all: the
    warning Task 12 raises is the engine's input problem, not its output's.
    """
    radius = _drawn_radii(atlas_scale_fixture)
    pinned = sorted(atlas_scale_fixture.pinned_keys)
    return min(
        math.dist(layout[first][:2], layout[second][:2]) - (radius[first] + radius[second])
        for index, first in enumerate(pinned)
        for second in pinned[index + 1 :]
    )


def _radii_of(nodes) -> dict[str, float]:
    """The spec's drawn radius (§12.2 step 2) per node row, anchor ratio included.

    ``_drawn_radii`` reads the same formula off the scale fixture; the
    counterexample tests build their own rows, so they need it one level down.
    """
    low, high = LAYOUT_CONSTANTS["r_min"], LAYOUT_CONSTANTS["r_max"]
    radius = {node.public_key: low + (high - low) * (node.importance / 100) for node in nodes}
    anchor = next((node.public_key for node in nodes if node.node_type.key == "identity"), None)
    if anchor is not None:
        widest = max(value for key, value in radius.items() if key != anchor)
        radius[anchor] = LAYOUT_CONSTANTS["anchor_ratio"] * widest
    return radius


def _pair_slacks(positions: dict, radius: dict) -> dict[tuple[str, str], float]:
    """``{(key_a, key_b): distance − (r_a + r_b)}`` for every pair, keys sorted.

    The one measure the pass is judged by: a *negative* slack is an overlap, and
    a pair whose slack dropped across the pass is a pair the pass made worse
    (ledger ruling R12 (b)).
    """
    keys = sorted(positions)
    return {
        (first, second): math.dist(positions[first][:2], positions[second][:2])
        - (radius[first] + radius[second])
        for index, first in enumerate(keys)
        for second in keys[index + 1 :]
    }


def _pipeline_positions(nodes, relations=(), groups=()):
    """``(end of step 7, end of step 8, end of step 9)`` positions for one run.

    The blueprint is split at the ``final-collision`` boundary so a test can
    snapshot every pair *before* the pass under test and compare it with the same
    pairs after: :func:`_stage_final_collision` is the exact call
    :func:`compute_layout` makes, and :func:`_stage_rounding` closes the pipeline
    as it does there. ``before`` is the state the pass receives, ``after`` its
    output (pre-rounding, where a repair is still visible), ``rounded`` the
    stored shape.
    """
    run = layout_module._prepare(nodes, relations, groups)
    for stage in layout_module.LAYOUT_BLUEPRINT[:7]:
        stage.run(run)
    before = {key: tuple(run.position[key]) for key in run.keys}
    layout_module._stage_final_collision(run)
    after = {key: tuple(run.position[key]) for key in run.keys}
    layout_module._stage_rounding(run)
    rounded = {key: tuple(run.position[key]) for key in run.keys}
    return before, after, rounded


def _hand_built_run(nodes, positions: dict):
    """A prepared run whose coordinates are the counterexample's, set by hand.

    The counterexample geometries are unstable saddles: the lens is only a lens
    when the two pin distances are *exactly* equal, and a one-ulp asymmetry in a
    seed's float lets the step-5 relaxation escape it on one machine and stall on
    another. The pass is what the counterexamples target, so they are handed to
    it directly — the same way the reviewer rebuilt them from duck-typed rows —
    and the seed stages still run first, so radii, pins and ordering are the
    engine's own.
    """
    run = layout_module._prepare(nodes, (), ())
    for stage in layout_module.LAYOUT_BLUEPRINT[:6]:
        stage.run(run)
    for key, (x, y) in positions.items():
        run.position[key][0], run.position[key][1] = x, y
    layout_module._stage_pins(run)
    before = {key: tuple(run.position[key]) for key in run.keys}
    return run, before


def _mover_slack(pairs: dict, mover_keys: set[str], pinned_keys: set[str]) -> float:
    """The tightest slack over the pairs that join a mover to a pinned node."""
    return min(
        slack
        for (first, second), slack in pairs.items()
        if (first in pinned_keys and second in mover_keys)
        or (second in pinned_keys and first in mover_keys)
    )


def _unpinned_pair_slack(pairs: dict, pinned_keys: set[str]) -> float:
    """The tightest slack over the pairs with **no** pinned endpoint (R12 (b))."""
    return min(
        slack
        for (first, second), slack in pairs.items()
        if first not in pinned_keys and second not in pinned_keys
    )


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


def test_a_legal_pin_expels_every_neighbour_the_relaxation_cannot(atlas_scale_fixture):
    """Spec §12.2 step 8's guarantee, at the one shape the fixture's own pins cannot reach.

    "A pin cannot leave an unpinned neighbour overlapping it" — the fixture's four
    pins satisfy it, but a *legal* pin placed on an unpinned project's own
    coordinate does not: the project's still-overlapping neighbours cancel the
    pin's push for the whole iteration budget and the final relaxation ends with
    the project inside the pin (5.632 units, measured before the expulsion
    post-pass). The spot is finite and clear of every other pin, so the guarantee
    has to hold here — the expulsion pass is what makes it true, because a force
    pass cannot promise a placement a geometry pass can.
    """
    baseline = compute_layout(*atlas_scale_fixture.parts)
    spot = baseline[SCALE_EXPULSION_PROJECT_KEY][:2]
    assert all(math.isfinite(value) for value in spot), "the counterexample's spot is in bounds"

    nodes = atlas_scale_fixture.with_pin(SCALE_EXPULSION_AREA_KEY, x=spot[0], y=spot[1])
    layout = compute_layout(nodes, atlas_scale_fixture.relations, atlas_scale_fixture.groups)

    assert layout[SCALE_EXPULSION_AREA_KEY][:2] == (spot[0], spot[1]), (
        "a pin is copied in, never derived — the expulsion pass may not move it"
    )
    assert _pin_to_pin_separation(atlas_scale_fixture, layout) >= 0.0, (
        "the counterexample's spot is legal: it clears every other pin (spec §12.5)"
    )
    assert _pin_separation(atlas_scale_fixture, layout) >= 0.0, (
        "no unpinned neighbour may be left inside a pin (spec §12.2 step 8)"
    )

    digest = json.dumps(layout, sort_keys=True)
    assert (
        json.dumps(
            compute_layout(nodes, atlas_scale_fixture.relations, atlas_scale_fixture.groups),
            sort_keys=True,
        )
        == digest
    ), "the expulsion post-pass must not move a recomputation"
    reversed_order = compute_layout(*atlas_scale_fixture.parts_reversed())
    assert json.dumps(reversed_order, sort_keys=True) == digest, (
        "and the arrival order of the rows must not change the digest"
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


def test_compute_layout_materialises_a_one_shot_relations_iterator(atlas_scale_fixture):
    """The annotation says ``Iterable``, so a generator must be as good as a list.

    ``_prepare`` used to build the hierarchy arcs from the iterator and *then*
    store ``tuple(relations)`` — empty for anything one-shot. Stage 1 then
    recomputed every depth as 0, the seed stage walked the projects before their
    parent areas and the call raised ``KeyError``. Materialising once at the top
    is the whole contract of the annotation: same coordinates, same digest, for a
    generator, a ``filter`` and a list.
    """
    nodes, relations, groups = atlas_scale_fixture.parts
    expected = compute_layout(nodes, relations, groups)
    digest = json.dumps(expected, sort_keys=True)

    from_generator = compute_layout(nodes, (relation for relation in relations), groups)
    from_filter = compute_layout(nodes, filter(lambda relation: True, relations), groups)

    assert from_generator == expected, "a generator must lay out exactly like a list"
    assert from_filter == expected, "and so must a filter"
    assert json.dumps(from_generator, sort_keys=True) == digest
    assert json.dumps(from_filter, sort_keys=True) == digest


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


def test_a_cyclic_hierarchy_is_tolerated_deterministically(atlas_scale_fixture):
    """F3: validation rejects a cycle first (``HIERARCHY_CYCLE``), but a direct call may not crash.

    ``hierarchy_depths`` answers a 2-cycle with 0 for every node of the cycle, and
    the engine has to agree: the seed stage used to raise a bare ``KeyError`` while
    looking for a parent no root ever seeded. The documented fallback places a node
    no seeded parent reaches on the root spiral, after the roots, in ``public_key``
    order — the same coordinates on every call and in every arrival order.
    """
    version = _version(status="draft", label="atlas-layout-cycle")
    node_type = atlas_scale_fixture.node_types["research-area"]
    first = _node(version=version, node_type=node_type, public_key="research-area-00000031")
    second = _node(version=version, node_type=node_type, public_key="research-area-00000032")
    tail = _node(version=version, node_type=node_type, public_key="research-area-00000033")
    cycle = [
        _relation(
            source=first,
            target=second,
            relation_type=atlas_scale_fixture.hierarchy_type,
            version=version,
        ),
        _relation(
            source=second,
            target=first,
            relation_type=atlas_scale_fixture.hierarchy_type,
            version=version,
        ),
        _relation(
            source=second,
            target=tail,
            relation_type=atlas_scale_fixture.hierarchy_type,
            version=version,
        ),
    ]
    nodes = [first, second, tail]

    assert hierarchy_depths(nodes, cycle) == {
        "research-area-00000031": 0,
        "research-area-00000032": 0,
        "research-area-00000033": 0,
    }, "no root reaches a cycle, and the cycle's tail is unreachable with it"

    layout = compute_layout(nodes, cycle, [])
    assert set(layout) == {node.public_key for node in nodes}
    assert compute_layout(nodes, cycle, []) == layout, "the fallback is deterministic"
    assert compute_layout(list(reversed(nodes)), list(reversed(cycle)), []) == layout, (
        "and it does not depend on the arrival order of the rows"
    )


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


# ---------------------------------------------------------------------------
# The pin-expulsion counterexamples (ledger ruling R12 — the `11-fix2` fix list)
# ---------------------------------------------------------------------------


def test_a_legal_two_pin_lens_clears_the_mover_without_worsening_a_pair(atlas_scale_fixture):
    """C1: a mover inside two *legal* pins ends outside both, and no pair pays for it.

    The pins are 40 apart (pin-to-pin slack +13.600, legal per §12.5) and the
    mover starts on the midpoint, inside both clearance circles (27.601 each)
    with the two pushes cancelling — the shape the final relaxation cannot
    repair. The old round loop ping-ponged it between the two clearance circles
    and stopped after ``len(pinned) + 1`` rounds **inside** the other pin: min
    slack −15.202, the arm-20 member of the reviewer's −5.202/… family. The
    global pass has to leave it clear of both, and — because R12 makes step 8's
    pair set every pair — leave no pair slacker than the pass found it.
    """
    version = _version(status="draft", label="atlas-layout-lens")
    node_types = atlas_scale_fixture.node_types
    left, right = LENS_PIN_KEYS
    nodes = [
        _node(
            version=version,
            node_type=node_types["project"],
            public_key=left,
            importance=LENS_PIN_IMPORTANCE,
            pin_x=-LENS_PIN_ARM,
            pin_y=0.0,
        ),
        _node(
            version=version,
            node_type=node_types["project"],
            public_key=right,
            importance=LENS_PIN_IMPORTANCE,
            pin_x=LENS_PIN_ARM,
            pin_y=0.0,
        ),
        _node(
            version=version,
            node_type=node_types["technology"],
            public_key=LENS_MOVER_KEY,
            importance=LENS_MOVER_IMPORTANCE,
        ),
    ]
    radius = _radii_of(nodes)
    assert radius[left] == radius[right] == pytest.approx(13.2)
    assert radius[LENS_MOVER_KEY] == pytest.approx(14.4)
    assert math.dist((-LENS_PIN_ARM, 0.0), (LENS_PIN_ARM, 0.0)) - (13.2 + 13.2) == pytest.approx(
        13.6
    ), "the counterexample's two pins are legal (spec §12.5)"

    run, before = _hand_built_run(nodes, {LENS_MOVER_KEY: (0.0, 0.0)})
    before_pairs = _pair_slacks(before, radius)
    assert before_pairs[(left, LENS_MOVER_KEY)] == pytest.approx(-7.6), (
        "the mover starts exactly 20 from each pin, i.e. inside both"
    )

    layout_module._stage_final_collision(run)
    after = {key: tuple(run.position[key]) for key in run.keys}
    after_pairs = _pair_slacks(after, radius)

    assert _mover_slack(after_pairs, {LENS_MOVER_KEY}, set(LENS_PIN_KEYS)) >= 0.0, (
        "no unpinned mover may be left inside a pin (spec §12.2 step 8)"
    )
    worsened = {
        pair: (before_pairs[pair], after_pairs[pair])
        for pair in before_pairs
        if after_pairs[pair] < before_pairs[pair]
    }
    assert not worsened, f"the pass left {len(worsened)} pair(s) slacker: {worsened}"

    settled = after
    layout_module._stage_final_collision(run)
    assert {key: tuple(run.position[key]) for key in run.keys} == settled, (
        "the pass is a fixpoint: a second pass over its own output moves nothing"
    )

    _final_before, _final_after, rounded = _pipeline_positions(nodes)
    assert rounded == compute_layout(nodes, (), ()), (
        "the split blueprint equals the public path, coordinate for coordinate"
    )
    assert _mover_slack(_pair_slacks(rounded, radius), {LENS_MOVER_KEY}, set(LENS_PIN_KEYS)) >= (
        0.0
    ), "the guarantee survives the 3-decimal rounding that ends the pipeline"


def test_the_pinned_counterexample_does_not_trade_a_pin_for_a_mover_overlap(atlas_scale_fixture):
    """R12 (b), end-to-end: repairing the F2 pin may not push the mover into a neighbour.

    The F2 shape — the fixture's own legal pin landed on ``project-0000000e``'s
    coordinate — used to leave the worst *unpinned↔unpinned* pair at −9.095 where
    the pass found it at −2.976: the expulsion moved the project out of the pin
    and into ``project-00000003``, and stopped there (R12's measured regression).
    Step 8's pair set is global, so the pass has to end at least as good as it
    found every pair it touched — the fixpoint loop is what makes that true.
    """
    baseline = compute_layout(*atlas_scale_fixture.parts)
    spot = baseline[SCALE_EXPULSION_PROJECT_KEY][:2]
    nodes = atlas_scale_fixture.with_pin(SCALE_EXPULSION_AREA_KEY, x=spot[0], y=spot[1])
    radius = _drawn_radii(atlas_scale_fixture)
    pinned = set(atlas_scale_fixture.pinned_keys)
    pinned_pair = tuple(sorted((SCALE_EXPULSION_AREA_KEY, SCALE_EXPULSION_PROJECT_KEY)))

    before, after, rounded = _pipeline_positions(
        nodes, atlas_scale_fixture.relations, atlas_scale_fixture.groups
    )
    before_pairs = _pair_slacks(before, radius)
    after_pairs = _pair_slacks(after, radius)

    assert _unpinned_pair_slack(before_pairs, pinned) > 0.0, (
        "the pass receives a graph whose unpinned pairs are already clear"
    )
    assert after_pairs[pinned_pair] >= 0.0, (
        "the pin's own neighbour is still expelled clear of it"
    )
    assert _pin_separation(atlas_scale_fixture, rounded) >= 0.0, (
        "and the rounded output keeps every unpinned node clear of every pin"
    )
    assert _unpinned_pair_slack(after_pairs, pinned) >= 0.0, (
        "the repair may not leave the unpinned pairs overlapping: the reviewer "
        "measured the old pass ending at −9.095 where it found −2.976 (R12 (b), "
        "step 8's pair set is global)"
    )
    assert min(after_pairs.values()) >= min(before_pairs.values()), (
        "and the tightest pair of any kind may not come out slacker than it went in"
    )


def test_twelve_coincident_movers_at_a_pin_centre_end_apart(atlas_scale_fixture):
    """C3: twelve coincident movers: still deterministic, now separated.

    The degenerate input is legal — twelve unpinned nodes may share one spot, and
    one of the fixture's node types can be pinned there. The old pass placed them
    all at the pin's clearance radius along the golden-angle turn of their index:
    deterministic (the docstring claimed exactly that much) but overlapping — the
    worst pair 9.245926 apart against two 13.34 radii in the reviewer's
    reproduction, slack **−17.434**. The pass has to leave a positive slack on
    every mover↔mover pair, which means the loop has to run: the single placement
    is the pass, the separation is the pass's obligation. This shape needs more
    cycles to reach a full fixpoint than the bounded loop grants, so the second
    pass here is asked for its *invariants* — still clear of the pin, still apart —
    not for coordinate equality (the bound is documented on ``_FIXPOINT_CYCLES``).
    """
    version = _version(status="draft", label="atlas-layout-coincident")
    node_types = atlas_scale_fixture.node_types
    nodes = [
        _node(
            version=version,
            node_type=node_types["project"],
            public_key=COINCIDENT_PIN_KEY,
            importance=COINCIDENT_PIN_IMPORTANCE,
            pin_x=0.0,
            pin_y=0.0,
        )
    ]
    nodes += [
        _node(
            version=version,
            node_type=node_types["technology"],
            public_key=f"technology-{index:08x}",
            importance=COINCIDENT_MOVER_IMPORTANCE,
        )
        for index in range(1, COINCIDENT_MOVER_TOTAL + 1)
    ]
    radius = _radii_of(nodes)
    mover_keys = {node.public_key for node in nodes if node.public_key != COINCIDENT_PIN_KEY}
    assert len(mover_keys) == COINCIDENT_MOVER_TOTAL

    run, before = _hand_built_run(nodes, {node.public_key: (0.0, 0.0) for node in nodes})
    before_pairs = _pair_slacks(before, radius)
    assert min(before_pairs.values()) == pytest.approx(-2 * radius[next(iter(mover_keys))]), (
        "twelve movers coincident on the pin centre"
    )

    layout_module._stage_final_collision(run)
    after = {key: tuple(run.position[key]) for key in run.keys}
    after_pairs = _pair_slacks(after, radius)

    mover_pairs = {
        pair: slack
        for pair, slack in after_pairs.items()
        if pair[0] in mover_keys and pair[1] in mover_keys
    }
    worst = min(mover_pairs.items(), key=lambda item: item[1])
    assert worst[1] > 0.0, (
        f"coincident movers must end apart, not stacked on the pin's rays: {worst} "
        f"(spec §12.2 step 5's to-relaxation is the pass's own job on its output)"
    )
    assert _mover_slack(after_pairs, mover_keys, {COINCIDENT_PIN_KEY}) >= 0.0, (
        "none of the twelve may be left inside the pin"
    )
    worsened = {
        pair: (before_pairs[pair], after_pairs[pair])
        for pair in before_pairs
        if after_pairs[pair] < before_pairs[pair]
    }
    assert not worsened, f"the pass left {len(worsened)} pair(s) slacker: {worsened}"

    layout_module._stage_final_collision(run)
    second = {key: tuple(run.position[key]) for key in run.keys}
    second_pairs = _pair_slacks(second, radius)
    assert _mover_slack(second_pairs, mover_keys, {COINCIDENT_PIN_KEY}) >= 0.0, (
        "a second pass keeps the twelve clear of the pin"
    )
    assert (
        min(
            slack
            for pair, slack in second_pairs.items()
            if pair[0] in mover_keys and pair[1] in mover_keys
        )
        > 0.0
    ), "and keeps them apart (this shape needs more cycles than the bound; the bound is documented)"


def test_a_hand_built_ping_pong_settles_and_stays_settled(atlas_scale_fixture):
    """C4: two pins whose clearance circles overlap: one pass settles, a second holds.

    With the pins ``clearance − gap`` either side of the mover, the best placement
    available is short of the clearance by exactly ``gap`` — and the old round
    loop alternated between the two clearance circles indefinitely (the reviewer
    measured 500 rounds without convergence; the shipped bound stopped after
    ``len(pinned) + 1`` at twice the best shortfall). The pass has to choose one
    placement, clear every pin it can clear, and then stay: a second pass over its
    own output must move nothing.
    """
    version = _version(status="draft", label="atlas-layout-ping-pong")
    node_types = atlas_scale_fixture.node_types
    guard = 10.0 ** -LAYOUT_CONSTANTS["coordinate_decimals"]
    arm = 14.4 + 13.2 + guard - LAYOUT_CONSTANTS["gap"]
    left, right = LENS_PIN_KEYS
    nodes = [
        _node(
            version=version,
            node_type=node_types["project"],
            public_key=left,
            importance=LENS_PIN_IMPORTANCE,
            pin_x=-arm,
            pin_y=0.0,
        ),
        _node(
            version=version,
            node_type=node_types["project"],
            public_key=right,
            importance=LENS_PIN_IMPORTANCE,
            pin_x=arm,
            pin_y=0.0,
        ),
        _node(
            version=version,
            node_type=node_types["technology"],
            public_key=LENS_MOVER_KEY,
            importance=LENS_MOVER_IMPORTANCE,
        ),
    ]
    radius = _radii_of(nodes)
    run, before = _hand_built_run(nodes, {LENS_MOVER_KEY: (0.0, 0.0)})
    before_pairs = _pair_slacks(before, radius)
    inside = arm - (radius[LENS_MOVER_KEY] + radius[left])
    assert before_pairs[(left, LENS_MOVER_KEY)] == pytest.approx(inside), (
        "the mover starts inside both pins by the gap"
    )
    assert before_pairs[(right, LENS_MOVER_KEY)] == pytest.approx(inside)

    layout_module._stage_final_collision(run)
    after = {key: tuple(run.position[key]) for key in run.keys}
    after_pairs = _pair_slacks(after, radius)

    assert _mover_slack(after_pairs, {LENS_MOVER_KEY}, set(LENS_PIN_KEYS)) >= 0.0, (
        "the pass may not leave the mover inside a pin, however tight the lens is"
    )
    worsened = {
        pair: (before_pairs[pair], after_pairs[pair])
        for pair in before_pairs
        if after_pairs[pair] < before_pairs[pair]
    }
    assert not worsened, f"the pass left {len(worsened)} pair(s) slacker: {worsened}"

    settled = after
    layout_module._stage_final_collision(run)
    assert {key: tuple(run.position[key]) for key in run.keys} == settled, (
        "the ping-pong's 500 rounds are now a fixpoint: the second pass moves nothing"
    )


@pytest.mark.slow
def test_a_seeded_sweep_of_legal_pin_sets_clears_every_pin_and_worsens_no_pair(
    atlas_scale_fixture,
):
    """C2, bounded and seeded: the legal pin sets the round bound used to lose.

    The reviewer's 40 000-geometry sweep is what found the defect — 21.8% of the
    21 945 legal pin sets ended with a mover still inside a pin, 1 788 of them
    never converging (one needed 153 rounds) — and *no test exercised the round
    bound at all*, which is how the round loop shipped. This sweep rebuilds the
    same family on a fixed seed, at the level the defect lives on: two pins whose
    separation makes them legal (spec §12.5) with one to three unpinned movers
    placed in their neighbourhood, so the lens, coincident and single-pin shapes
    all occur (a third of the movers sit exactly on the pins' midpoint).

    Three obligations, each measured on every geometry: no mover may end inside a
    pin; the tightest pair may not come out slacker than it went in; and no pair
    that was already overlapping may end *deeper* — the literal R12 (b) shape
    (pairs that were clear are allowed to come closer, because the relaxation in
    the same pass is doing its own work, and it does so in a quarter of these
    geometries; an overlap going deeper is never allowed). The old pass fails the
    first two on 33 and 26 geometries of this seed respectively, and the overlap
    obligation on the same 33.
    """
    node_types = atlas_scale_fixture.node_types
    checked = 0
    start_inside = 0
    for trial in range(SWEEP_TRIALS):
        rng = random.Random(f"{SWEEP_SEED}:{trial}")
        version = _version(status="draft", label=f"atlas-layout-sweep-{trial:04d}")
        pin_importance = rng.randint(20, 90)
        mover_importance = rng.randint(20, 90)
        pin_keys = [f"project-{index:08x}" for index in (1, 2)]
        nodes = [
            _node(
                version=version,
                node_type=node_types["project"],
                public_key=key,
                importance=pin_importance,
            )
            for key in pin_keys
        ]
        nodes += [
            _node(
                version=version,
                node_type=node_types["technology"],
                public_key=f"technology-{index:08x}",
                importance=mover_importance,
            )
            for index in range(1, rng.randint(1, SWEEP_MAX_MOVERS) + 1)
        ]
        radius = _radii_of(nodes)
        mover_keys = {node.public_key for node in nodes if node.public_key not in pin_keys}

        # A legal pin pair (spec §12.5), symmetric about the origin; the angle is
        # an axis half the time, so the mover's two distances are bit-equal and
        # the lens is an exact saddle rather than a one-ulp accident.
        separation = 2 * radius[pin_keys[0]] + rng.uniform(
            0.0, 2.0 * (radius[next(iter(mover_keys))] + radius[pin_keys[0]])
        )
        angle = rng.choice((0.0, 0.5 * math.pi, rng.uniform(0.0, 2.0 * math.pi)))
        half = 0.5 * separation
        plan = {
            pin_keys[0]: (-half * math.cos(angle), -half * math.sin(angle)),
            pin_keys[1]: (half * math.cos(angle), half * math.sin(angle)),
        }
        reach = 1.5 * (radius[next(iter(mover_keys))] + radius[pin_keys[0]])
        for key in sorted(mover_keys):
            plan[key] = (
                (0.0, 0.0)
                if rng.random() < SWEEP_MIDPOINT_SHARE
                else (rng.uniform(-reach, reach), rng.uniform(-reach, reach))
            )
        if any(
            math.dist(plan[first], plan[second]) < radius[first] + radius[second]
            for first, second in combinations(pin_keys, 2)
        ):
            continue  # cannot happen with the draw above; kept as a real legality gate

        for key in pin_keys:  # only the pin pair carries a pin; the movers stay movers
            x, y = plan[key]
            row = next(row for row in nodes if row.public_key == key)
            row.pin_x, row.pin_y = x, y

        run, before = _hand_built_run(nodes, plan)
        before_pairs = _pair_slacks(before, radius)
        if _mover_slack(before_pairs, mover_keys, set(pin_keys)) < 0.0:
            start_inside += 1

        layout_module._stage_final_collision(run)
        after = {key: tuple(run.position[key]) for key in run.keys}
        after_pairs = _pair_slacks(after, radius)

        worst_pin = _mover_slack(after_pairs, mover_keys, set(pin_keys))
        assert worst_pin >= 0.0, (
            f"trial {trial}: a mover ended inside a pin (slack {worst_pin:.6f}); pins={plan}"
        )
        assert min(after_pairs.values()) >= min(before_pairs.values()), (
            f"trial {trial}: the tightest pair came out slacker than it went in "
            f"({min(before_pairs.values()):.6f} → {min(after_pairs.values()):.6f})"
        )
        overlap_worse = {
            pair: (before_pairs[pair], after_pairs[pair])
            for pair in before_pairs
            if before_pairs[pair] < 0.0 and after_pairs[pair] < before_pairs[pair]
        }
        assert not overlap_worse, (
            f"trial {trial}: the pass deepened {len(overlap_worse)} overlap(s): {overlap_worse}"
        )
        checked += 1

    assert start_inside >= SWEEP_TRIALS // 3, (
        f"only {start_inside} of {SWEEP_TRIALS} geometries started with a mover inside a "
        "pin — the sweep is meant to target the shapes the pass exists for"
    )
    assert checked == SWEEP_TRIALS
