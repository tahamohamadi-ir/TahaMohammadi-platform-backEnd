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
  exposes — all five, not four);
* :func:`validate_locale_projection` — does every visible node resolve in *both*
  locales, and does every group carry copy in both? (spec §5.4/§5.5; the parity
  note of §20.1);
* :func:`validate_taxonomy` — does a node use a retired node type? (the relation
  half is Task 9's ``RELATION_TYPE_INACTIVE``);
* :func:`validate_canonical_refs` — is the canonical reference present, published
  and unambiguous? (spec §5.4).

Scope decisions of Tasks 9–10, recorded because the plan leaves them open:

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
  the relation rules applies (a retired group can be re-activated).
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from uuid import UUID

from apps.atlas.canonical import (
    CANONICAL_SOURCES,
    DEFAULT_LOCALES,
    AmbiguousCanonicalRef,
    CanonicalResolution,
    resolve_canonical,
    resolve_canonical_pair,
)
from apps.atlas.models import (
    SELF_LOOP_POLICIES,
    AtlasNode,
    AtlasRelation,
    AtlasRelationType,
    AtlasVersion,
)

# ``apps.atlas.models`` also defines a name ``CANONICAL_SOURCES`` — a
# ``TextChoices`` vocabulary for the ``AtlasNodeType.canonical_source`` /
# ``AtlasNode.canonical_model`` *columns*. The name imported above is the
# resolver **registry** (``apps.atlas.canonical``): its keys are that same
# canonical-source vocabulary and its values are the model classes. This module
# never needs the column vocabulary — membership in the registry is the question
# it asks, and ``none`` (an Atlas-only structural node) is not in it.

#: Codes whose violation blocks publication (spec §20.1, plus the plan's
#: ``DIRECTION_NOT_OVERRIDABLE``). Task 9's relation rules first, then Task 10's
#: node/locale/taxonomy codes — each group in the spec table's order, the
#: plan-added code at the end of its group; the order is not semantic. Task 12
#: freezes the complete vocabulary against spec §20.1 + §20.2 and re-pins this
#: tuple, including the carry-forward recorded in the ledger:
#: ``DIRECTION_NOT_OVERRIDABLE`` is a real blocker with no §20.1 row of its own.
BLOCKING_CODES: tuple[str, ...] = (
    "DANGLING_RELATION_ENDPOINT",
    "RELATION_TYPE_INACTIVE",
    "RELATION_TYPE_NOT_ALLOWED",
    "SELF_LOOP_FORBIDDEN",
    "DUPLICATE_RELATION",
    "DIRECTION_NOT_OVERRIDABLE",
    "DANGLING_NODE_HIDDEN_RELATION",
    "CANONICAL_SOURCE_MISSING",
    "CANONICAL_SOURCE_UNPUBLISHED",
    "MISSING_LOCALE_PROJECTION",
    "AMBIGUOUS_CANONICAL_REF",
    "NODE_TYPE_INACTIVE",
    "HIERARCHY_CYCLE",
    "GROUP_LOCALE_MISSING",
)

#: Warnings are never blocking (spec §20.2). Task 9 implemented none and Task 10
#: adds none — every code of the node/locale pass below blocks publication, and
#: ``MISSING_LOCALE_PROJECTION`` in particular is never emitted as a warning
#: (§20.1's note). The warning vocabulary arrives with Task 12.
WARNING_CODES: tuple[str, ...] = ()

#: The whole implemented vocabulary — every code this module may emit.
ATLAS_ISSUE_CODES: tuple[str, ...] = BLOCKING_CODES + WARNING_CODES

#: ``messageToken`` per code: one token per code, ``atlas.*`` namespace, mirroring
#: the AB-06 validator (``graph.*``). The admin renders the token, never the code.
_MESSAGE_TOKENS: dict[str, str] = {
    "AMBIGUOUS_CANONICAL_REF": "atlas.ambiguousCanonicalRef",
    "CANONICAL_SOURCE_MISSING": "atlas.canonicalSourceMissing",
    "CANONICAL_SOURCE_UNPUBLISHED": "atlas.canonicalSourceUnpublished",
    "DANGLING_NODE_HIDDEN_RELATION": "atlas.danglingNodeHiddenRelation",
    "DANGLING_RELATION_ENDPOINT": "atlas.danglingRelationEndpoint",
    "DIRECTION_NOT_OVERRIDABLE": "atlas.directionNotOverridable",
    "DUPLICATE_RELATION": "atlas.duplicateRelation",
    "GROUP_LOCALE_MISSING": "atlas.groupLocaleMissing",
    "HIERARCHY_CYCLE": "atlas.hierarchyCycle",
    "MISSING_LOCALE_PROJECTION": "atlas.missingLocaleProjection",
    "NODE_TYPE_INACTIVE": "atlas.nodeTypeInactive",
    "RELATION_TYPE_INACTIVE": "atlas.relationTypeInactive",
    "RELATION_TYPE_NOT_ALLOWED": "atlas.relationTypeNotAllowed",
    "SELF_LOOP_FORBIDDEN": "atlas.selfLoopForbidden",
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

    Task 9 produces the type; the aggregator that fills it is ``validate_version``
    (Task 12), which also owns the wire serialization.
    """

    blocking: list[Issue] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)

    def blocking_codes(self) -> list[str]:
        """The distinct blocking codes, sorted — what an activation reports."""
        return sorted({issue.code for issue in self.blocking})

    def warning_codes(self) -> list[str]:
        """The distinct warning codes, sorted."""
        return sorted({issue.code for issue in self.warnings})


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


def validate_locale_projection(version: AtlasVersion) -> list[Issue]:
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

    Resolution goes through :func:`apps.atlas.canonical.resolve_canonical_pair`, the
    exact-locale, publish-gated resolver: a node whose every locale is covered by
    overrides costs no query at all, and a node of a family outside the registry
    (``none``) is never handed to the resolver, which raises ``KeyError`` for it by
    design. A pair resolution that trips over an ambiguous locale is finished per
    locale instead, so an ambiguous locale masks only itself (ledger Task 10 review,
    F3): the ambiguity is the *reference* gate's finding, and another locale's gap is
    still reported here.
    """
    issues: list[Issue] = []
    nodes = version.nodes.filter(visible=True).prefetch_related("translations")
    for node in nodes:
        covered = {
            translation.locale
            for translation in node.translations.all()
            if translation.label_override.strip()
        }
        missing_locales = [locale for locale in DEFAULT_LOCALES if locale not in covered]
        if not missing_locales:
            continue
        resolutions: dict[str, CanonicalResolution | None] = {}
        ambiguous: set[str] = set()
        if node.canonical_model in CANONICAL_SOURCES and node.canonical_translation_key:
            try:
                resolutions = resolve_canonical_pair(
                    node.canonical_model,
                    node.canonical_translation_key,
                    locales=tuple(missing_locales),
                )
            except AmbiguousCanonicalRef as error:
                # The pair resolver abandons every locale it has not answered yet when
                # one of them is ambiguous, so abandoning the *node* would let that one
                # locale mask the others. Re-resolve them one by one: an ambiguous
                # locale can then only mask itself — it is a reference finding
                # (``validate_canonical_refs`` reports it as ``AMBIGUOUS_CANONICAL_REF``),
                # never this gate's missing projection.
                ambiguous = {error.locale}
                for locale in missing_locales:
                    if locale in ambiguous:
                        continue
                    try:
                        resolutions[locale] = resolve_canonical(
                            node.canonical_model, node.canonical_translation_key, locale
                        )
                    except AmbiguousCanonicalRef as locale_error:
                        ambiguous.add(locale_error.locale)
        for locale in missing_locales:
            if locale in ambiguous:
                continue
            if resolutions.get(locale) is None:
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


def validate_canonical_refs(version: AtlasVersion) -> list[Issue]:
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
    an unknown family makes the resolver raise ``KeyError`` by design.
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
        covered = {
            translation.locale
            for translation in node.translations.all()
            if translation.label_override.strip()
        }
        ambiguous = False
        unpublished = False
        for locale in DEFAULT_LOCALES:
            try:
                resolution = resolve_canonical(source, key, locale)
            except AmbiguousCanonicalRef:
                ambiguous = True
                continue
            if resolution is not None or locale in covered:
                continue
            if _canonical_row_exists(source, key, locale):
                unpublished = True
        if ambiguous:
            issues.append(_issue("AMBIGUOUS_CANONICAL_REF", node_key=node.public_key))
        if unpublished:
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
