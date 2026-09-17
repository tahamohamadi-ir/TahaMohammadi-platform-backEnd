"""Publish-gate validation (spec §20) — relation rules and the node/locale pass.

The module is **pure**: no HTTP layer, and no queries inside the rule loops.
:func:`validate_relations` loads a version's relations once (``select_related``)
plus the two allowed-pair tables once, then hands both to
:func:`relation_rule_issues` — the rule layer a payload-level validation (Plan B's
bulk graph PUT) can call with rows that are not written yet. Validation never
reshapes a graph: it returns issues (spec §20.3 — the backend is the only
authority on validity, and a validator never returns a repaired graph).

Every issue carries the composed relation key it is about (spec §5.3: composed,
never a stored column) and the ``message_token`` the admin renders. Codes are
stable strings that are never renamed (spec §20); :data:`BLOCKING_CODES` is the
vocabulary that stops an activation (spec §8.3).

Task 9 implements the relation rules of §20.1. Task 10 adds the rest of the
publish gate, one function per question it answers:

* :func:`validate_hierarchy` — is the hierarchy-role subgraph a DAG? (spec §5.6)
  with :func:`validate_visibility` — does a visible relation reference an
  invisible node? The two groups the plan's Interfaces line does not name
  together are split on purpose: ``DANGLING_NODE_HIDDEN_RELATION`` is not a
  hierarchy, locale, taxonomy or canonical rule, and a separate name keeps each
  group honest (``validate_version``, Task 12, aggregates every group this module
  exposes — all six, not the plan's four);
* :func:`validate_locale_projection` — does every visible node resolve in *both*
  locales, and does every group carry copy in both? (spec §5.4/§5.5; the parity
  note of §20.1);
* :func:`validate_taxonomy` — does a node use a retired node type? (the relation
  half is Task 9's ``RELATION_TYPE_INACTIVE``);
* :func:`validate_canonical_refs` — is the canonical reference present, published
  and unambiguous? (spec §5.4).

Task 12 adds the report and the two gates of the serving path:

* :func:`validate_version` — the publish gate (spec §20, §8.3): it aggregates
  **every** blocking group this module exposes (the six validator functions
  above plus :func:`validate_layout` for ``MISSING_LAYOUT`` and
  :func:`validate_pins` for ``INVALID_PIN``) and the §20.2 warnings of
  :func:`warning_issues`, de-duplicates them and returns one
  :class:`ValidationReport`;
* :func:`validate_payload_contract` — the serving gate (spec §20.1's
  ``PAYLOAD_CONTRACT_INVALID``; §11.4's payload validation): a **pure** check of
  a projected payload (spec §10.2) against its own contract. It never touches
  the ORM, so Task 15 can call it on Task 14's freshly built projection and
  answer a non-empty issue list with its fail-closed response. **Interface for
  Tasks 14/15:** ``validate_payload_contract(projection: dict) -> list[Issue]``
  takes the §10.2 payload as plain data, returns ``[]`` when it is valid and a
  sorted list of ``PAYLOAD_CONTRACT_INVALID`` / ``MISSING_LAYOUT`` /
  ``DUPLICATE_PUBLIC_KEY`` issues otherwise, and never raises on malformed
  input. ``MISSING_LAYOUT`` is the payload's own rule too: a visible node whose
  stored layout has no coordinate reaches the payload without ``position``, and
  this check reports it as the activation gate does (the ledger's 12-note
  de-duplication is about ``Issue`` rows, not about this gate).

Scope decisions of Tasks 9–12, recorded because the plan leaves them open:

* **every relation of the version is judged**, ``visible`` or not: the rule texts
  of spec §20.1 are not visibility-conditional, and a hidden row that is invalid
  would pass the gate silently and reopen on the next unhide. Visibility-scoped
  rules are the ones whose text says so: ``DANGLING_NODE_HIDDEN_RELATION`` (a
  *visible* relation, spec §20.1) and the locale gate (``visible`` nodes, §20.1's
  note: "an invisible node is neither published nor traversed — and is therefore
  not parity-gated") — and the node half of the canonical rules follows the
  projection, so hiding a node drops its reference issues too;
* ``directed`` is **judged, never coerced**: spec §5.3's "initialised from
  ``directed_default``" rule is the authoring layer's job, and a relation that
  contradicts its type while ``overridable_direction`` is false is exactly
  ``DIRECTION_NOT_OVERRIDABLE`` (the plan adds that code to the blocking set);
* duplicates are reported **once per duplicated composed key** — the key is the
  wire identity, and the two halves of an undirected mirrored pair compose the
  same key, so "this key is claimed twice" is the honest message and the one the
  admin can act on. An undirected row answers to **both** endpoint orders, so a
  directed row that reverses it is its duplicate whichever way round the undirected
  row was authored — the verdict is order-independent, as the sorted key of §5.3
  requires — and each involved key spelling is reported once;
* the **hierarchy subgraph is restricted on both ends** — a relation counts only
  when its type has ``hierarchy_role`` *and* both endpoints are visible nodes of
  this version (spec §5.6 "restricted to ``visible`` nodes"), and every walk of the
  cycle rule reads ``public_key`` order alone, so a verdict and the node it names
  are properties of the graph rather than of relation row order (the Task 10 review
  found a row-order-dependent false negative there — ruling R11);
* ``CANONICAL_SOURCE_UNPUBLISHED`` and ``MISSING_LOCALE_PROJECTION`` are **never
  each other's alias**: the first is about the referenced *record* (a row exists
  and does not pass ``objects.public()``), the second about the *locale's*
  projection (nothing resolves there). Both are blocking; the admin renders the
  difference between "fix the record" and "you have no copy for this locale";
* the **taxonomy lifecycle is judged for every node**, visible or not (the Task 9
  relation decision above), while the two other lifecycle rules stay model-level
  and are deliberately not duplicated: ``PROTECT`` blocks deleting an in-use type
  and ``TaxonomyKeyMixin`` blocks renaming its key;
* a **group is judged for copy in both locales** whatever its ``active`` flag —
  §20.1's ``GROUP_LOCALE_MISSING`` row has no qualifier, and the same reasoning as
  the relation rules applies (a retired group can be re-activated);
* the **report is per (code, entity)**: a fact whose only difference is an
  attribute the issue shape does not carry (a locale, a type key) is reported
  once. ``MISSING_LOCALE_PROJECTION`` is still emitted once per locale by
  ``validate_locale_projection`` — an ``Issue`` has no locale, so the raw
  validator's two rows for one node are honest facts — and
  ``validate_version`` collapses them per node (ledger row ``12-note`` a);
  ``UNUSED_NODE_TYPE`` / ``UNUSED_RELATION_TYPE`` fire once per version for the
  same reason: a per-type row would be a duplicate no reader could tell apart;
* ``DUPLICATE_RELATION`` is collapsed **per conflicting pair** by the report:
  when an undirected row and a reversed *directed* row carry different key
  spellings, the rule layer reports one issue per shared key — one conflict, two
  spellings — and ``validate_version`` keeps the first spelling in sorted order
  (ledger row ``9-note``);
* the **§20.2 graph-shape warnings judge the visible graph** — the payload is
  visible-only, §20.2's own texts say "visible" where they mean it, and
  ``ISOLATED_NODE``/``HIGH_DEGREE_HUB``/``SCALE_*`` are §12.7's scene warnings —
  while the two taxonomy warnings read "no node/relation in this version"
  literally and count every row, and ``SUMMARY_MISSING`` needs no resolution for
  a node that any locale's ``summary_override`` already covers;
* ``INVALID_PIN`` judges **every node** of the version, visible or not, like the
  taxonomy gate: a pin that is invalid now is invalid after an unhide, and a
  hidden row's defect must not reopen silently. ``PIN_BOUND`` is this module's
  scene bound — spec §12.5 says "within the scene bounds" without a number, so
  the bound is a named constant here (a spec gap, reported for the owner);
* ``OVERLAPPING_PINS`` names each pinned **visible** node that sits closer than
  the sum of the §12.2 step 2 radii to another pinned visible node outside its
  group — the engine's own separation rule, read in the x/y plane (§12.2's
  relaxation moves x/y only). The radii come from ``apps.atlas.layout``'s
  constants so the warning and the scene can never disagree on the numbers;
* **canonical facts are computed once per node** and shared by both canonical
  gates (and by the summary warning) when they run inside
  :func:`validate_version`: :func:`canonical_facts` resolves each visible node's
  ``(source, key)`` per locale exactly once, where the standalone validators
  each resolved it for themselves (the Task 10 review measured 133/333/413
  queries at 30/80/100 nodes for the naive aggregation). Called standalone, each
  validator still computes exactly the facts it needs — no verdict changes
  either way, which the existing tests and the shared-facts tests both pin.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from uuid import UUID

from apps.atlas.canonical import (
    CANONICAL_SOURCES,
    DEFAULT_LOCALES,
    AmbiguousCanonicalRef,
    CanonicalResolution,
    resolve_canonical,
)
from apps.atlas.keys import is_valid_public_key
from apps.atlas.models import (
    SELF_LOOP_POLICIES,
    AtlasGroupMembership,
    AtlasNode,
    AtlasNodeType,
    AtlasRelation,
    AtlasRelationType,
    AtlasVersion,
    is_valid_relation_public_key,
)

# ``apps.atlas.models`` also defines a name ``CANONICAL_SOURCES`` — a
# ``TextChoices`` vocabulary for the ``AtlasNodeType.canonical_source`` /
# ``AtlasNode.canonical_model`` *columns*. The name imported above is the
# resolver **registry** (``apps.atlas.canonical``): its keys are that same
# canonical-source vocabulary and its values are the model classes. This module
# never needs the column vocabulary — membership in the registry is the question
# it asks, and ``none`` (an Atlas-only structural node) is not in it.

#: Codes whose violation blocks publication — **the frozen vocabulary**: every
#: row of spec §20.1, in the spec table's order, plus the plan's
#: ``DIRECTION_NOT_OVERRIDABLE``, which has **no §20.1 row at all** (ledger row
#: ``9-cf``: it occurs zero times in the spec, and it is a real, tested blocker —
#: a ``directed`` value contradicting ``directed_default`` while
#: ``overridable_direction`` is false violates spec §5.3). The vocabulary is
#: frozen against the *live emission sites* by
#: ``tests/test_validation_report.py``: every code here is emitted somewhere in
#: this module, and nothing this module emits is outside the tuple.
BLOCKING_CODES: tuple[str, ...] = (
    "DANGLING_NODE_HIDDEN_RELATION",
    "DANGLING_RELATION_ENDPOINT",
    "CANONICAL_SOURCE_MISSING",
    "CANONICAL_SOURCE_UNPUBLISHED",
    "MISSING_LOCALE_PROJECTION",
    "AMBIGUOUS_CANONICAL_REF",
    "NODE_TYPE_INACTIVE",
    "RELATION_TYPE_INACTIVE",
    "RELATION_TYPE_NOT_ALLOWED",
    "HIERARCHY_CYCLE",
    "SELF_LOOP_FORBIDDEN",
    "DUPLICATE_PUBLIC_KEY",
    "DUPLICATE_RELATION",
    "INVALID_PIN",
    "MISSING_LAYOUT",
    "GROUP_LOCALE_MISSING",
    "PAYLOAD_CONTRACT_INVALID",
    "DIRECTION_NOT_OVERRIDABLE",
)

#: Every row of spec §20.2, in the spec table's order. Warnings are never
#: blocking, and §20.1's note keeps ``MISSING_LOCALE_PROJECTION`` a blocker in
#: both directions — it is never emitted as a warning.
WARNING_CODES: tuple[str, ...] = (
    "ISOLATED_NODE",
    "NO_INBOUND_RELATIONS",
    "NO_OUTBOUND_RELATIONS",
    "HIGH_DEGREE_HUB",
    "SUMMARY_MISSING",
    "UNUSED_NODE_TYPE",
    "UNUSED_RELATION_TYPE",
    "OVERLAPPING_PINS",
    "SCALE_NODES",
    "SCALE_RELATIONS",
    "SINGLE_LEVEL_HIERARCHY",
)

#: The whole implemented vocabulary — every code this module may emit.
ATLAS_ISSUE_CODES: tuple[str, ...] = BLOCKING_CODES + WARNING_CODES

#: The §20.2/§12.7 thresholds, in one home: more than ``nodes`` visible nodes,
#: more than ``relations`` visible relations, more than ``hubs`` relations on
#: one node. The plan's Task 12 constants, verbatim.
SCALE_WARN_THRESHOLDS: dict[str, int] = {"nodes": 100, "relations": 250, "hubs": 12}

#: The scene bound a pin coordinate must sit inside (spec §12.5: "within the
#: scene bounds"). The spec fixes no number, so this is the module's named
#: constant: the layout engine's largest legal coordinate at the v1 ceiling
#: (100 nodes → a seed radius around 140, depth spread 9) is an order of
#: magnitude inside it, and an unbounded coordinate would make the scene framing
#: meaningless. Reported as a spec gap for the owner.
PIN_BOUND: float = 1000.0

#: ``messageToken`` per code: one token per code, ``atlas.*`` namespace, mirroring
#: the AB-06 validator (``graph.*``). The admin renders the token, never the code.
_MESSAGE_TOKENS: dict[str, str] = {
    "AMBIGUOUS_CANONICAL_REF": "atlas.ambiguousCanonicalRef",
    "CANONICAL_SOURCE_MISSING": "atlas.canonicalSourceMissing",
    "CANONICAL_SOURCE_UNPUBLISHED": "atlas.canonicalSourceUnpublished",
    "DANGLING_NODE_HIDDEN_RELATION": "atlas.danglingNodeHiddenRelation",
    "DANGLING_RELATION_ENDPOINT": "atlas.danglingRelationEndpoint",
    "DIRECTION_NOT_OVERRIDABLE": "atlas.directionNotOverridable",
    "DUPLICATE_PUBLIC_KEY": "atlas.duplicatePublicKey",
    "DUPLICATE_RELATION": "atlas.duplicateRelation",
    "GROUP_LOCALE_MISSING": "atlas.groupLocaleMissing",
    "HIERARCHY_CYCLE": "atlas.hierarchyCycle",
    "HIGH_DEGREE_HUB": "atlas.highDegreeHub",
    "INVALID_PIN": "atlas.invalidPin",
    "ISOLATED_NODE": "atlas.isolatedNode",
    "MISSING_LAYOUT": "atlas.missingLayout",
    "MISSING_LOCALE_PROJECTION": "atlas.missingLocaleProjection",
    "NODE_TYPE_INACTIVE": "atlas.nodeTypeInactive",
    "NO_INBOUND_RELATIONS": "atlas.noInboundRelations",
    "NO_OUTBOUND_RELATIONS": "atlas.noOutboundRelations",
    "OVERLAPPING_PINS": "atlas.overlappingPins",
    "PAYLOAD_CONTRACT_INVALID": "atlas.payloadContractInvalid",
    "RELATION_TYPE_INACTIVE": "atlas.relationTypeInactive",
    "RELATION_TYPE_NOT_ALLOWED": "atlas.relationTypeNotAllowed",
    "SCALE_NODES": "atlas.scaleNodes",
    "SCALE_RELATIONS": "atlas.scaleRelations",
    "SELF_LOOP_FORBIDDEN": "atlas.selfLoopForbidden",
    "SINGLE_LEVEL_HIERARCHY": "atlas.singleLevelHierarchy",
    "SUMMARY_MISSING": "atlas.summaryMissing",
    "UNUSED_NODE_TYPE": "atlas.unusedNodeType",
    "UNUSED_RELATION_TYPE": "atlas.unusedRelationType",
}


@dataclass(frozen=True)
class Issue:
    """One validation issue (spec §20) — the admin's ``{code, …, messageToken}``.

    At most one entity key is set: a relation issue names ``relation_key``, a node
    issue ``node_key``, a group issue ``group_key``. ``message_token`` is filled by
    this module for every issue it emits; Task 12's ``ValidationReport.to_dict``
    serializes the camelCase wire spelling of the same five fields.
    """

    code: str
    node_key: str | None = None
    relation_key: str | None = None
    group_key: str | None = None
    message_token: str = ""


@dataclass
class ValidationReport:
    """``{"blocking": [...], "warnings": [...]}`` — the publish gate's answer (spec §20).

    :func:`validate_version` (Task 12) fills it: both lists are sorted by
    ``(code, entity key)``, every ``(code, entity)`` fact appears once, and a
    conflicting relation pair appears once however its two rows spell the key.
    ``to_dict`` is the wire shape the admin reads (spec §20's
    ``{code, …, messageToken}`` with ````nodeKey``/``relationKey``/``groupKey``
    in camelCase and unset keys omitted).
    """

    blocking: list[Issue] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)

    def blocking_codes(self) -> list[str]:
        """The distinct blocking codes, sorted — what an activation reports."""
        return sorted({issue.code for issue in self.blocking})

    def warning_codes(self) -> list[str]:
        """The distinct warning codes, sorted."""
        return sorted({issue.code for issue in self.warnings})

    def to_dict(self) -> dict[str, list[dict[str, str]]]:
        """The JSON-ready report — the admin's wire shape (spec §20)."""
        return {
            "blocking": [_issue_dict(issue) for issue in self.blocking],
            "warnings": [_issue_dict(issue) for issue in self.warnings],
        }


def _issue_dict(issue: Issue) -> dict[str, str]:
    """One issue in the wire spelling: camelCase keys, unset keys omitted."""
    payload = {"code": issue.code}
    if issue.node_key is not None:
        payload["nodeKey"] = issue.node_key
    if issue.relation_key is not None:
        payload["relationKey"] = issue.relation_key
    if issue.group_key is not None:
        payload["groupKey"] = issue.group_key
    payload["messageToken"] = issue.message_token
    return payload


@dataclass(frozen=True)
class AllowedTypes:
    """A relation type's allowed endpoint node types, as node-type primary keys.

    An **empty** set means "any active type" (spec §6.2) — a non-empty one is
    enforced as ``RELATION_TYPE_NOT_ALLOWED``. Kept as primary keys so the rule
    layer compares identifiers it was handed instead of walking a manager.
    """

    sources: frozenset[int] = frozenset()
    targets: frozenset[int] = frozenset()

    def allows(self, *, source_type_id: int, target_type_id: int) -> bool:
        """Whether this endpoint pair is inside the allowed pair."""
        return (not self.sources or source_type_id in self.sources) and (
            not self.targets or target_type_id in self.targets
        )


def _issue(
    code: str,
    *,
    node_key: str | None = None,
    relation_key: str | None = None,
    group_key: str | None = None,
) -> Issue:
    """Build an issue with this module's ``messageToken`` for ``code``."""
    return Issue(
        code=code,
        node_key=node_key,
        relation_key=relation_key,
        group_key=group_key,
        message_token=_MESSAGE_TOKENS[code],
    )


def allowed_types_by_relation_type(relation_type_ids: Iterable[int]) -> dict[int, AllowedTypes]:
    """Load the allowed endpoint types of ``relation_type_ids`` in two queries.

    Every requested id gets an entry (empty = any), so the rule loop can index the
    mapping directly: an id the caller forgot is a ``KeyError`` naming the mistake
    instead of a silent "any" that would disable the pair rule for that type.
    """
    ids = list(relation_type_ids)
    sources: dict[int, set[int]] = {type_id: set() for type_id in ids}
    targets: dict[int, set[int]] = {type_id: set() for type_id in ids}
    if ids:
        for m2m, sink in (
            (AtlasRelationType.allowed_source_types, sources),
            (AtlasRelationType.allowed_target_types, targets),
        ):
            rows = m2m.through.objects.filter(atlasrelationtype_id__in=ids).values_list(
                "atlasrelationtype_id", "atlasnodetype_id"
            )
            for type_id, node_type_id in rows:
                sink[type_id].add(node_type_id)
    return {
        type_id: AllowedTypes(frozenset(sources[type_id]), frozenset(targets[type_id]))
        for type_id in ids
    }


def _composed_key(relation: AtlasRelation) -> str | None:
    """The relation's composed public key, or ``None`` when a segment is missing.

    A row without endpoints or a type has no key to name — the shape a payload
    validation can hand over. A stored row's three foreign keys are non-null, so
    this is the payload path's case only, and such a row is reported as dangling
    (nothing of it can be found in the version).
    """
    if (
        relation.source_id is None
        or relation.target_id is None
        or relation.relation_type_id is None
    ):
        return None
    return relation.public_key


def _identities(relation: AtlasRelation) -> set[tuple[str, str, str]]:
    """The ``(source key, type key, target key)`` triples this row answers to.

    The stored identity is the authored triple. An **undirected** relation also
    answers to both ordered orientations — ``(min, t, max)`` *and* ``(max, t, min)``
    — because either pair composes the same public key (``relation_public_key``
    sorts the ends of an undirected key): the mirror rule of ``AtlasRelation.clean``.

    Both orientations, not just the sorted one: ``(authored) ∪ {(min, t, max)}``
    collapses to a single triple whenever the row was authored ascending, so an
    undirected row would then not answer for the reversed pair of a directed row
    that happens to be stored that way round — an insertion-order-dependent verdict,
    which is exactly what the sorted key of spec §5.3 exists to rule out. Claiming
    ``(min, t, max)`` and ``(max, t, min)`` makes the row's identity set the same
    whatever order its endpoints were authored in (a self-loop, where the two
    coincide, still claims one).
    """
    source, target = relation.source.public_key, relation.target.public_key
    identities = {(source, relation.relation_type.key, target)}
    if not relation.directed:
        identities.add((min(source, target), relation.relation_type.key, max(source, target)))
        identities.add((max(source, target), relation.relation_type.key, min(source, target)))
    return identities


def _duplicate_issues(judged: list[tuple[str, set[tuple[str, str, str]]]]) -> list[Issue]:
    """One ``DUPLICATE_RELATION`` per composed key that more than one row claims."""
    owners: dict[tuple[str, str, str], list[str]] = {}
    for key, identities in judged:
        for identity in identities:
            owners.setdefault(identity, []).append(key)
    duplicated = {key for keys in owners.values() if len(keys) > 1 for key in keys}
    return [_issue("DUPLICATE_RELATION", relation_key=key) for key in sorted(duplicated)]


def _sort_key(issue: Issue) -> tuple[str, str, str, str]:
    """Deterministic issue order: code first, then whichever key names the entity."""
    return (issue.code, issue.relation_key or "", issue.node_key or "", issue.group_key or "")


def relation_rule_issues(
    relations: Sequence[AtlasRelation],
    *,
    version_id: int,
    allowed_types: Mapping[int, AllowedTypes],
) -> list[Issue]:
    """Every relation rule of spec §20.1 over rows of one version, in stable order.

    ``relations`` are ``AtlasRelation`` instances — stored rows, or detached ones a
    payload validation built; ``allowed_types`` must cover every relation type they
    use (see :func:`allowed_types_by_relation_type`). Nothing here queries:
    endpoints, types and allowed pairs were loaded by the caller, which is what
    keeps the rule loop free of per-relation database work.
    """
    issues: list[Issue] = []
    judged: list[tuple[str, set[tuple[str, str, str]]]] = []
    for relation in relations:
        key = _composed_key(relation)
        if key is None:
            issues.append(_issue("DANGLING_RELATION_ENDPOINT"))
            continue
        relation_type = relation.relation_type
        if not relation_type.active:
            issues.append(_issue("RELATION_TYPE_INACTIVE", relation_key=key))
        if not allowed_types[relation_type.pk].allows(
            source_type_id=relation.source.node_type_id,
            target_type_id=relation.target.node_type_id,
        ):
            issues.append(_issue("RELATION_TYPE_NOT_ALLOWED", relation_key=key))
        if (
            relation.source_id is not None
            and relation.source_id == relation.target_id
            and relation_type.self_loop_policy != SELF_LOOP_POLICIES.ALLOW
        ):
            issues.append(_issue("SELF_LOOP_FORBIDDEN", relation_key=key))
        if (
            relation.directed != relation_type.directed_default
            and not relation_type.overridable_direction
        ):
            issues.append(_issue("DIRECTION_NOT_OVERRIDABLE", relation_key=key))
        if relation.source.version_id != version_id or relation.target.version_id != version_id:
            issues.append(_issue("DANGLING_RELATION_ENDPOINT", relation_key=key))
        judged.append((key, _identities(relation)))
    issues.extend(_duplicate_issues(judged))
    return sorted(issues, key=_sort_key)


def validate_relations(version: AtlasVersion) -> list[Issue]:
    """Every relation issue of ``version`` (the spec §20.1 relation rules, Task 9).

    Three queries in total, independent of how many relations the version holds:
    the rows themselves (``select_related``, so the rules never touch a related
    object they were not handed) and the two allowed-pair tables of the types in
    use.
    """
    relations = list(version.relations.select_related("source", "target", "relation_type"))
    allowed_types = allowed_types_by_relation_type(
        {relation.relation_type_id for relation in relations}
    )
    return relation_rule_issues(relations, version_id=version.pk, allowed_types=allowed_types)


# ---------------------------------------------------------------------------
# Task 10 — hierarchy, visibility, locale parity, taxonomy and canonical refs.
# ---------------------------------------------------------------------------


def validate_hierarchy(version: AtlasVersion) -> list[Issue]:
    """``HIERARCHY_CYCLE`` — the hierarchy-role subgraph must be a DAG (spec §5.6).

    Two queries: the version's visible nodes and its visible relations.
    :func:`hierarchy_rule_issues` owns the cycle rule; multiple parents, orphan
    nodes, several relation types between one pair and cycles *outside* the
    hierarchy subgraph are all permitted and never reported (spec §5.6).
    """
    nodes = list(version.nodes.filter(visible=True))
    relations = list(
        version.relations.filter(visible=True).select_related(
            "source", "target", "relation_type"
        )
    )
    return hierarchy_rule_issues(nodes, relations)


def _witness_walk(
    arcs: Mapping[int, list[tuple[int, int]]],
    *,
    origin: int,
    goal: int,
    relation_pk: int,
) -> list[int] | None:
    """A shortest ``origin → goal`` walk that avoids one relation's arcs, or ``None``.

    Breadth-first over the node-level arcs, so the walk handed back is a property of
    the graph: neighbours are followed in ``public_key`` order (see
    :func:`hierarchy_rule_issues`), and two arcs to one neighbour are interchangeable
    for a walk whose nodes are all that is read. ``origin == goal`` answers with the
    trivial walk — that is a relation whose two ends are one node, and the relation
    alone is the whole cycle; any longer walk would have to walk it twice.
    """
    if origin == goal:
        return [origin]
    previous: dict[int, int] = {origin: origin}
    queue: deque[int] = deque([origin])
    while queue:
        node_pk = queue.popleft()
        for neighbour_pk, walk_relation_pk in arcs[node_pk]:
            if walk_relation_pk == relation_pk or neighbour_pk in previous:
                continue
            previous[neighbour_pk] = node_pk
            if neighbour_pk == goal:
                walk = [goal]
                while walk[-1] != origin:
                    walk.append(previous[walk[-1]])
                walk.reverse()
                return walk
            queue.append(neighbour_pk)
    return None


def hierarchy_rule_issues(
    nodes: Sequence[AtlasNode], relations: Sequence[AtlasRelation]
) -> list[Issue]:
    """The hierarchy gate — ``HIERARCHY_CYCLE`` per **named** node, at the first node
    in ``public_key`` order of the cycle its witness closes.

    Pure, like :func:`relation_rule_issues`: ``nodes`` are the version's nodes and
    ``relations`` its relations, both with endpoints and types loaded. A relation is
    part of the subgraph when its type has ``hierarchy_role`` and **both** endpoints
    are among ``nodes`` — spec §5.6's "restricted to ``visible`` nodes". Every other
    relation is somebody else's finding (``DANGLING_NODE_HIDDEN_RELATION`` for an
    invisible endpoint, Task 9's ``DANGLING_RELATION_ENDPOINT`` for a foreign one).

    What counts as a cycle: a closed walk of **distinct relations**. A directed
    relation is one arc ``source → target``; an undirected one is the same edge
    traversable in *both* directions — so walking one undirected edge out and back is
    one relation twice and not a cycle (a single undirected hierarchy edge is not a
    cycle, a three-node undirected loop is), while a directed arc **parallel** to an
    undirected edge closes a real cycle with it. Multiple parents, orphan nodes and
    cycles outside the subgraph stay legal (spec §5.6).

    Two **undirected** hierarchy relations between one pair are two distinct
    relations, so walking one out and the other back is a closed walk of distinct
    relations — a cycle, and it is reported. Spec §5.6's "multiple relation types
    between the same pair" permits several relations on a pair; it is not a licence
    for a closed walk (ledger row ``12-note`` b; pinned by a test).

    How it is decided: for each relation of the subgraph, remove that one relation and
    ask whether its head still reaches its tail (an undirected relation: either end
    the other). If it does, that walk plus the relation is a cycle through it, and the
    issue names the cycle's **first node in ``public_key`` order**.

    The report is per **named** node, never one issue per cycle: the walk returned is a
    single shortest witness, so a relation carrying several cycles through it can name
    only some of them, and a cycle whose first node never becomes a walk's minimum goes
    unnamed (ledger row ``fix10-b``: 13 of 2 951 swept graphs carry at least one such
    cycle; the 4-node/6-row shape that pins this is in the module's test file).

    Why not a depth-first walk that colours nodes: finishing a node after a reversible
    edge was walked once loses every cycle that would re-enter it — a directed arc
    parallel to an undirected edge and a directed arc running into an undirected path
    were both reported as nothing, and the verdict followed the relations' row order
    (ledger Task 10 review, F1). This rule carries no such state: "is there a cycle"
    is a question about the graph, and it is answered from the graph.

    Determinism: nodes, every walk's neighbours and the reported node all follow
    ``public_key`` order, so both the verdict and the node it names are properties of
    the graph — never of relation row order, creation order or arrival order (ruling
    R11). Cycles that name the same node are reported once: ``Issue`` carries a single
    node key and the admin's next action is the same.
    """
    known = {node.pk: node for node in nodes}
    rows = [
        relation
        for relation in relations
        if relation.relation_type.hierarchy_role
        and relation.source_id in known
        and relation.target_id in known
    ]
    arcs: dict[int, list[tuple[int, int]]] = {node.pk: [] for node in nodes}
    for relation in rows:
        arcs[relation.source_id].append((relation.target_id, relation.pk))
        if not relation.directed:
            arcs[relation.target_id].append((relation.source_id, relation.pk))
    for node_arcs in arcs.values():
        # ``(neighbour key, relation pk)`` — total, so the arc list itself is a
        # property of the graph instead of following the rows' arrival order. The
        # relation pk only decides which of a node's *parallel* arcs comes first, and
        # the walk below reads nodes alone, so no verdict can turn on it.
        node_arcs.sort(key=lambda arc: (known[arc[0]].public_key, arc[1]))

    reported: set[str] = set()
    for relation in rows:
        directions = [(relation.target_id, relation.source_id)]
        if not relation.directed:
            directions.append((relation.source_id, relation.target_id))
        for origin, goal in directions:
            walk = _witness_walk(arcs, origin=origin, goal=goal, relation_pk=relation.pk)
            if walk is not None:
                reported.add(min(known[node_pk].public_key for node_pk in walk))
    issues = [_issue("HIERARCHY_CYCLE", node_key=key) for key in reported]
    return sorted(issues, key=_sort_key)


def validate_visibility(version: AtlasVersion) -> list[Issue]:
    """``DANGLING_NODE_HIDDEN_RELATION`` — a visible relation references a hidden node.

    Two queries. One issue per offending relation, carrying its composed key: the
    relation is the row the admin acts on (hide it, or unhide the node), and a
    relation whose *both* endpoints are hidden is one broken edge, not two. An
    endpoint that is not a node of this version is not "invisible" — Task 9's
    ``DANGLING_RELATION_ENDPOINT`` owns that case — so only this version's nodes are
    consulted.
    """
    visibility = dict(version.nodes.values_list("pk", "visible"))
    issues = [
        _issue("DANGLING_NODE_HIDDEN_RELATION", relation_key=relation.public_key)
        for relation in version.relations.filter(visible=True).select_related("source", "target")
        if any(
            endpoint.pk in visibility and not visibility[endpoint.pk]
            for endpoint in (relation.source, relation.target)
        )
    ]
    return sorted(issues, key=_sort_key)


# ---------------------------------------------------------------------------
# Task 12 — canonical facts, resolved once per node and shared
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NodeCanonicalFacts:
    """What the canonical-facing gates need to know about one node (Task 12).

    ``covered`` — the locales whose per-locale ``label_override`` is non-blank;
    ``resolved`` — each requested locale's resolution, or ``None`` when nothing
    resolves there; ``ambiguous`` — the locales whose resolution raised
    ``AmbiguousCanonicalRef``; ``unpublished`` — the locales where a row exists
    but does not pass ``objects.public()`` and no override covers them (the fact
    behind ``CANONICAL_SOURCE_UNPUBLISHED``).

    One resolution per ``(node, locale)`` is the whole point:
    :func:`validate_locale_projection` and :func:`validate_canonical_refs` ask
    different questions about the same facts, and resolving once removes the
    duplicated work (the Task 10 review measured ≈4.1–4.4 queries per node for
    the naive aggregation). A node outside the canonical registry, or without a
    translation key, has no reference at all: every requested locale is
    ``None``, no query is made, and the parity gate reports the gaps.
    """

    covered: frozenset[str]
    resolved: Mapping[str, CanonicalResolution | None]
    ambiguous: frozenset[str]
    unpublished: frozenset[str]


def _covered_locales(node: AtlasNode) -> frozenset[str]:
    """The locales whose per-locale ``label_override`` is non-blank (spec §5.4)."""
    return frozenset(
        translation.locale
        for translation in node.translations.all()
        if translation.label_override.strip()
    )


def _node_facts(
    node: AtlasNode, *, locales: Iterable[str] = DEFAULT_LOCALES, check_exists: bool = True
) -> NodeCanonicalFacts:
    """One node's canonical facts for ``locales`` — the shared unit of Task 12.

    Resolution is **per locale**, never through the pair helper: one ambiguous
    locale then masks only itself (the F3 rule of the Task 10 review), and the
    caller reads the exact locale whose resolution failed. ``check_exists`` adds
    the existence probe behind ``CANONICAL_SOURCE_UNPUBLISHED`` — the reference
    gate's question only, so the parity gate never pays for it.
    """
    covered = _covered_locales(node)
    source, key = node.canonical_model, node.canonical_translation_key
    requested = tuple(locales)
    if source not in CANONICAL_SOURCES or key is None:
        return NodeCanonicalFacts(
            covered=covered,
            resolved=dict.fromkeys(requested),
            ambiguous=frozenset(),
            unpublished=frozenset(),
        )
    resolved: dict[str, CanonicalResolution | None] = {}
    ambiguous: set[str] = set()
    for locale in requested:
        try:
            resolved[locale] = resolve_canonical(source, key, locale)
        except AmbiguousCanonicalRef as error:
            ambiguous.add(error.locale)
    unpublished: set[str] = set()
    if check_exists:
        for locale in requested:
            if locale in ambiguous or locale in covered or resolved.get(locale) is not None:
                continue
            if _canonical_row_exists(source, key, locale):
                unpublished.add(locale)
    return NodeCanonicalFacts(
        covered=covered,
        resolved=resolved,
        ambiguous=frozenset(ambiguous),
        unpublished=frozenset(unpublished),
    )


def canonical_facts(version: AtlasVersion) -> dict[int, NodeCanonicalFacts]:
    """``{node pk: NodeCanonicalFacts}`` for the version's visible nodes (Task 12).

    The aggregator's shared work: two queries (the visible nodes and their
    translations) plus one resolution pass per node, handed to every validator
    that needs canonical resolution. The validators answer exactly as they do
    standalone — the same verdicts, with the repeated queries gone.
    """
    nodes = version.nodes.filter(visible=True).prefetch_related("translations")
    return {node.pk: _node_facts(node) for node in nodes}


def _facts_for(
    facts: Mapping[int, NodeCanonicalFacts] | None,
    node: AtlasNode,
    *,
    locales: Iterable[str],
    check_exists: bool = True,
) -> NodeCanonicalFacts:
    """The node's shared facts when the aggregator supplied them, else its own.

    A node the caller left out of a partial map falls back to computing its own
    facts, so a partial map can never produce a verdict the standalone call would
    not have produced.
    """
    if facts is not None:
        shared = facts.get(node.pk)
        if shared is not None:
            return shared
    return _node_facts(node, locales=locales, check_exists=check_exists)


def validate_locale_projection(
    version: AtlasVersion, *, facts: Mapping[int, NodeCanonicalFacts] | None = None
) -> list[Issue]:
    """The locale gates — ``MISSING_LOCALE_PROJECTION`` and ``GROUP_LOCALE_MISSING``.

    Per **visible** node and each locale of :data:`DEFAULT_LOCALES`, the locale must
    resolve from that locale's own canonical row or from a non-blank
    ``label_override`` **for that locale** (spec §5.4): a node whose override is for
    the locale that already resolves leaves the missing one missing — there is no
    cross-locale fallback, and there is no node-wide override either. A
    ``canonical_model = "none"`` node has no canonical row by construction and needs
    both overrides. An invisible node is not parity-gated at all: §20.1's note says
    the visibility rule governs it and no stricter rule is invented; the same note
    makes this code a blocker in both directions and never a warning.

    A locale with more than one published row is *not* missing — the payload is
    ambiguous, which :func:`validate_canonical_refs` reports as
    ``AMBIGUOUS_CANONICAL_REF``; this gate does not double-report it.

    Groups: every group of the version needs a non-blank label for both locales
    (spec §5.5). A retired group is judged like any other — the §20.1 rule text has
    no qualifier, and this module already records that reasoning for Task 9's
    relation rules.

    Resolution goes through :func:`apps.atlas.canonical.resolve_canonical`, the
    exact-locale, publish-gated resolver — one locale at a time, so an ambiguous
    locale masks only itself (ledger Task 10 review, F3): the ambiguity is the
    *reference* gate's finding, and another locale's gap is still reported here.
    A node whose every locale is covered by overrides costs no query at all, and a
    node of a family outside the registry (``none``) is never handed to the
    resolver, which raises ``KeyError`` for it by design.

    Inside :func:`validate_version` the facts arrive pre-computed
    (:func:`canonical_facts`) instead of being resolved again per validator; a
    standalone call resolves exactly the missing locales, as it always did — the
    verdict is the same either way. One issue is emitted per missing locale: an
    ``Issue`` carries no locale, so the raw facts stay per locale and
    :func:`validate_version` collapses the indistinguishable rows per node when it
    builds the report (ledger row ``12-note`` a).
    """
    issues: list[Issue] = []
    nodes = version.nodes.filter(visible=True).prefetch_related("translations")
    for node in nodes:
        covered = _covered_locales(node)
        missing_locales = [locale for locale in DEFAULT_LOCALES if locale not in covered]
        if not missing_locales:
            continue
        node_facts = _facts_for(facts, node, locales=missing_locales, check_exists=False)
        for locale in missing_locales:
            if locale in node_facts.ambiguous:
                continue
            if node_facts.resolved.get(locale) is None:
                issues.append(_issue("MISSING_LOCALE_PROJECTION", node_key=node.public_key))
    for group in version.groups.prefetch_related("translations"):
        labelled = {
            translation.locale
            for translation in group.translations.all()
            if translation.label.strip()
        }
        if set(DEFAULT_LOCALES) - labelled:
            issues.append(_issue("GROUP_LOCALE_MISSING", group_key=group.public_key))
    return sorted(issues, key=_sort_key)


def validate_taxonomy(version: AtlasVersion) -> list[Issue]:
    """``NODE_TYPE_INACTIVE`` — a node uses a retired node type (spec §20.1).

    One query. The relation half of the lifecycle is Task 9's
    ``RELATION_TYPE_INACTIVE`` inside :func:`validate_relations`; this is the node
    half, and it judges **every** node of the version, visible or not, exactly as
    Task 9 judges every relation (the rule text is not visibility-conditional, and a
    hidden node's retired type would otherwise pass the gate silently and reopen on
    the next unhide).

    The other two lifecycle rules stay where they are enforced and are deliberately
    not duplicated here: ``AtlasNode.node_type`` is ``PROTECT`` (an in-use type
    cannot be deleted) and ``TaxonomyKeyMixin`` refuses a rename once a row uses the
    key.
    """
    issues = [
        _issue("NODE_TYPE_INACTIVE", node_key=node.public_key)
        for node in version.nodes.filter(node_type__active=False)
    ]
    return sorted(issues, key=_sort_key)


def validate_canonical_refs(
    version: AtlasVersion, *, facts: Mapping[int, NodeCanonicalFacts] | None = None
) -> list[Issue]:
    """The canonical reference itself — present, published, unambiguous (spec §5.4).

    Scoped to the **visible** nodes: the reference exists to fill a projection, and
    an invisible node is neither published nor traversed (§20.1's note), so hiding a
    node drops its reference issues together with its parity ones.

    Three questions, three codes, never each other's alias:

    * *is there a reference?* A node whose ``canonical_model`` is not ``none`` must
      carry a ``canonical_translation_key`` — ``CANONICAL_SOURCE_MISSING``. An
      override supplies copy, never a reference, so this one blocks even when both
      locales are covered;
    * *does the record resolve?* A row that **exists** and does not pass
      ``objects.public()`` in a locale that lacks an override is
      ``CANONICAL_SOURCE_UNPUBLISHED``. A locale with no row at all is the parity
      gate's ``MISSING_LOCALE_PROJECTION``: "fix or publish the record" and "you
      have no copy for this locale" are different instructions to the admin;
    * *is it unambiguous?* More than one published row for one
      ``(family, translation_key, locale)`` is ``AMBIGUOUS_CANONICAL_REF`` — detected
      by the resolver and never guessed around, reported whether or not an override
      hides the canonical link, because the reference itself is the defect.

    One issue per node and code: ``Issue`` carries no locale, so repeating the same
    finding per locale would only inflate the report. A family the closed registry
    does not list is skipped — ``none`` needs overrides instead (the parity gate) and
    an unknown family makes the resolver raise ``KeyError`` by design. Inside
    :func:`validate_version` the resolutions arrive pre-computed
    (:func:`canonical_facts`); standalone, this validator resolves both locales
    itself, exactly as it always did.
    """
    issues: list[Issue] = []
    nodes = version.nodes.filter(visible=True).prefetch_related("translations")
    for node in nodes:
        source, key = node.canonical_model, node.canonical_translation_key
        if source not in CANONICAL_SOURCES:
            # ``none`` (an Atlas-only structural node) is not in the registry: it has
            # no record by construction and needs both overrides instead — the parity
            # gate's rule. A bogus family is skipped for the same reason; the resolver
            # raises ``KeyError`` for it by design.
            continue
        if key is None:
            issues.append(_issue("CANONICAL_SOURCE_MISSING", node_key=node.public_key))
            continue
        node_facts = _facts_for(facts, node, locales=DEFAULT_LOCALES, check_exists=True)
        if node_facts.ambiguous:
            issues.append(_issue("AMBIGUOUS_CANONICAL_REF", node_key=node.public_key))
        if node_facts.unpublished:
            issues.append(_issue("CANONICAL_SOURCE_UNPUBLISHED", node_key=node.public_key))
    return sorted(issues, key=_sort_key)


def _canonical_row_exists(source: str, translation_key: UUID, locale: str) -> bool:
    """Whether *any* row — published or not — exists for one family/key/locale.

    The proof behind ``CANONICAL_SOURCE_UNPUBLISHED``: the record exists and does not
    resolve, rather than not existing at all. ``_base_manager`` on purpose, never
    ``objects.public()`` — the question is existence, and a default manager that
    later gains a filter would silently turn every unpublished record into a missing
    one.
    """
    return (
        CANONICAL_SOURCES[source]
        ._base_manager.filter(translation_key=translation_key, locale=locale)
        .exists()
    )


# ---------------------------------------------------------------------------
# Task 12 — the layout and pin gates, the §20.2 warnings, and the report.
# ---------------------------------------------------------------------------


def _stored_coordinate(
    layout: Mapping[str, object], key: str
) -> tuple[float, float, float] | None:
    """The stored coordinate of ``key``, or ``None`` when the layout has none.

    A coordinate is usable when it is a three-item sequence of finite numbers.
    Anything else — absent, a short list, a string, a mapping — is not a
    coordinate a projection could serve, so the node is reported as
    :func:`validate_layout` reports it. The finiteness half is **defence in
    depth**: the ``JSONField``'s ``JSON_VALID`` check already rejects a
    non-finite value at the database (`NaN` cannot be stored at all — reproduced
    while writing this rule), but a layout dict handed in some other way must not
    reach a projection either.
    """
    coordinate = layout.get(key)
    if not isinstance(coordinate, (list, tuple)) or len(coordinate) != 3:
        return None
    values: list[float] = []
    for value in coordinate:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        if not math.isfinite(value):
            return None
        values.append(float(value))
    return (values[0], values[1], values[2])


def validate_layout(version: AtlasVersion) -> list[Issue]:
    """``MISSING_LAYOUT`` — a visible node has no stored coordinate (spec §8.3, §20.1).

    One query. The activation checklist's second line (spec §8.3: "Confirm that
    layout coordinates exist for every visible node") and §20.1's row ask the
    same question of the **stored** layout: every visible node needs a coordinate
    the projection can serve, and a stored value that is absent or not three
    finite numbers is no such coordinate. Hidden nodes get no coordinate at all
    (spec §12.2's pipeline walks visible nodes only), so they are not judged.
    """
    layout = version.layout if isinstance(version.layout, Mapping) else {}
    issues = [
        _issue("MISSING_LAYOUT", node_key=node.public_key)
        for node in version.nodes.filter(visible=True)
        if _stored_coordinate(layout, node.public_key) is None
    ]
    return sorted(issues, key=_sort_key)


def _pin_defect(node: AtlasNode) -> bool:
    """Whether one node's pin trio is invalid (spec §12.5; the model's own rules).

    Four ways, all of them read on a row the ORM can hold (``create`` and
    ``update`` never call ``clean()``): ``pin_x``/``pin_y`` are set together or
    not at all; ``pin_z`` requires both; and every set value is a finite number
    inside :data:`PIN_BOUND`.
    """
    if (node.pin_x is None) != (node.pin_y is None):
        return True
    if node.pin_x is None and node.pin_z is not None:
        return True
    for value in (node.pin_x, node.pin_y, node.pin_z):
        if value is None:
            continue
        if not math.isfinite(value) or abs(value) > PIN_BOUND:
            return True
    return False


def validate_pins(version: AtlasVersion) -> list[Issue]:
    """``INVALID_PIN`` — a pin is half-set, non-finite or out of bounds (spec §12.5).

    One query. Every node of the version is judged, visible or not, exactly as
    :func:`validate_taxonomy` judges every node: a pin that is invalid now stays
    invalid after an unhide, and a hidden row's defect must not reopen silently.
    ``OVERLAPPING_PINS`` — the *pair* rule — is a warning and lives in
    :func:`warning_issues`, because spec §20.2 makes it one.
    """
    issues = [
        _issue("INVALID_PIN", node_key=node.public_key)
        for node in version.nodes.all()
        if _pin_defect(node)
    ]
    return sorted(issues, key=_sort_key)


def _drawn_radii(nodes: Sequence[AtlasNode]) -> dict[str, float]:
    """The §12.2 step 2 drawn radii, read from the engine's own constants.

    ``radius = r_min + (r_max - r_min) · importance/100``; the ``identity``
    anchor draws at ``anchor_ratio`` × the widest domain radius. The constants
    are imported **inside** the function: this module stays a pure rules module
    whose module-level imports are the models it needs (spec §20), and the
    warning and the scene can then never disagree about a scene number. The
    caller must have ``node_type`` loaded (``select_related``) — the anchor rule
    reads it.
    """
    from apps.atlas.layout import ANCHOR_NODE_TYPE_KEY, LAYOUT_CONSTANTS

    low = LAYOUT_CONSTANTS["r_min"]
    high = LAYOUT_CONSTANTS["r_max"]
    radii = {node.public_key: low + (high - low) * (node.importance / 100) for node in nodes}
    anchors = [node.public_key for node in nodes if node.node_type.key == ANCHOR_NODE_TYPE_KEY]
    if anchors:
        widest = max(
            (radius for key, radius in radii.items() if key not in set(anchors)),
            default=radii[anchors[0]],
        )
        for key in anchors:
            radii[key] = LAYOUT_CONSTANTS["anchor_ratio"] * widest
    return radii


def _summary_missing(
    node: AtlasNode, facts: Mapping[int, NodeCanonicalFacts] | None
) -> bool:
    """Whether no locale resolves a non-blank summary for this node (spec §20.2).

    A per-locale ``summary_override`` is the node's own copy; otherwise the
    summary is the locale's canonical row's (spec §5.4.2). The warning is
    node-level — "no summary resolved for a visible node" — so a summary in
    *any* locale clears it, and an override anywhere means no resolution is
    needed at all.
    """
    if any(translation.summary_override.strip() for translation in node.translations.all()):
        return False
    node_facts = _facts_for(facts, node, locales=DEFAULT_LOCALES, check_exists=False)
    return not any(
        resolution is not None and resolution.summary.strip()
        for resolution in node_facts.resolved.values()
    )


def warning_issues(
    version: AtlasVersion, *, facts: Mapping[int, NodeCanonicalFacts] | None = None
) -> list[Issue]:
    """Every warning of spec §20.2 over one version — informational, never blocking.

    The graph-shape warnings judge the **visible** graph, because that is the
    graph the payload serves and §20.2's own texts say "visible" where they mean
    it (``SUMMARY_MISSING``, ``SCALE_*``); ``ISOLATED_NODE``/``HIGH_DEGREE_HUB``
    are §12.7's *scene* warnings for the same graph:

    * ``ISOLATED_NODE`` — no relation and no group membership;
    * ``NO_INBOUND_RELATIONS`` / ``NO_OUTBOUND_RELATIONS`` — no relation of that
      direction; an undirected row counts as both, exactly as
      :func:`hierarchy_rule_issues` reads one;
    * ``HIGH_DEGREE_HUB`` — more than :data:`SCALE_WARN_THRESHOLDS`'s ``hubs``
      *distinct* relations touch the node (a self-loop is one relation);
    * ``SUMMARY_MISSING`` — no locale resolves a non-blank summary;
    * ``SCALE_NODES`` / ``SCALE_RELATIONS`` — more visible nodes/relations than
      the thresholds allow;
    * ``SINGLE_LEVEL_HIERARCHY`` — the visible hierarchy subgraph (hierarchy
      role, visible relation, both endpoints visible — the subgraph
      :func:`validate_hierarchy` walks) is empty;
    * ``OVERLAPPING_PINS`` — a pinned visible node closer than the sum of the
      drawn radii to another pinned visible node outside its group.

    The two taxonomy warnings read §20.2 literally — "no node/relation in this
    version" — so they count every row, and they fire **once per version**: an
    ``Issue`` carries no type key, so one row per unused type would be a
    duplicate no reader could tell apart (the report's per-``(code, entity)``
    rule would collapse it anyway).
    """
    visible_nodes = list(
        version.nodes.filter(visible=True)
        .select_related("node_type")
        .prefetch_related("translations")
    )
    visible_relations = list(
        version.relations.filter(visible=True).select_related(
            "source", "target", "relation_type"
        )
    )
    visible_pks = {node.pk for node in visible_nodes}
    served = [
        relation
        for relation in visible_relations
        if relation.source_id in visible_pks and relation.target_id in visible_pks
    ]

    incident: dict[int, set[int]] = {}
    incoming: dict[int, int] = {}
    outgoing: dict[int, int] = {}
    for relation in served:
        incident.setdefault(relation.source_id, set()).add(relation.pk)
        incident.setdefault(relation.target_id, set()).add(relation.pk)
        ends = [(relation.source_id, relation.target_id)]
        if not relation.directed:
            ends.append((relation.target_id, relation.source_id))
        for source_id, target_id in ends:
            outgoing[source_id] = outgoing.get(source_id, 0) + 1
            incoming[target_id] = incoming.get(target_id, 0) + 1

    memberships: dict[int, set[int]] = {}
    membership_rows = AtlasGroupMembership.objects.filter(group__version=version).values_list(
        "group_id", "node_id"
    )
    for group_id, node_id in membership_rows:
        memberships.setdefault(node_id, set()).add(group_id)

    issues: list[Issue] = []
    for node in visible_nodes:
        degree = len(incident.get(node.pk, ()))
        if degree == 0 and node.pk not in memberships:
            issues.append(_issue("ISOLATED_NODE", node_key=node.public_key))
        if incoming.get(node.pk, 0) == 0:
            issues.append(_issue("NO_INBOUND_RELATIONS", node_key=node.public_key))
        if outgoing.get(node.pk, 0) == 0:
            issues.append(_issue("NO_OUTBOUND_RELATIONS", node_key=node.public_key))
        if degree > SCALE_WARN_THRESHOLDS["hubs"]:
            issues.append(_issue("HIGH_DEGREE_HUB", node_key=node.public_key))
        if _summary_missing(node, facts):
            issues.append(_issue("SUMMARY_MISSING", node_key=node.public_key))

    if len(visible_nodes) > SCALE_WARN_THRESHOLDS["nodes"]:
        issues.append(_issue("SCALE_NODES"))
    if len(visible_relations) > SCALE_WARN_THRESHOLDS["relations"]:
        issues.append(_issue("SCALE_RELATIONS"))
    if AtlasNodeType.objects.filter(active=True).exclude(nodes__version=version).exists():
        issues.append(_issue("UNUSED_NODE_TYPE"))
    if AtlasRelationType.objects.filter(active=True).exclude(relations__version=version).exists():
        issues.append(_issue("UNUSED_RELATION_TYPE"))
    if not any(relation.relation_type.hierarchy_role for relation in served):
        issues.append(_issue("SINGLE_LEVEL_HIERARCHY"))

    pinned = [
        node
        for node in visible_nodes
        if node.pin_x is not None
        and node.pin_y is not None
        and math.isfinite(node.pin_x)
        and math.isfinite(node.pin_y)
    ]
    if len(pinned) > 1:
        radii = _drawn_radii(visible_nodes)
        overlapping: set[str] = set()
        ordered = sorted(pinned, key=lambda node: node.public_key)
        for index, first in enumerate(ordered):
            for second in ordered[index + 1 :]:
                if memberships.get(first.pk, set()) & memberships.get(second.pk, set()):
                    continue
                distance = math.hypot(first.pin_x - second.pin_x, first.pin_y - second.pin_y)
                if distance < radii[first.public_key] + radii[second.public_key]:
                    overlapping.update((first.public_key, second.public_key))
        issues.extend(_issue("OVERLAPPING_PINS", node_key=key) for key in sorted(overlapping))

    return sorted(issues, key=_sort_key)


def _deduplicate(issues: Iterable[Issue]) -> list[Issue]:
    """One row per ``(code, entity)`` — the report's own de-duplication (Task 12).

    ``Issue`` carries no locale, so the parity gate's per-locale rows for one
    node are indistinguishable strings; a per-type row for the taxonomy warnings
    would be another. Keeping the first occurrence of each marker removes exactly
    the rows no reader could tell apart, and the validators themselves keep
    emitting the honest per-locale facts (ledger row ``12-note`` a).
    """
    seen: set[tuple[str, str | None, str | None, str | None]] = set()
    deduplicated: list[Issue] = []
    for issue in issues:
        marker = (issue.code, issue.node_key, issue.relation_key, issue.group_key)
        if marker in seen:
            continue
        seen.add(marker)
        deduplicated.append(issue)
    return deduplicated


def _collapse_duplicate_pairs(issues: list[Issue], version: AtlasVersion) -> list[Issue]:
    """One ``DUPLICATE_RELATION`` per conflicting *pair* (ledger row ``9-note``).

    The rule layer reports one issue per composed key spelling. When an
    undirected row and a reversed **directed** row share an identity triple, both
    spellings name one conflict; the report keeps the first spelling in sorted
    order, so the row count is a property of the graph rather than of the two
    rows' authored directions. Keys are grouped by *shared identity triple*, not
    by unordered endpoint pair: two directed rows in opposite directions are
    legal and share no identity, so they stay unreported and uncollapsed.

    The relation rows are read only when a duplicate issue can be collapsed, so
    the ordinary report pays no extra query.
    """
    duplicate_keys = {
        issue.relation_key
        for issue in issues
        if issue.code == "DUPLICATE_RELATION" and issue.relation_key is not None
    }
    if len(duplicate_keys) < 2:
        return issues
    parent: dict[str, str] = {}

    def find(key: str) -> str:
        parent.setdefault(key, key)
        root = key
        while parent[root] != root:
            root = parent[root]
        while parent[key] != root:
            parent[key], key = root, parent[key]
        return root

    owners: dict[tuple[str, str, str], list[str]] = {}
    for relation in version.relations.select_related("source", "target", "relation_type"):
        key = _composed_key(relation)
        if key is None or key not in duplicate_keys:
            continue
        for identity in _identities(relation):
            owners.setdefault(identity, []).append(key)
    for keys in owners.values():
        for other in keys[1:]:
            first_root, second_root = find(keys[0]), find(other)
            if first_root != second_root:
                parent[second_root] = first_root

    kept: set[str] = set()
    survivors: list[Issue] = []
    for issue in issues:
        if issue.code != "DUPLICATE_RELATION" or issue.relation_key is None:
            survivors.append(issue)
            continue
        root = find(issue.relation_key)
        if root in kept:
            continue
        kept.add(root)
        survivors.append(issue)
    return survivors


def validate_version(version: AtlasVersion) -> ValidationReport:
    """The publish gate for one version (spec §20, §8.3) — the aggregate report.

    Every blocking group this module exposes, in the spec's order (blocking
    codes first, then warnings), each list sorted by ``(code, entity key)``:

    * :func:`validate_relations` — the §20.1 relation rules (Task 9);
    * :func:`validate_hierarchy` — the hierarchy-role subgraph is a DAG;
    * :func:`validate_visibility` — a visible relation references a hidden node
      (the plan's Task 12 step names four functions; this one is the fourth of
      Task 10's and dropping it would lose a blocker);
    * :func:`validate_locale_projection` — both locales resolve for every visible
      node, and every group carries copy in both;
    * :func:`validate_taxonomy` — no node uses a retired type;
    * :func:`validate_canonical_refs` — the reference is present, published and
      unambiguous;
    * :func:`validate_layout` — every visible node has a stored coordinate;
    * :func:`validate_pins` — every pin is whole, finite and inside the scene;
    * :func:`warning_issues` — the §20.2 warnings, never blocking.

    The canonical facts behind the two canonical gates are resolved **once per
    node** (:func:`canonical_facts`) and shared: the same verdicts the standalone
    calls reach, without resolving each node's reference again for every gate.
    The merged blocking list is then de-duplicated per ``(code, entity)`` and per
    conflicting relation pair, so a fact that differs only by an attribute the
    wire shape cannot carry is reported once (ledger rows ``12-note`` a and
    ``9-note``).
    """
    facts = canonical_facts(version)
    blocking = [
        *validate_relations(version),
        *validate_hierarchy(version),
        *validate_visibility(version),
        *validate_locale_projection(version, facts=facts),
        *validate_taxonomy(version),
        *validate_canonical_refs(version, facts=facts),
        *validate_layout(version),
        *validate_pins(version),
    ]
    blocking = _collapse_duplicate_pairs(_deduplicate(sorted(blocking, key=_sort_key)), version)
    warnings = _deduplicate(sorted(warning_issues(version, facts=facts), key=_sort_key))
    return ValidationReport(blocking=blocking, warnings=warnings)


# ---------------------------------------------------------------------------
# Task 12 — the payload contract check (the serving gate of Tasks 14/15).
# ---------------------------------------------------------------------------


#: The envelope of spec §10.2 — the keys a projected payload must carry.
_PAYLOAD_FIELDS: tuple[str, ...] = (
    "contractVersion",
    "locale",
    "version",
    "nodeTypes",
    "relationTypes",
    "groups",
    "nodes",
    "relations",
)


def _payload_entity_key(entry: object) -> str | None:
    """The non-empty string ``key`` of a payload entry, or ``None`` when unusable."""
    if not isinstance(entry, Mapping):
        return None
    key = entry.get("key")
    return key if isinstance(key, str) and key else None


def _is_payload_entity_key(value: str) -> bool:
    """A node/group key: the single-key grammar, and never a relation key."""
    return "~" not in value and is_valid_public_key(value)


def _payload_collection(
    entries: object, *, grammar: Callable[[str], bool], attribute: str | None = None
) -> tuple[set[str], list[Issue]]:
    """Validate one collection's entity keys — grammar and uniqueness together.

    Returns the keys found and the key issues: a missing or malformed key is
    ``PAYLOAD_CONTRACT_INVALID`` (spec §20.1's row names "key grammar"), a
    repeated one is ``DUPLICATE_PUBLIC_KEY`` ("a node, group or composed relation
    key is not unique") — for the three *entity* collections, which pass the
    issue attribute their keys belong to. A catalog is a vocabulary rather than
    a collection of entities, so its duplicate keys are a contract defect
    (``attribute`` stays ``None``). Both are blocking, and the caller reads the
    returned keys for its reference checks, so one pass answers both questions.
    """
    if not isinstance(entries, list):
        return set(), [_issue("PAYLOAD_CONTRACT_INVALID")]
    keys: set[str] = set()
    issues: list[Issue] = []
    for entry in entries:
        key = _payload_entity_key(entry)
        if key is None or not grammar(key):
            issues.append(_issue("PAYLOAD_CONTRACT_INVALID"))
            continue
        if key in keys:
            if attribute is None:
                issues.append(_issue("PAYLOAD_CONTRACT_INVALID"))
            else:
                issues.append(_issue("DUPLICATE_PUBLIC_KEY", **{attribute: key}))
            continue
        keys.add(key)
    return keys, issues


def _coordinate_defect(node: Mapping) -> str | None:
    """``"missing"``/``"invalid"`` for a node's ``position``, ``None`` when it is fine.

    Absence is ``MISSING_LAYOUT`` — §20.1's own row: "a visible node has no
    stored coordinate" (a node whose stored layout has no coordinate reaches the
    payload without ``position``). A position that is present but broken — not a
    mapping, an axis missing or non-finite — is the contract defect §20.1 names
    under ``PAYLOAD_CONTRACT_INVALID`` ("coordinate finiteness").
    """
    position = node.get("position")
    if position is None:
        return "missing"
    if not isinstance(position, Mapping):
        return "invalid"
    for axis in ("x", "y", "z"):
        value = position.get(axis)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return "invalid"
        if not math.isfinite(value):
            return "invalid"
    return None


def validate_payload_contract(projection: dict) -> list[Issue]:
    """A projected payload against its own contract (spec §20.1, §11.4) — the serving gate.

    **Pure**: no ORM access, no queries, and the input is never reshaped (spec
    §11.4: "the validator validates, never reshapes"). Task 15 calls it on Task
    14's freshly built projection and answers a non-empty list with its
    fail-closed ``atlas_internal`` response; a payload that validates returns
    ``[]``. Malformed input is a finding, never a traceback: anything that is not
    a mapping at all — or a collection that is not a list, an entity that is not
    a mapping — is reported as ``PAYLOAD_CONTRACT_INVALID``.

    What it checks, per §20.1's ``PAYLOAD_CONTRACT_INVALID`` row (catalog
    references, key grammar, coordinate finiteness) and the two codes the plan
    pairs with it:

    * the §10.2 envelope: every documented field present, ``contractVersion`` a
      non-empty string, ``locale`` one of :data:`DEFAULT_LOCALES`, the ``version``
      block a mapping whose ``id`` is present and whose ``nodeCount`` /
      ``relationCount`` equal the collections' lengths;
    * catalogs: entries keyed by the key grammar, without duplicates, and every
      entity the payload references (node ``type``, relation ``type``, relation
      ``source``/``target``, group ``nodeKeys``) resolves inside them —
      ``PAYLOAD_CONTRACT_INVALID`` otherwise;
    * keys: node/group keys are single segments that never carry the relation
      separator; relation keys are composed keys validated **per segment**;
      duplicates are ``DUPLICATE_PUBLIC_KEY``;
    * coordinates: ``position`` present with three finite numbers —
      ``MISSING_LAYOUT`` when absent, ``PAYLOAD_CONTRACT_INVALID`` when broken.

    The answer is sorted by ``(code, entity key)`` like the report's, so a
    caller's diagnostics read the same way.
    """
    if not isinstance(projection, Mapping):
        return [_issue("PAYLOAD_CONTRACT_INVALID")]

    issues: list[Issue] = []
    for required in _PAYLOAD_FIELDS:
        if required not in projection:
            issues.append(_issue("PAYLOAD_CONTRACT_INVALID"))
    contract_version = projection.get("contractVersion")
    if not isinstance(contract_version, str) or not contract_version:
        issues.append(_issue("PAYLOAD_CONTRACT_INVALID"))
    if projection.get("locale") not in DEFAULT_LOCALES:
        issues.append(_issue("PAYLOAD_CONTRACT_INVALID"))
    version_block = projection.get("version")
    if not isinstance(version_block, Mapping) or version_block.get("id") is None:
        issues.append(_issue("PAYLOAD_CONTRACT_INVALID"))

    catalog_keys: dict[str, set[str]] = {}
    for catalog in ("nodeTypes", "relationTypes"):
        keys, catalog_issues = _payload_collection(
            projection.get(catalog), grammar=_is_payload_entity_key
        )
        catalog_keys[catalog] = keys
        issues.extend(catalog_issues)

    node_keys, node_key_issues = _payload_collection(
        projection.get("nodes"), attribute="node_key", grammar=_is_payload_entity_key
    )
    group_keys, group_key_issues = _payload_collection(
        projection.get("groups"), attribute="group_key", grammar=_is_payload_entity_key
    )
    relation_keys, relation_key_issues = _payload_collection(
        projection.get("relations"),
        attribute="relation_key",
        grammar=is_valid_relation_public_key,
    )
    issues.extend(node_key_issues + group_key_issues + relation_key_issues)

    nodes = projection.get("nodes")
    if isinstance(nodes, list):
        for entry in nodes:
            key = _payload_entity_key(entry)
            if key is None or key not in node_keys or not isinstance(entry, Mapping):
                continue
            if entry.get("type") not in catalog_keys.get("nodeTypes", set()):
                issues.append(_issue("PAYLOAD_CONTRACT_INVALID", node_key=key))
                continue
            defect = _coordinate_defect(entry)
            if defect == "missing":
                issues.append(_issue("MISSING_LAYOUT", node_key=key))
            elif defect == "invalid":
                issues.append(_issue("PAYLOAD_CONTRACT_INVALID", node_key=key))

    relations = projection.get("relations")
    if isinstance(relations, list):
        for entry in relations:
            key = _payload_entity_key(entry)
            if key is None or key not in relation_keys or not isinstance(entry, Mapping):
                continue
            if (
                entry.get("type") not in catalog_keys.get("relationTypes", set())
                or entry.get("source") not in node_keys
                or entry.get("target") not in node_keys
            ):
                issues.append(_issue("PAYLOAD_CONTRACT_INVALID", relation_key=key))

    groups = projection.get("groups")
    if isinstance(groups, list):
        for entry in groups:
            key = _payload_entity_key(entry)
            if key is None or key not in group_keys or not isinstance(entry, Mapping):
                continue
            members = entry.get("nodeKeys")
            if not isinstance(members, list) or any(member not in node_keys for member in members):
                issues.append(_issue("PAYLOAD_CONTRACT_INVALID", group_key=key))

    if isinstance(version_block, Mapping):
        node_count = len(nodes) if isinstance(nodes, list) else None
        relation_count = len(relations) if isinstance(relations, list) else None
        if (
            version_block.get("nodeCount") != node_count
            or version_block.get("relationCount") != relation_count
        ):
            issues.append(_issue("PAYLOAD_CONTRACT_INVALID"))

    return sorted(issues, key=_sort_key)
