"""Relation validation rules (spec §20.1) — plan Task 9.

One test per code this task implements, each asserting the exact code string and
that the issue names the offending composed relation key:

* ``RELATION_TYPE_INACTIVE`` — a relation uses a retired relation type;
* ``RELATION_TYPE_NOT_ALLOWED`` — a pair outside the type's allowed source/target
  types (empty means "any active type", spec §6.2);
* ``SELF_LOOP_FORBIDDEN`` — a self-loop the relation type forbids;
* ``DUPLICATE_RELATION`` — one composed key claimed twice: the same
  ``(source, type, target)`` authored twice, or an undirected relation plus its
  reversed pair;
* ``DANGLING_RELATION_ENDPOINT`` — an endpoint that is not a node of the version
  (either end, and a payload row with no key at all);
* ``DIRECTION_NOT_OVERRIDABLE`` — ``directed`` contradicts the type's
  ``directed_default`` while the type does not allow the override (the plan adds
  this code to the blocking set; the model deliberately never coerces ``directed``).

The offenders live in the ``atlas_v1`` fixture (``bad_relation`` for the pair
rule, ``direction_relation`` for the direction rule, ``relation_type`` for the
retirement rule); ``atlas_active_version`` is the control — a clean,
publishable-shaped mirror whose relation rules report nothing.

Fixtures are imported by name (``factories.py`` is not a conftest), so ``__all__``
marks them as used-by-design: pyflakes cannot see a test parameter as a use of a
module-level binding.
"""

import pytest

from apps.atlas.models import AtlasRelation, AtlasRelationType
from apps.atlas.tests.factories import (
    _node,
    _relation,
    _relation_type,
    _version,
    atlas_active_version,
    atlas_v1,
)
from apps.atlas.validation import (
    ATLAS_ISSUE_CODES,
    BLOCKING_CODES,
    WARNING_CODES,
    AllowedTypes,
    relation_rule_issues,
    validate_relations,
)

__all__ = ["atlas_active_version", "atlas_v1"]

pytestmark = pytest.mark.django_db

#: ``messageToken`` per code (the plan's ``atlas.relationTypeNotAllowed`` shape).
#: Extended additively: Task 9's six codes here, Task 10's eight below, Task 12's
#: four more — this table has to cover every blocking code, so each task that adds
#: one extends it (a shared file is extended, never narrowed).
MESSAGE_TOKENS = {
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
    "INVALID_PIN": "atlas.invalidPin",
    "MISSING_LAYOUT": "atlas.missingLayout",
    "MISSING_LOCALE_PROJECTION": "atlas.missingLocaleProjection",
    "NODE_TYPE_INACTIVE": "atlas.nodeTypeInactive",
    "PAYLOAD_CONTRACT_INVALID": "atlas.payloadContractInvalid",
    "RELATION_TYPE_INACTIVE": "atlas.relationTypeInactive",
    "RELATION_TYPE_NOT_ALLOWED": "atlas.relationTypeNotAllowed",
    "SELF_LOOP_FORBIDDEN": "atlas.selfLoopForbidden",
}


def issue_codes(issues):
    """``{code: [relation key, …]}`` — the shape the plan's Task 9 snippets assert."""
    grouped: dict[str, list[str]] = {}
    for issue in issues:
        grouped.setdefault(issue.code, []).append(issue.relation_key)
    return grouped


def test_allowed_pair_violation_is_blocking(atlas_v1):
    issue = issue_codes(validate_relations(atlas_v1.version))
    assert "RELATION_TYPE_NOT_ALLOWED" in issue
    assert issue["RELATION_TYPE_NOT_ALLOWED"] == [atlas_v1.bad_relation.public_key]
    assert "RELATION_TYPE_NOT_ALLOWED" in BLOCKING_CODES


def test_an_allowed_pair_is_not_reported(atlas_active_version):
    """The control: the same three ``research-focus`` edges, all inside the allowed pair."""
    assert validate_relations(atlas_active_version.version) == []


def test_inactive_relation_type_blocks_publish(atlas_v1):
    AtlasRelationType.objects.filter(pk=atlas_v1.relation_type.pk).update(active=False)
    grouped = issue_codes(validate_relations(atlas_v1.version))
    assert "RELATION_TYPE_INACTIVE" in grouped
    assert set(grouped["RELATION_TYPE_INACTIVE"]) == {
        relation.public_key
        for relation in atlas_v1.version.relations.filter(relation_type=atlas_v1.relation_type)
    }


def test_directed_override_is_only_allowed_when_the_type_permits_it(atlas_v1):
    grouped = issue_codes(validate_relations(atlas_v1.version))
    assert grouped["DIRECTION_NOT_OVERRIDABLE"] == [atlas_v1.direction_relation.public_key]
    assert "DIRECTION_NOT_OVERRIDABLE" in BLOCKING_CODES

    AtlasRelationType.objects.filter(pk=atlas_v1.direction_relation.relation_type_id).update(
        overridable_direction=True
    )
    assert "DIRECTION_NOT_OVERRIDABLE" not in issue_codes(validate_relations(atlas_v1.version))


def test_self_loop_forbidden_by_the_relation_type(atlas_v1):
    node = atlas_v1.areas[0]
    loop = _relation(
        source=node, target=node, relation_type=atlas_v1.relation_type, version=atlas_v1.version
    )
    grouped = issue_codes(validate_relations(atlas_v1.version))
    assert grouped["SELF_LOOP_FORBIDDEN"] == [loop.public_key]

    allowing = _relation_type(key="circles-back", self_loop_policy="allow")
    allowed_loop = _relation(
        source=node, target=node, relation_type=allowing, version=atlas_v1.version
    )
    assert allowed_loop.public_key not in issue_codes(validate_relations(atlas_v1.version)).get(
        "SELF_LOOP_FORBIDDEN", []
    )


def test_an_undirected_relation_duplicating_its_reversed_pair_is_flagged(atlas_v1):
    first, second = _node(version=atlas_v1.version), _node(version=atlas_v1.version)
    undirected = _relation_type(key="mirrors", directed_default=False)
    forward = _relation(
        source=first,
        target=second,
        relation_type=undirected,
        version=atlas_v1.version,
        directed=False,
    )
    reverse = _relation(
        source=second,
        target=first,
        relation_type=undirected,
        version=atlas_v1.version,
        directed=False,
    )
    assert forward.public_key == reverse.public_key, "an undirected key orders its endpoints"

    grouped = issue_codes(validate_relations(atlas_v1.version))
    assert grouped["DUPLICATE_RELATION"] == [forward.public_key]


@pytest.mark.parametrize(
    "ascending",
    [True, False],
    ids=["undirected-authored-ascending", "undirected-authored-descending"],
)
def test_a_reversed_directed_row_duplicates_the_undirected_one_either_way_round(
    atlas_v1, ascending
):
    """Spec §20.1's "duplicates its reversed pair", read with §5.3's sorted key.

    One undirected row and one directed row of the same type between the same two
    nodes compose the same edge in two spellings: the undirected key orders its
    ends (``A~t~B``), the directed key keeps the authored order (``B~t~A``). Which
    end the undirected row was authored from is not part of the graph, so it must
    not change the verdict.

    The state is reachable through the ORM: ``AtlasRelation.clean``'s mirror guard
    fires for **undirected** rows only, so the directed row passes ``full_clean()``
    (asserted below), ``create()`` never calls ``clean()``, and the unique
    constraint covers one authored triple rather than the pair.

    Every composed key the two colliding rows claim is reported, once each — in the
    descending case both rows compose the same string (so one issue), in the
    ascending case the two spellings differ (so two).
    """
    low = _node(version=atlas_v1.version, public_key="research-area-00000001")
    high = _node(version=atlas_v1.version, public_key="research-area-00000002")
    undirected_type = _relation_type(
        key="mirrors", directed_default=False, overridable_direction=True
    )
    undirected = _relation(
        source=low if ascending else high,
        target=high if ascending else low,
        relation_type=undirected_type,
        version=atlas_v1.version,
        directed=False,
    )
    # The directed row reverses the stored triple — the only orientation the unique
    # constraint leaves for a second row between these two nodes.
    directed = _relation(
        source=high if ascending else low,
        target=low if ascending else high,
        relation_type=undirected_type,
        version=atlas_v1.version,
        directed=True,
    )
    directed.full_clean()

    assert undirected.public_key == f"{low.public_key}~mirrors~{high.public_key}"
    grouped = issue_codes(validate_relations(atlas_v1.version))
    assert grouped["DUPLICATE_RELATION"] == sorted({undirected.public_key, directed.public_key})


def test_two_directed_reversals_are_not_duplicates(atlas_v1):
    """The other half of the identity rule: a directed pair stays legal.

    Only an **undirected** row answers for both endpoint orders, because only its
    key is sorted (spec §5.3). Two directed rows between the same two nodes compose
    two different keys and are two different edges; claiming the reversed identity
    for a directed row as well would report every antiparallel directed pair —
    the shape the Task 10 hierarchy fixture builds on purpose — as a duplicate.
    """
    low = _node(version=atlas_v1.version, public_key="research-area-00000001")
    high = _node(version=atlas_v1.version, public_key="research-area-00000002")
    directed_type = _relation_type(key="points-at", directed_default=True)
    forward = _relation(
        source=low, target=high, relation_type=directed_type, version=atlas_v1.version
    )
    backward = _relation(
        source=high, target=low, relation_type=directed_type, version=atlas_v1.version
    )
    assert forward.public_key != backward.public_key, "a directed key keeps the authored order"

    assert "DUPLICATE_RELATION" not in issue_codes(validate_relations(atlas_v1.version))


def test_the_same_triple_twice_is_flagged_on_the_rule_layer(atlas_v1):
    """The exact-triple case the DB constraint makes unreachable through the ORM.

    ``atlas_relation_unique_version_pair_rel`` refuses a second stored row for one
    ``(version, source, target, relation_type)``, so the rule is proven here with
    two detached rows — the shape a payload-level validation (Plan B's bulk PUT)
    hands to the pure layer.
    """
    relation_type = atlas_v1.relation_type
    fields = {
        "version": atlas_v1.version,
        "source": atlas_v1.areas[0],
        "target": atlas_v1.areas[1],
        "relation_type": relation_type,
    }
    first, second = AtlasRelation(**fields), AtlasRelation(**fields)

    issues = relation_rule_issues(
        [first, second],
        version_id=atlas_v1.version.pk,
        allowed_types={relation_type.pk: AllowedTypes()},
    )
    assert issue_codes(issues)["DUPLICATE_RELATION"] == [first.public_key]


def test_a_keyless_detached_row_is_dangling(atlas_v1):
    """A row with no composed key is reported unnamed — never read, never a crash.

    Plan B's bulk PUT validates a payload graph before writing it, so the rule layer
    can be handed rows whose endpoints do not exist yet: a relation row with no
    ``source``/``target``/``relation_type`` has no composed key at all
    (``public_key`` raises on it — asserted below), and the honest report is
    ``DANGLING_RELATION_ENDPOINT`` with no relation key to name, because the payload
    references nothing this version holds. A stored row's three foreign keys are
    non-null, so this is the payload path's shape only.
    """
    bare = AtlasRelation()
    type_only = AtlasRelation(relation_type=atlas_v1.relation_type)
    endpoints_only = AtlasRelation(source=atlas_v1.identity, target=atlas_v1.identity)

    assert (bare.source_id, bare.target_id, bare.relation_type_id) == (None, None, None)
    with pytest.raises(AttributeError):
        _ = bare.public_key  # no endpoints to compose a key from

    issues = relation_rule_issues(
        [bare, type_only, endpoints_only], version_id=atlas_v1.version.pk, allowed_types={}
    )
    assert issue_codes(issues) == {"DANGLING_RELATION_ENDPOINT": [None, None, None]}
    for issue in issues:
        assert issue.node_key is None and issue.group_key is None
        assert issue.message_token == MESSAGE_TOKENS["DANGLING_RELATION_ENDPOINT"]


def test_an_endpoint_outside_the_version_is_dangling(atlas_v1):
    elsewhere = _version(status="draft", label="atlas-v1-elsewhere")
    foreign = _node(version=elsewhere)
    relation = _relation(
        source=foreign,
        target=atlas_v1.identity,
        relation_type=_relation_type(key="connects"),
        version=atlas_v1.version,
    )

    grouped = issue_codes(validate_relations(atlas_v1.version))
    assert grouped["DANGLING_RELATION_ENDPOINT"] == [relation.public_key]
    assert relation.public_key in {
        other.public_key for other in atlas_v1.version.relations.all()
    }


def test_a_foreign_target_is_dangling_either_way_round(atlas_v1):
    """Both clauses of the endpoint rule, each with its own foreign endpoint.

    The rule is one ``or`` over the row's two endpoints. The foreign-*source* case
    above cannot tell a mutation that drops the target clause
    (``relation.target.version_id != version_id``) from the real rule — the source
    half keeps reporting — so this test adds the missing direction, and a local
    control next to both foreign rows so "dangling" cannot degenerate into "any
    relation of the version".
    """
    elsewhere = _version(status="draft", label="atlas-v1-elsewhere")
    foreign = _node(version=elsewhere)
    relation_type = _relation_type(key="connects")
    foreign_source = _relation(
        source=foreign,
        target=atlas_v1.identity,
        relation_type=relation_type,
        version=atlas_v1.version,
    )
    foreign_target = _relation(
        source=atlas_v1.identity,
        target=foreign,
        relation_type=relation_type,
        version=atlas_v1.version,
    )
    both_local = _relation(
        source=atlas_v1.identity,
        target=atlas_v1.areas[0],
        relation_type=relation_type,
        version=atlas_v1.version,
    )

    grouped = issue_codes(validate_relations(atlas_v1.version))
    assert grouped["DANGLING_RELATION_ENDPOINT"] == sorted(
        {foreign_source.public_key, foreign_target.public_key}
    )
    assert both_local.public_key not in grouped["DANGLING_RELATION_ENDPOINT"]


def test_an_invisible_relation_is_still_judged(atlas_v1):
    """The rule texts of spec §20.1 are not visibility-conditional, so retirement,
    pair, loop, duplicate and endpoint defects are reported on a hidden relation too
    — hiding a row must not silence the gate that would reopen on the next unhide.
    """
    AtlasRelation.objects.filter(pk=atlas_v1.bad_relation.pk).update(visible=False)
    grouped = issue_codes(validate_relations(atlas_v1.version))
    assert grouped["RELATION_TYPE_NOT_ALLOWED"] == [atlas_v1.bad_relation.public_key]


def test_every_issue_names_its_relation_and_carries_a_message_token(atlas_v1):
    issues = validate_relations(atlas_v1.version)
    assert {issue.code for issue in issues} == {
        "RELATION_TYPE_NOT_ALLOWED",
        "DIRECTION_NOT_OVERRIDABLE",
    }
    keys = {relation.public_key for relation in atlas_v1.version.relations.all()}
    for issue in issues:
        assert issue.code in ATLAS_ISSUE_CODES
        assert issue.code in BLOCKING_CODES and issue.code not in WARNING_CODES
        assert issue.relation_key in keys
        assert issue.node_key is None and issue.group_key is None
        assert issue.message_token == MESSAGE_TOKENS[issue.code]


def test_the_declared_code_sets_are_consistent():
    assert set(ATLAS_ISSUE_CODES) == set(BLOCKING_CODES) | set(WARNING_CODES)
    assert not set(BLOCKING_CODES) & set(WARNING_CODES)
    assert set(BLOCKING_CODES) == set(MESSAGE_TOKENS)
    # Task 9 implemented the relation rules, Task 10 the node/locale/taxonomy
    # codes; Task 12 froze the full vocabulary against §20.1 + §20.2 +
    # ``DIRECTION_NOT_OVERRIDABLE`` (ledger row ``9-cf``) and added the warning
    # vocabulary, so this file's own empty-tuple pin is now the frozen table.
    assert WARNING_CODES == (
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


def test_the_rule_output_is_stable_across_runs(atlas_v1):
    first = validate_relations(atlas_v1.version)
    assert first == validate_relations(atlas_v1.version)
    assert first == sorted(first, key=lambda issue: (issue.code, issue.relation_key or ""))


def test_the_rule_loop_does_not_query_per_relation(atlas_v1, django_assert_num_queries):
    """One query for the version's relations and two for the allowed-pair tables.

    Adding five relations (ten nodes) must not move the count: a per-relation
    ``allowed_source_types``/``allowed_target_types`` access is exactly the shape
    the plan's purity constraint forbids.
    """
    with django_assert_num_queries(3):
        validate_relations(atlas_v1.version)

    for _ in range(5):
        _relation(version=atlas_v1.version)
    assert atlas_v1.version.relations.count() == 12
    with django_assert_num_queries(3):
        validate_relations(atlas_v1.version)
