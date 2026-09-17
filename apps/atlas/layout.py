"""Deterministic layout engine (spec §12) — plan Task 11.

One authority, one set of coordinates: the engine is computed once per version
revision and stored (spec §12.1), so the 3D scene, the 2D projection, the About
preview and the admin editor all read the same numbers and never re-simulate
them.

Pipeline (spec §12.2, in the order :data:`LAYOUT_BLUEPRINT` declares it — the
constant *is* the execution order, not documentation about it):

1. hierarchy depth (longest path from a root, over the visible hierarchy-role
   subgraph; a node no root reaches is depth 0 — a placement ring slot, the
   `ISOLATED_NODE` warning's shape, not an error);
2. importance radius (``r_min + (r_max - r_min) · importance/100``, and the
   ``identity`` anchor at ``anchor_ratio`` × the widest domain radius);
3. seed placement (a golden-angle spiral for the roots, a smaller spiral around
   the parent for everything else);
4. group attraction (toward the mean of the node's group centroids, computed
   from the **seed** positions, so the force is a pure function of the input);
5. importance-aware collision relaxation (a fixed 120 iterations, damped,
   one scene unit of movement per node per iteration at most);
6. depth layering (``z`` spread by depth — ``z`` has no other author);
7. pins (copied in, never derived);
8. a final relaxation restricted to unpinned nodes, then an exact expulsion of any
   unpinned mover the relaxation left inside a pin, so a pin cannot leave a
   neighbour overlapping it;
9. rounding to 3 decimals, once.

Determinism (spec §12.3, binding): every collection is walked in ``public_key``
order, no ``random``/``time``/``hash``/collation input exists, all arithmetic is
IEEE-754 double, and rounding happens once at the end. ``set``/``dict`` values
appear only as membership tests and lookups — never as an iteration source.

Scope decisions the spec and the plan leave open, recorded here so no reader has
to re-derive them:

* **only visible nodes are laid out.** The stored layout is the projection's, so
  a hidden node has no coordinate (spec §20.1's ``MISSING_LAYOUT`` is about a
  *visible* node) and the row stays inert until it is shown and the layout is
  recomputed — which is exactly what "computed once per revision" means;
* **the final collision pass is a full pass, not a single iteration.** Spec §12.2
  step 8 calls it "one more relaxation pass restricted to unpinned nodes, so a pin
  cannot leave an unpinned neighbour overlapping it" — a single iteration bounded
  at one scene unit per node cannot discharge that purpose, so the pass repeats
  the frozen iteration count with the pinned nodes held still as obstacles. A force
  pass cannot *promise* the outcome either (a pinned area sitting on an unpinned
  project's own coordinate holds it inside the pin for the whole budget), so step 8
  ends with :func:`_expel_from_pins` — a geometric pass that puts any unpinned mover
  still inside a pin exactly on the pin's clearance circle, and with it the
  guarantee becomes true of the output rather than of the intent;
* **a cyclic hierarchy is tolerated deterministically.** Validation rejects one
  first (``HIERARCHY_CYCLE``), but the engine is also callable on its own rows: a
  node no *seeded* parent reaches is seeded on the root spiral after the roots, in
  ``public_key`` order (step 3's documented fallback), and its depth stays ``0`` —
  the same answer :func:`hierarchy_depths` gives. No input raises a bare
  ``KeyError``;
* **groups arrive as membership, not as rows.** ``AtlasGroup`` carries no members,
  so the engine's ``groups`` argument is ``{group key: member node keys}`` (a
  mapping, or ``(key, keys)`` pairs). That keeps the engine query-free and lets
  :func:`apply_layout` read the memberships in one query; it is the only input
  shape under which "a node in two groups is attracted to the midpoint of the two
  centroids" (spec §12.4) is a pure function;
* **the mean inter-root spacing** of spec §12.4's displacement bound is the mean
  distance between consecutive roots in ``public_key`` order — the cheapest
  spacing measure that is a function of the same ordered roots the spiral uses;
* **a node from a stale group membership is dropped**, because the engine can
  only place the nodes it was handed.

The module performs **no queries of its own**: :func:`compute_layout` reads the
rows it is given (pass them with ``select_related`` at scale) and
:func:`apply_layout` is the single ORM adapter — one query set per collection,
one save.
"""

from __future__ import annotations

import heapq
import itertools
import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from apps.atlas.models import AtlasGroupMembership, AtlasNode, AtlasRelation, AtlasVersion

#: The plan's frozen numbers (plan Task 11 step 3; spec §12.2). Recorded here so
#: the implementation and the tests agree on every scene unit the engine uses.
LAYOUT_CONSTANTS: dict[str, float | int] = {
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

#: The node type whose single node is the scene's anchor (spec §6.1): it draws at
#: ``anchor_ratio`` × the widest domain radius.
ANCHOR_NODE_TYPE_KEY = "identity"

def _public_key(row: Any) -> str:
    """The ``public_key`` of a row — the one ordering rule of the pipeline (spec §12.3)."""
    return row.public_key


@dataclass(frozen=True, slots=True)
class LayoutStage:
    """One pipeline stage: its name (spec §12.2's step label), purpose and body."""

    name: str
    description: str
    run: Callable[[_Run], None]


@dataclass
class _Run:
    """The working state of one layout run — everything the stages read and write."""

    #: The visible hierarchy subgraph, as sorted ``(parent, child)`` arcs — built
    #: **once**, from the materialised relations, and read by stage 1 alone.
    arcs: tuple[tuple[str, str], ...]
    #: ``{group key: member node keys}``, members sorted and visible-only.
    members: dict[str, tuple[str, ...]]
    #: Every visible node key, sorted — the single ordered walk of every stage.
    keys: tuple[str, ...]
    #: ``{node pk: public key}``, so hierarchy arcs resolve without a lazy FK load.
    by_pk: dict[int, str]
    #: ``{node key: (pin_x, pin_y, pin_z)}`` — ``pin_z`` may be ``None``.
    pins: dict[str, tuple[float | None, float | None, float | None]]
    #: ``{node key: importance}``.
    importance: dict[str, int]
    #: ``{node key: (parent keys)}`` and its inverse, from the visible hierarchy arcs.
    parents: dict[str, tuple[str, ...]] = field(default_factory=dict)
    children: dict[str, tuple[str, ...]] = field(default_factory=dict)
    #: The identity anchor's key, when the version has one.
    anchor_key: str | None = None
    #: ``{node key: (group keys)}`` — built once so no stage rebuilds it.
    node_groups: dict[str, tuple[str, ...]] = field(default_factory=dict)
    #: Filled by the stages, in :data:`LAYOUT_BLUEPRINT` order.
    depth: dict[str, int] = field(default_factory=dict)
    radius: dict[str, float] = field(default_factory=dict)
    seed: dict[str, tuple[float, float]] = field(default_factory=dict)
    position: dict[str, list[float]] = field(default_factory=dict)
    root_keys: tuple[str, ...] = ()
    max_depth: int = 0
    mean_root_spacing: float = 0.0
    #: Membership only — never iterated, so it cannot leak an ordering.
    pinned: set[str] = field(default_factory=set)


# ---------------------------------------------------------------------------
# Input normalisation — the pure helpers every stage and the public functions share
# ---------------------------------------------------------------------------


def _visible_by_pk(nodes: Iterable[AtlasNode]) -> dict[int, str]:
    """``{node pk: public key}`` for the visible nodes of the passed rows."""
    return {node.pk: node.public_key for node in nodes if node.visible}


def _visible_keys(nodes: Iterable[AtlasNode]) -> tuple[str, ...]:
    """Every visible node key, sorted — the order every stage walks."""
    return tuple(sorted(node.public_key for node in nodes if node.visible))


def _hierarchy_arcs(by_pk: Mapping[int, str], relations: Iterable[AtlasRelation]):
    """The visible hierarchy subgraph as sorted ``(parent, child)`` arcs.

    A relation counts when it is visible, its type has ``hierarchy_role`` and both
    of its endpoints are visible nodes of this graph (spec §5.6/§12.2 step 1).
    Endpoints are read by id and resolved through ``by_pk``, so the engine never
    triggers a lazy foreign-key load inside its loops; a relation whose endpoint
    is missing is skipped rather than raising.
    """
    arcs = set()
    for relation in relations:
        if not relation.visible or not relation.relation_type.hierarchy_role:
            continue
        parent = by_pk.get(relation.source_id)
        child = by_pk.get(relation.target_id)
        if parent is None or child is None or parent == child:
            continue
        arcs.add((parent, child))
    return tuple(sorted(arcs))


def _depth_by_arcs(keys: Iterable[str], arcs: Iterable[tuple[str, str]]) -> dict[str, int]:
    """``{public key: depth}`` — the longest hierarchy path from any root.

    A Kahn-style pass over the arcs whose ready set is a heap, so the walk order
    is a property of the keys rather than of the row order (spec §12.2 step 1).
    Depth is the **longest** path, and a node no root reaches is ``0`` — including
    every node of a cyclic input, which validation rejects long before this point
    (``HIERARCHY_CYCLE``) and which the seed stage places with its documented
    fallback, so the two functions answer the same 2-cycle with the same zeros.
    """
    depth = dict.fromkeys(keys, 0)
    children: dict[str, set[str]] = {}
    unresolved = dict.fromkeys(depth, 0)
    for parent, child in arcs:
        if parent not in depth or child not in depth:
            continue
        seen = children.setdefault(parent, set())
        if child in seen:
            continue
        seen.add(child)
        unresolved[child] += 1

    ready = [key for key in sorted(depth) if unresolved[key] == 0]
    heapq.heapify(ready)
    while ready:
        parent = heapq.heappop(ready)
        for child in sorted(children.get(parent, ())):
            depth[child] = max(depth[child], depth[parent] + 1)
            unresolved[child] -= 1
            if unresolved[child] == 0:
                heapq.heappush(ready, child)
    return depth


def _normalise_groups(groups, visible: set[str]) -> dict[str, tuple[str, ...]]:
    """``{group key: member node keys}`` — deduplicated, sorted, visible members only.

    Accepts a mapping or an iterable of ``(group key, member keys)`` pairs. A
    member that is not a visible node of this graph is dropped (the engine can
    only place the nodes it was given) and a group left with no members is
    dropped with it, because it has nothing to attract.
    """
    items = groups.items() if isinstance(groups, Mapping) else groups
    members: dict[str, tuple[str, ...]] = {}
    for key, member_keys in items:
        kept = sorted({member for member in member_keys if member in visible})
        if kept:
            members[str(key)] = tuple(kept)
    return members


def _spiral(spacing: float, index: int, angle: float) -> tuple[float, float]:
    """The ``k = index + 1`` turn of the golden-angle spiral, in the x/y plane.

    ``θ_k = k · angle`` and ``s_k = √k`` (spec §12.2 step 3), scaled by
    ``spacing``: the plane whose coordinates the later stages do not overwrite,
    because ``z`` belongs to the depth rule alone.
    """
    turn = index + 1
    radius = spacing * math.sqrt(turn)
    theta = turn * angle
    return (radius * math.cos(theta), radius * math.sin(theta))


def _mean_root_spacing(root_keys: Sequence[str], seed: Mapping[str, tuple[float, float]]) -> float:
    """Mean distance between consecutive roots in ``public_key`` order (spec §12.4)."""
    if len(root_keys) < 2:
        return 0.0
    gaps = (math.dist(seed[first], seed[second]) for first, second in itertools.pairwise(root_keys))
    return sum(gaps) / (len(root_keys) - 1)


# ---------------------------------------------------------------------------
# The nine stages
# ---------------------------------------------------------------------------


def _stage_hierarchy_depth(run: _Run) -> None:
    """Step 1 — the longest path from any root; the spiral's root order is set up here."""
    run.depth = _depth_by_arcs(run.keys, run.arcs)
    run.max_depth = max(run.depth.values(), default=0)


def _stage_importance_radius(run: _Run) -> None:
    """Step 2 — the drawn radius, with the identity anchor wider than any domain."""
    low = LAYOUT_CONSTANTS["r_min"]
    high = LAYOUT_CONSTANTS["r_max"]
    radii = {key: low + (high - low) * (run.importance[key] / 100) for key in run.keys}
    if run.anchor_key is not None:
        widest = max(
            (radius for key, radius in radii.items() if key != run.anchor_key),
            default=radii[run.anchor_key],
        )
        radii[run.anchor_key] = LAYOUT_CONSTANTS["anchor_ratio"] * widest
    run.radius = radii


def _stage_seed_placement(run: _Run) -> None:
    """Step 3 — roots on the spiral, every other node around its seed parent.

    Parents are processed in ``(depth, public_key)`` order so a parent's seed
    always exists first; a node with several hierarchy parents follows the first
    of them in ``public_key`` order, which is the one the sorted arc list names
    first.

    A node no *seeded* parent reaches — a member of a cycle, which validation
    rejects long before this point (``HIERARCHY_CYCLE``) and which a direct call
    may still hand the engine — is deferred and then seeded on the root spiral,
    after the roots, in ``public_key`` order: the fallback is a documented order,
    not an error and not an accident of iteration.
    """
    spacing = LAYOUT_CONSTANTS["seed_spacing"]
    angle = LAYOUT_CONSTANTS["golden_angle"]
    child_scale = LAYOUT_CONSTANTS["child_seed_scale"]

    seed: dict[str, tuple[float, float]] = {
        key: _spiral(spacing, index, angle) for index, key in enumerate(run.root_keys)
    }
    deferred: list[str] = []
    for key in sorted(run.parents, key=lambda child: (run.depth[child], child)):
        parent = run.parents[key][0]
        if parent not in seed:
            deferred.append(key)
            continue
        siblings = run.children.get(parent, ())
        offset = _spiral(spacing * child_scale, siblings.index(key), angle)
        base = seed[parent]
        seed[key] = (base[0] + offset[0], base[1] + offset[1])
    for index, key in enumerate(deferred, start=len(run.root_keys)):
        seed[key] = _spiral(spacing, index, angle)

    run.seed = seed
    run.position = {key: [seed[key][0], seed[key][1], 0.0] for key in run.keys}
    run.mean_root_spacing = _mean_root_spacing(run.root_keys, seed)


def _stage_group_attraction(run: _Run) -> None:
    """Step 4 — drift toward the mean of the node's group centroids (spec §12.4).

    Centroids are computed from the **seed** positions of each group's members, so
    the force is a pure function of the input rather than of the relaxation's
    progress; a node in two groups goes to the midpoint of the two centroids, and
    the whole displacement is capped at half the mean inter-root spacing so a
    cluster can never move a node across the hierarchy.
    """
    if not run.members:
        return
    centres: dict[str, tuple[float, float]] = {}
    for group_key in sorted(run.members):
        member_keys = run.members[group_key]
        centres[group_key] = (
            sum(run.seed[key][0] for key in member_keys) / len(member_keys),
            sum(run.seed[key][1] for key in member_keys) / len(member_keys),
        )

    fraction = LAYOUT_CONSTANTS["group_attraction"]
    cap = 0.5 * run.mean_root_spacing
    for key in run.keys:
        group_keys = run.node_groups.get(key, ())
        if not group_keys:
            continue
        target_x = sum(centres[group][0] for group in group_keys) / len(group_keys)
        target_y = sum(centres[group][1] for group in group_keys) / len(group_keys)
        shift_x = fraction * (target_x - run.position[key][0])
        shift_y = fraction * (target_y - run.position[key][1])
        distance = math.hypot(shift_x, shift_y)
        if cap and distance > cap:
            shift_x, shift_y = shift_x * cap / distance, shift_y * cap / distance
        run.position[key][0] += shift_x
        run.position[key][1] += shift_y


def _stage_collision_relaxation(run: _Run) -> None:
    """Step 5 — the frozen 120-iteration relaxation over every node."""
    _relax(run, movers=run.keys)


def _stage_depth_layering(run: _Run) -> None:
    """Step 6 — ``z`` from the depth rule, and from nothing else."""
    spread = LAYOUT_CONSTANTS["depth_spread"]
    denominator = max(1, run.max_depth)
    for key in run.keys:
        run.position[key][2] = spread * run.depth[key] / denominator


def _stage_pins(run: _Run) -> None:
    """Step 7 — a pinned coordinate is copied in, after the relaxation."""
    for key in run.keys:
        pin_x, pin_y, pin_z = run.pins[key]
        if pin_x is None or pin_y is None:
            continue
        run.position[key][0] = pin_x
        run.position[key][1] = pin_y
        if pin_z is not None:
            run.position[key][2] = pin_z
        run.pinned.add(key)


def _stage_final_collision(run: _Run) -> None:
    """Step 8 — the same relaxation with the pinned nodes held still, then the expulsion."""
    _relax(run, movers=tuple(key for key in run.keys if key not in run.pinned))
    _expel_from_pins(run)


def _stage_rounding(run: _Run) -> None:
    """Step 9 — round once, at the end (spec §12.3)."""
    decimals = LAYOUT_CONSTANTS["coordinate_decimals"]
    for key in run.keys:
        run.position[key] = [round(value, decimals) for value in run.position[key]]


#: The pipeline the engine executes, in spec §12.2's order. A test freezes the
#: order, and reordering this tuple reorders the computation — the two can never
#: drift apart.
LAYOUT_BLUEPRINT: tuple[LayoutStage, ...] = (
    LayoutStage(
        "hierarchy-depth",
        "longest hierarchy path from any root; unreachable nodes sit at depth 0",
        _stage_hierarchy_depth,
    ),
    LayoutStage(
        "importance-radius",
        "drawn radius from importance; the anchor wider than any domain",
        _stage_importance_radius,
    ),
    LayoutStage("seed-placement", "golden-angle spiral, parents first", _stage_seed_placement),
    LayoutStage(
        "group-attraction",
        "toward the mean of the node's group centroids, displacement capped",
        _stage_group_attraction,
    ),
    LayoutStage(
        "collision-relaxation",
        "fixed 120 iterations over every node, damped",
        _stage_collision_relaxation,
    ),
    LayoutStage("depth-layering", "z spread by depth", _stage_depth_layering),
    LayoutStage("pins", "pinned coordinates copied in, never derived", _stage_pins),
    LayoutStage("final-collision", "relaxation with pins held still", _stage_final_collision),
    LayoutStage("rounding", "3-decimal quantisation, once", _stage_rounding),
)


def _relax(run: _Run, *, movers: Sequence[str]) -> None:
    """One relaxation routine, shared by steps 5 and 8.

    Each iteration accumulates one push per overlapping pair — half the overlap
    each, damped by ``relaxation_damping`` — and then moves every mover by at most
    ``max_step_per_iteration`` scene units, so no single iteration can displace a
    node further than the constants allow. Only ``movers`` are displaced; a pinned
    node is an obstacle its neighbours move away from. Pairs further apart than
    their combined radius plus ``gap`` are rejected on the squared distance alone,
    which is what keeps the pair loop inside spec §19.2's budget. Coincident nodes
    are separated along a fixed axis: determinism forbids a random jitter, and a
    fixed axis gives the same result on every run.
    """
    damping = LAYOUT_CONSTANTS["relaxation_damping"]
    step_cap = LAYOUT_CONSTANTS["max_step_per_iteration"]
    gap = LAYOUT_CONSTANTS["gap"]
    iterations = LAYOUT_CONSTANTS["relaxation_iterations"]
    mover_set = set(movers)
    keys = run.keys
    for _iteration in range(iterations):
        deltas = {key: [0.0, 0.0] for key in movers}
        for index, first in enumerate(keys):
            first_position = run.position[first]
            first_radius = run.radius[first]
            for second in keys[index + 1 :]:
                second_position = run.position[second]
                wanted = first_radius + run.radius[second] + gap
                dx = first_position[0] - second_position[0]
                dy = first_position[1] - second_position[1]
                squared = dx * dx + dy * dy
                if squared >= wanted * wanted:
                    continue
                if squared == 0.0:
                    dx, dy, distance = 1.0, 0.0, 1.0
                else:
                    distance = math.sqrt(squared)
                push = damping * (wanted - distance) / 2
                unit_x, unit_y = dx / distance, dy / distance
                if first in mover_set:
                    deltas[first][0] += push * unit_x
                    deltas[first][1] += push * unit_y
                if second in mover_set:
                    deltas[second][0] -= push * unit_x
                    deltas[second][1] -= push * unit_y
        for key in movers:
            delta = deltas[key]
            magnitude = math.hypot(delta[0], delta[1])
            if magnitude > step_cap:
                scale = step_cap / magnitude
                delta[0], delta[1] = delta[0] * scale, delta[1] * scale
            run.position[key][0] += delta[0]
            run.position[key][1] += delta[1]


def _expel_from_pins(run: _Run) -> None:
    """Place every unpinned mover still inside a pin on the pin's clearance circle.

    The relaxation above is a *force* pass: each iteration moves a node by a damped
    share of every push it receives, and a mover whose still-overlapping neighbours
    push against the pin can be held inside it for the whole iteration budget — for
    such a node spec §12.2 step 8's guarantee is false however many iterations run
    (the counterexample in ``test_layout.py`` measures 5.632 scene units of it). This
    pass is *geometric*: a mover inside a pin is set exactly on the clearance circle,
    along the ray that joins the two, so the pair ends with no overlap at all. Only
    unpinned movers move — a pinned coordinate is an input (step 7).

    Determinism (spec §12.3): pins and movers are walked in ``public_key`` order —
    the tie-break when a mover lies inside several pins — and a mover sitting
    exactly *on* a pin, which has no ray of its own, takes the golden-angle turn of
    its key's position in the ordered walk, so the direction is a function of
    ``public_key`` too. The pass repeats until a round displaces nothing, bounded by
    one round per pin plus one. The clearance carries one quantisation unit
    (``10 ** -coordinate_decimals``) because step 9 rounds afterwards, and rounding
    can steal up to ``√2 · 5·10⁻⁴`` scene units of any distance: the guarantee has
    to survive the rounding that ends the pipeline.
    """
    pinned = tuple(key for key in run.keys if key in run.pinned)
    if not pinned:
        return
    guard = 10.0 ** -LAYOUT_CONSTANTS["coordinate_decimals"]
    angle = LAYOUT_CONSTANTS["golden_angle"]
    for _round in range(len(pinned) + 1):
        displaced = False
        for index, key in enumerate(run.keys):
            if key in run.pinned:
                continue
            position = run.position[key]
            for pin in pinned:
                pin_position = run.position[pin]
                wanted = run.radius[key] + run.radius[pin] + guard
                dx = position[0] - pin_position[0]
                dy = position[1] - pin_position[1]
                distance = math.hypot(dx, dy)
                if distance >= wanted:
                    continue
                if distance == 0.0:
                    turn = (index + 1) * angle
                    dx, dy, distance = math.cos(turn), math.sin(turn), 1.0
                position[0] = pin_position[0] + dx * wanted / distance
                position[1] = pin_position[1] + dy * wanted / distance
                displaced = True
        if not displaced:
            break


# ---------------------------------------------------------------------------
# The public surface (the plan's Task 11 interfaces)
# ---------------------------------------------------------------------------


def hierarchy_depths(
    nodes: Iterable[AtlasNode], relations: Iterable[AtlasRelation]
) -> dict[str, int]:
    """``{public key: depth}`` for the visible hierarchy subgraph (spec §12.2 step 1).

    The longest path from any root, over visible relations whose type has
    ``hierarchy_role`` and whose endpoints are both visible nodes; a node no root
    reaches is ``0``.
    """
    by_pk = _visible_by_pk(nodes)
    return _depth_by_arcs(tuple(sorted(by_pk.values())), _hierarchy_arcs(by_pk, relations))


def _anchor_key(visible_nodes: Sequence[AtlasNode]) -> str | None:
    """The identity anchor's key, when the version has an anchor node (spec §6.1)."""
    return next(
        (node.public_key for node in visible_nodes if node.node_type.key == ANCHOR_NODE_TYPE_KEY),
        None,
    )


def _prepare(nodes: Iterable[AtlasNode], relations: Iterable[AtlasRelation], groups) -> _Run:
    """Build a run's immutable input state: rows sorted, relations materialised, pins read.

    ``relations`` is annotated ``Iterable``, so it is materialised **once, first**:
    a generator or a ``filter`` is a legal input, and building the arcs and reading
    the rows later must both see the same sequence (an iterator drained by the first
    reader used to leave stage 1 with nothing and the seed stage with a bare
    ``KeyError``). The arcs are then built once and shared, which is what removes
    the duplicate arc build stage 1 used to perform.
    """
    relations = tuple(relations)
    visible_nodes = tuple(sorted((node for node in nodes if node.visible), key=_public_key))
    keys = tuple(node.public_key for node in visible_nodes)
    by_pk = {node.pk: node.public_key for node in visible_nodes}
    arcs = _hierarchy_arcs(by_pk, relations)

    parents: dict[str, list[str]] = {}
    children: dict[str, list[str]] = {}
    for parent, child in arcs:
        parents.setdefault(child, []).append(parent)
        children.setdefault(parent, []).append(child)

    members = _normalise_groups(groups, set(keys))
    node_groups: dict[str, list[str]] = {}
    for group_key in sorted(members):
        for member in members[group_key]:
            node_groups.setdefault(member, []).append(group_key)

    return _Run(
        arcs=arcs,
        members=members,
        keys=keys,
        by_pk=by_pk,
        pins={node.public_key: (node.pin_x, node.pin_y, node.pin_z) for node in visible_nodes},
        importance={node.public_key: node.importance for node in visible_nodes},
        parents={key: tuple(value) for key, value in parents.items()},
        children={key: tuple(value) for key, value in children.items()},
        anchor_key=_anchor_key(visible_nodes),
        node_groups={key: tuple(value) for key, value in node_groups.items()},
        root_keys=tuple(key for key in keys if key not in parents),
    )


def compute_layout(
    nodes: Iterable[AtlasNode],
    relations: Iterable[AtlasRelation],
    groups,
) -> dict[str, tuple[float, float, float]]:
    """Compute one version's coordinates: ``{public key: (x, y, z)}`` (spec §12.2).

    Pure: it reads the rows and the group membership mapping it is handed, walks
    every collection in ``public_key`` order and returns 3-decimal tuples. The
    annotation is honest, too — ``nodes`` and ``relations`` may be any iterable
    (list, generator, ``filter``), because both are materialised before the first
    read, and the same input produces byte-identical output on every run and every
    machine. Hidden nodes get no coordinate at all (they are not part of the
    projection), and a cyclic hierarchy is tolerated deterministically rather than
    raising (see the module docstring's fallback note).
    """
    run = _prepare(nodes, relations, groups)
    for stage in LAYOUT_BLUEPRINT:
        stage.run(run)
    return {key: tuple(run.position[key]) for key in run.keys}


def apply_layout(version: AtlasVersion) -> dict[str, list[float]]:
    """Compute, store and re-revision one version's layout (spec §12.2 step 9).

    The plan's split is unchanged: this is the engine's only ORM adapter — one
    query set per collection and one save — and the stored value is the storage
    shape the plan fixes, ``{"<node public key>": [x, y, z]}`` with 3-decimal
    coordinates. ``layout_revision`` advances on every call, because every call is
    a recomputation (the plan's ``recompute_layout`` contract for Plan B's button
    and Plan D's seed).
    """
    nodes = list(version.nodes.select_related("node_type"))
    # The engine resolves a relation's endpoints by id (``source_id``/``target_id``),
    # so the endpoint rows are never loaded — only the type carries a field the
    # stages read (``hierarchy_role``).
    relations = list(version.relations.select_related("relation_type"))
    computed = compute_layout(nodes, relations, _stored_groups(version))

    layout = {key: list(coordinate) for key, coordinate in computed.items()}
    version.layout = layout
    version.layout_revision += 1
    version.save(update_fields=["layout", "layout_revision", "updated_at"])
    return layout


def _stored_groups(version: AtlasVersion) -> dict[str, tuple[str, ...]]:
    """``{group key: member node keys}`` for one version, in a single query.

    Membership rows are read once and sorted in Python: the engine's contract is
    ``public_key`` order, and a database collation is not part of it (spec §12.3
    forbids locale-dependent collation).
    """
    members: dict[str, list[str]] = {}
    rows = (
        AtlasGroupMembership.objects.filter(group__version=version)
        .select_related("group", "node")
        .order_by()
    )
    for row in rows:
        members.setdefault(row.group.public_key, []).append(row.node.public_key)
    return {key: tuple(sorted(value)) for key, value in members.items()}
