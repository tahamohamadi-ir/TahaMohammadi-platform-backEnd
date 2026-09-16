"""Publish-gate validation (spec §20) — relation rules first (plan Task 9).

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

Scope decisions of this task, recorded because the plan leaves them open:

* **every relation of the version is judged**, ``visible`` or not: the rule texts
  of spec §20.1 are not visibility-conditional, and a hidden row that is invalid
  would pass the gate silently and reopen on the next unhide. Visibility-scoped
  rules (``DANGLING_NODE_HIDDEN_RELATION``) belong to Task 10's node/locale pass;
* ``directed`` is **judged, never coerced**: spec §5.3's "initialised from
  ``directed_default``" rule is the authoring layer's job, and a relation that
  contradicts its type while ``overridable_direction`` is false is exactly
  ``DIRECTION_NOT_OVERRIDABLE`` (the plan adds that code to the blocking set);
* duplicates are reported **once per duplicated composed key** — the key is the
  wire identity, and the two halves of an undirected mirrored pair compose the
  same key, so "this key is claimed twice" is the honest message and the one the
  admin can act on.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from apps.atlas.models import (
    SELF_LOOP_POLICIES,
    AtlasRelation,
    AtlasRelationType,
    AtlasVersion,
)

#: Codes whose violation blocks publication (spec §20.1, plus the plan's
#: ``DIRECTION_NOT_OVERRIDABLE``). Ordered as the spec's table lists them, with the
#: plan-added code last; the order is not semantic. Task 12 freezes the complete
#: vocabulary against spec §20.1 + §20.2 and re-pins this tuple.
BLOCKING_CODES: tuple[str, ...] = (
    "DANGLING_RELATION_ENDPOINT",
    "RELATION_TYPE_INACTIVE",
    "RELATION_TYPE_NOT_ALLOWED",
    "SELF_LOOP_FORBIDDEN",
    "DUPLICATE_RELATION",
    "DIRECTION_NOT_OVERRIDABLE",
)

#: Warnings are never blocking (spec §20.2). Task 9 implements none: the warning
#: vocabulary arrives with Tasks 10 and 12.
WARNING_CODES: tuple[str, ...] = ()

#: The whole implemented vocabulary — every code this module may emit.
ATLAS_ISSUE_CODES: tuple[str, ...] = BLOCKING_CODES + WARNING_CODES

#: ``messageToken`` per code: one token per code, ``atlas.*`` namespace, mirroring
#: the AB-06 validator (``graph.*``). The admin renders the token, never the code.
_MESSAGE_TOKENS: dict[str, str] = {
    "DANGLING_RELATION_ENDPOINT": "atlas.danglingRelationEndpoint",
    "DIRECTION_NOT_OVERRIDABLE": "atlas.directionNotOverridable",
    "DUPLICATE_RELATION": "atlas.duplicateRelation",
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


def _issue(code: str, *, relation_key: str | None = None) -> Issue:
    """Build an issue with this module's ``messageToken`` for ``code``."""
    return Issue(code=code, relation_key=relation_key, message_token=_MESSAGE_TOKENS[code])


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
    answers to the same triple with its endpoints ordered, because that reversed
    pair composes the same public key (``relation_public_key`` sorts the ends of
    an undirected key) — the mirror rule of ``AtlasRelation.clean``.
    """
    source, target = relation.source.public_key, relation.target.public_key
    identities = {(source, relation.relation_type.key, target)}
    if not relation.directed:
        identities.add((min(source, target), relation.relation_type.key, max(source, target)))
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
