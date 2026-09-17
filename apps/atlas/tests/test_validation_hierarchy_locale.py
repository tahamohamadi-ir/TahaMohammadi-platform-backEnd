"""Hierarchy DAG, locale parity and taxonomy lifecycle validation — plan Task 10.

The five rule groups of spec §20.1 this task adds, each with the fixture state
that violates it:

* ``HIERARCHY_CYCLE`` — the hierarchy-role subgraph of the *visible* graph must be
  a DAG (spec §5.6). Two parents for one child are legal and no code exists for
  them; a cycle outside the hierarchy subgraph is legal too. Fixture: ``atlas_dag``.
* ``MISSING_LOCALE_PROJECTION`` — the locale-parity gate, blocking in **both**
  directions: every visible node resolves EN *and* FA through that locale's own
  canonical row or a non-blank per-locale override, never by falling back to the
  other locale (spec §20.1 note; plan Phase 0, correction 1). Fixture: ``atlas_v1``,
  whose two one-sided nodes are one direction each.
* ``DANGLING_NODE_HIDDEN_RELATION`` — a visible relation referencing an invisible node.
* ``GROUP_LOCALE_MISSING`` — a group without a non-blank label for both locales.
* ``NODE_TYPE_INACTIVE``, ``CANONICAL_SOURCE_MISSING``,
  ``CANONICAL_SOURCE_UNPUBLISHED``, ``AMBIGUOUS_CANONICAL_REF`` — taxonomy lifecycle
  and the canonical reference itself.

Four things the file does deliberately, all of them traced to the plan or the
ledger (each is spelled out again where it is used):

* the plan's Task 10 snippets call ``validate_version``, which **Task 12** produces
  (the aggregator, the blocking/warning split and the wire shape are its scope).
  This file composes the same groups locally with :func:`blocking_report` and
  asserts the same intents, so no part of Task 12 is pre-empted;
* ``test_invisible_nodes_and_relations_do_not_participate`` hides **both**
  one-sided nodes. The plan hides one; the other one then still fails its own
  parity check and the snippet's first assertion could never hold (ledger row
  ``8-h``, proven there);
* no assertion pins the fixture's *overall* blocker set: ``atlas_v1`` carries Task
  9's relation offenders (``bad_relation``, ``direction_relation``), so every
  parity assertion filters to ``MISSING_LOCALE_PROJECTION`` first (as the plan's
  Phase-0 parity-matrix test does) instead of expecting exactly two entries;
* the F1/R11 block builds each of its two hierarchy shapes **twice, with the rows
  inserted in both orders**, and compares the two verdicts of one logical graph.
  That is ruling R11's falsification form, not a ``public_key``-order assertion on
  the report: the reviewed walk's verdict followed relation *row order* for exactly
  those shapes (ledger Task 10 review, F1). ``part-of`` is an admin-created
  undirected hierarchy-role type — the seeded §6.2 vocabulary cannot produce either
  shape.

Fixtures are imported by name (``factories.py`` is not a conftest), so ``__all__``
marks the fixture parameters as used-by-design: pyflakes cannot see a test
parameter as a use of a module-level binding.
"""

from uuid import uuid4

import pytest

from apps.atlas.models import (
    AtlasNode,
    AtlasNodeTranslation,
    AtlasNodeType,
    AtlasRelation,
)
from apps.atlas.tests.factories import (
    DAG_KEYS,
    DAG_NODE_NAMES,
    PUBLISHED_AT,
    _dag_edges,
    _dag_nodes,
    _group,
    _group_translation,
    _node,
    _node_type,
    _relation,
    _relation_type,
    _version,
    atlas_active_version,
    atlas_dag,
    atlas_v1,
)
from apps.atlas.validation import (
    ATLAS_ISSUE_CODES,
    BLOCKING_CODES,
    WARNING_CODES,
    ValidationReport,
    validate_canonical_refs,
    validate_hierarchy,
    validate_locale_projection,
    validate_relations,
    validate_taxonomy,
    validate_visibility,
)
from apps.content.models import Publication, ResearchTopic

__all__ = ["atlas_active_version", "atlas_dag", "atlas_v1"]

pytestmark = pytest.mark.django_db


def blocking_report(version) -> ValidationReport:
    """Every blocking rule group of one version, as one report.

    ``validate_version`` is Task 12's aggregator; composing the groups here keeps
    this task's snippets' intent asserted without pre-empting it. Each group emits
    blocking issues only (spec §20.1), so the report carries them in ``blocking``.
    """
    return ValidationReport(
        blocking=[
            *validate_relations(version),
            *validate_hierarchy(version),
            *validate_visibility(version),
            *validate_locale_projection(version),
            *validate_taxonomy(version),
            *validate_canonical_refs(version),
        ]
    )


def codes(issues) -> set[str]:
    """The distinct codes ``issues`` reports."""
    return {issue.code for issue in issues}


def codes_for_node(issues, node) -> set[str]:
    """The codes ``issues`` reports for exactly ``node``."""
    return {issue.code for issue in issues if issue.node_key == node.public_key}


def parity_keys(version) -> set[str]:
    """The node keys ``MISSING_LOCALE_PROJECTION`` names for ``version``."""
    return {
        issue.node_key
        for issue in validate_locale_projection(version)
        if issue.code == "MISSING_LOCALE_PROJECTION"
    }


def group_copy_keys(version) -> set[str]:
    """The group keys ``GROUP_LOCALE_MISSING`` names for ``version``."""
    return {
        issue.group_key
        for issue in validate_locale_projection(version)
        if issue.code == "GROUP_LOCALE_MISSING"
    }


def expected_token(code: str) -> str:
    """``HIERARCHY_CYCLE`` → ``atlas.hierarchyCycle`` — the token a code spells."""
    head, *rest = code.lower().split("_")
    return "atlas." + head + "".join(part.capitalize() for part in rest)


# ---------------------------------------------------------------------------
# Hierarchy: multi-parent is legal, the hierarchy-role subgraph is a DAG
# ---------------------------------------------------------------------------


def test_a_multi_parent_diamond_is_a_legal_hierarchy(atlas_dag):
    """``area-c`` has two hierarchy parents and nothing blocks.

    The plan's Step 1 reads ``atlas_dag.diamond.node("area-c").parents()``: a version
    has no ``node()`` accessor and ``AtlasNode`` has no ``parents()`` method (no task
    adds one — "render in both directions" is the frontend's job), so the fixture
    answers by short name and the assertions keep the intent: the whole report is
    clean, and the two parents are the two hierarchy edges.
    """
    report = blocking_report(atlas_dag.diamond)
    assert report.blocking_codes() == []

    parents = atlas_dag.parents("area-c")
    assert [node.public_key for node in parents] == [DAG_KEYS["area-a"], DAG_KEYS["area-b"]]
    # "Multiple parents … are permitted" (spec §5.6) is a *no-code* rule: no code
    # exists for it, and the clean report above is the proof it is not reported.
    assert "MULTIPLE_PARENTS_ALLOWED" not in ATLAS_ISSUE_CODES


def test_a_hierarchy_cycle_blocks_publish(atlas_dag):
    relation = atlas_dag.close_cycle()
    issues = validate_hierarchy(atlas_dag.diamond)

    assert [issue.code for issue in issues] == ["HIERARCHY_CYCLE"]
    # "Report the first node on every cycle" (plan Step 3) — the cycle is
    # area-a → area-c → area-a, and the node named is its first member in
    # public_key order: area-a (…0002) < area-c (…0004).
    assert issues[0].node_key == DAG_KEYS["area-a"]
    assert issues[0].message_token == "atlas.hierarchyCycle"
    assert "HIERARCHY_CYCLE" in BLOCKING_CODES and "HIERARCHY_CYCLE" not in WARNING_CODES

    assert relation.public_key == f"{DAG_KEYS['area-c']}~parent-of~{DAG_KEYS['area-a']}"
    assert blocking_report(atlas_dag.diamond).blocking_codes() == ["HIERARCHY_CYCLE"]
    assert validate_hierarchy(atlas_dag.diamond) == issues  # stable across runs


def test_a_general_graph_cycle_is_legal(atlas_dag):
    """Spec §5.6: a cycle outside the hierarchy subgraph is an ordinary relation."""
    added = atlas_dag.add_related_cycle()
    assert [relation.public_key for relation in added] == [
        f"{DAG_KEYS['area-a']}~relates-to~{DAG_KEYS['area-b']}",
        f"{DAG_KEYS['area-b']}~relates-to~{DAG_KEYS['area-a']}",
    ]

    report = blocking_report(atlas_dag.diamond)
    assert "HIERARCHY_CYCLE" not in report.blocking_codes()
    assert report.blocking_codes() == []


def test_the_cycle_report_follows_public_key_order_not_creation_order(atlas_dag):
    """The plan pins ``public_key`` iteration; insertion order must not decide it."""
    reversed_version = _version(status="draft", label="atlas-dag-reversed")
    nodes = _dag_nodes(
        reversed_version,
        node_type=atlas_dag.node_type,
        order=tuple(reversed(DAG_NODE_NAMES)),
    )
    _dag_edges(reversed_version, nodes, hierarchy_type=atlas_dag.hierarchy_type)
    _relation(
        source=nodes["area-c"],
        target=nodes["area-a"],
        relation_type=atlas_dag.hierarchy_type,
        version=reversed_version,
    )
    atlas_dag.close_cycle()

    assert [issue.node_key for issue in validate_hierarchy(reversed_version)] == [
        DAG_KEYS["area-a"]
    ]
    assert [issue.node_key for issue in validate_hierarchy(reversed_version)] == [
        issue.node_key for issue in validate_hierarchy(atlas_dag.version)
    ]


def test_a_single_undirected_hierarchy_edge_is_not_a_cycle(atlas_dag):
    """A cycle needs more than one edge: an undirected hierarchy pair is not one.

    The graph is built fresh — three nodes of the fixture's structural type, no
    directed edge to mix in — so the claim is about the undirected arcs alone, which
    a directed walk-back exclusion would otherwise misread as a two-node cycle.
    """
    part_of = _relation_type(
        "part-of", directed_default=False, overridable_direction=True, hierarchy_role=True
    )
    chain = _version(status="draft", label="atlas-dag-undirected")
    first, second, third = [
        _node(version=chain, node_type=atlas_dag.node_type, public_key=DAG_KEYS[name])
        for name in ("area", "area-a", "area-c")
    ]

    _relation(source=first, target=second, relation_type=part_of, version=chain)
    assert validate_hierarchy(chain) == []  # one undirected edge is not a cycle…

    _relation(source=second, target=third, relation_type=part_of, version=chain)
    assert validate_hierarchy(chain) == []  # …nor is a path of two…

    _relation(source=third, target=first, relation_type=part_of, version=chain)
    assert [issue.node_key for issue in validate_hierarchy(chain)] == [
        first.public_key
    ]  # …while the third edge closes one, reported at its first node in key order.


def test_a_hidden_hierarchy_edge_and_a_hidden_node_are_not_traversed(atlas_dag):
    """The subgraph is restricted to visible nodes *and* visible relations (§5.6)."""
    atlas_dag.close_cycle()
    AtlasRelation.objects.filter(version=atlas_dag.version).update(visible=False)
    assert validate_hierarchy(atlas_dag.version) == []

    AtlasRelation.objects.filter(version=atlas_dag.version).update(visible=True)
    AtlasNode.objects.filter(pk=atlas_dag.node("area-c").pk).update(visible=False)
    assert validate_hierarchy(atlas_dag.version) == []


# ---------------------------------------------------------------------------
# F1/R11 — the hierarchy verdict is a property of the graph, not of row order
# ---------------------------------------------------------------------------

#: The two shapes the reviewed walk reported **nothing** for (ledger Task 10 review,
#: F1 (a) and (b)), as ``(source, target, directed)`` rows over :data:`DAG_KEYS`
#: names. Each is built once per insertion order so a test can compare the verdicts
#: of one logical graph.
#:
#: Shape (a) authors its undirected row ``B → A`` and its directed row ``A → B``:
#: the two rows are the same *logical* edge set (an undirected edge has no
#: orientation), and the differing stored triple is what
#: ``atlas_relation_unique_version_pair_rel`` requires — a reachable authoring
#: state, not a contrived one (Task 9's review found the same pair reachable).
F1_SHAPES: dict[str, tuple[tuple[str, str, bool], ...]] = {
    "undirected-pair-with-directed-parallel": (
        ("area-b", "area-a", False),
        ("area-a", "area-b", True),
    ),
    "directed-arc-into-undirected-path": (
        ("area-a", "area-c", True),
        ("area-c", "area-b", False),
        ("area-b", "area-a", False),
    ),
}


def undirected_hierarchy_type():
    """An admin-created undirected ``hierarchy_role`` type — the seeded set has none.

    ``overridable_direction`` is on, so a row of this type can be authored directed
    without tripping Task 9's ``DIRECTION_NOT_OVERRIDABLE``, and both pair tables stay
    empty ("any active type", spec §6.2). Call once per test: the key is unique.
    """
    return _relation_type(
        "part-of", directed_default=False, overridable_direction=True, hierarchy_role=True
    )


def hierarchy_probe(atlas_dag, label, *, relation_type, edges):
    """One fresh version with the four fixed-key nodes and ``edges`` as its rows.

    Only the rows' *order* changes between the forward and the permuted build of one
    shape, so the two verdicts a test compares can differ in nothing else.
    """
    version = _version(status="draft", label=label)
    nodes = {
        name: _node(version=version, node_type=atlas_dag.node_type, public_key=DAG_KEYS[name])
        for name in DAG_NODE_NAMES
    }
    for source, target, directed in edges:
        _relation(
            source=nodes[source],
            target=nodes[target],
            relation_type=relation_type,
            version=version,
            directed=directed,
        )
    return version


def hierarchy_verdict(version) -> list[tuple[str, str | None]]:
    """The hierarchy verdict as ``(code, node key)`` pairs — what a test compares."""
    return [(issue.code, issue.node_key) for issue in validate_hierarchy(version)]


@pytest.mark.parametrize("edges", list(F1_SHAPES.values()), ids=list(F1_SHAPES))
def test_a_cycle_closed_through_an_undirected_row_is_reported(atlas_dag, edges):
    """F1 (a) and (b): a cycle the reviewed walk returned no issue for.

    (a) An undirected ``area-a—area-b`` with a directed ``area-a → area-b`` parallel
    to it: the undirected row contributes *both* directions (the model this module
    documents), so ``A → B → A`` is a closed walk of two distinct relations — a cycle
    — while the walk out and back along the single undirected row alone would not be
    one. The reviewed walk found the walk-back only and reported nothing.
    (b) A directed ``area-a → area-c`` running into an undirected ``area-c—area-b—area-a``
    path: ``A → C → B → A`` closes the same way, three distinct relations.

    Both shapes are reachable through an admin-created undirected hierarchy-role
    type, which the seeded §6.2 vocabulary cannot produce — so this file builds it —
    and both are reported at the cycle's first node in ``public_key`` order.
    """
    version = hierarchy_probe(
        atlas_dag, "atlas-f1-shape", relation_type=undirected_hierarchy_type(), edges=edges
    )

    assert hierarchy_verdict(version) == [("HIERARCHY_CYCLE", DAG_KEYS["area-a"])]


def test_an_undirected_row_is_walkable_against_its_authored_order(atlas_dag):
    """An undirected edge has no orientation: a cycle walking it backwards counts.

    The three rows form one triangle, and the closed walk it must be recognised on
    takes its last step *against* the ends that step was authored from (the ``area-a``
    row stored ``area-a → area-c`` is walked ``area-c → area-a``). A rule reading each
    row's stored direction alone would call this graph a DAG — this test is the guard
    for the model's "both directions" half, because none of the F1 shapes needs it.
    """
    version = hierarchy_probe(
        atlas_dag,
        "atlas-undirected-reversed",
        relation_type=undirected_hierarchy_type(),
        edges=(
            ("area-a", "area", False),
            ("area-a", "area-c", False),
            ("area", "area-c", False),
        ),
    )

    assert hierarchy_verdict(version) == [("HIERARCHY_CYCLE", DAG_KEYS["area"])]


@pytest.mark.parametrize("edges", list(F1_SHAPES.values()), ids=list(F1_SHAPES))
def test_the_hierarchy_verdict_does_not_follow_relation_row_order(atlas_dag, edges):
    """Ruling R11: one logical graph, rows inserted in both orders, one verdict.

    Shape (a) failed this on the reviewed walk: the undirected row authored first
    produced ``[]`` and the directed row authored first produced ``[area-a]`` — the
    *row* order decided a publish gate. A determinism claim is falsified by this
    permutation, never by an order assertion on the report, so the same graph is
    built twice and the two verdicts are compared — and the shared verdict cannot be
    the empty one, because the graph has a cycle.
    """
    hierarchy_type = undirected_hierarchy_type()
    forward = hierarchy_probe(
        atlas_dag, "atlas-r11-forward", relation_type=hierarchy_type, edges=edges
    )
    permuted = hierarchy_probe(
        atlas_dag,
        "atlas-r11-permuted",
        relation_type=hierarchy_type,
        edges=tuple(reversed(edges)),
    )

    assert hierarchy_verdict(permuted) == hierarchy_verdict(forward)
    assert hierarchy_verdict(forward) == [("HIERARCHY_CYCLE", DAG_KEYS["area-a"])]


def test_a_cycle_is_reported_at_its_first_node_in_public_key_order(atlas_dag):
    """F2: the node named is the cycle's **first** node in ``public_key`` order.

    The walk that closes this cycle enters it through ``area-b`` (…0003) from
    ``area`` and returns to it, but the cycle is ``area-a (…0002) ↔ area-b (…0003)``
    and the finding names ``area-a``. "The DFS enters the cycle at its first member"
    held for the fixture's diamond and was overstated in general — exactly the claim
    the Task 10 review flagged (the node a walk returns to is not the first node).
    """
    version = hierarchy_probe(
        atlas_dag,
        "atlas-f2-first-node",
        relation_type=atlas_dag.hierarchy_type,
        edges=(
            ("area", "area-b", True),  # the outside arc: the cycle is entered at …0003
            ("area-b", "area-a", True),
            ("area-a", "area-b", True),
        ),
    )

    assert hierarchy_verdict(version) == [("HIERARCHY_CYCLE", DAG_KEYS["area-a"])]


# ---------------------------------------------------------------------------
# Locale parity: blocking in both directions (spec §20.1 note)
# ---------------------------------------------------------------------------


def test_missing_fa_projection_blocks_and_an_override_clears_it(atlas_v1):
    """The FA direction, with the assertion scoped to the node the override is about.

    The plan asserts ``MISSING_LOCALE_PROJECTION`` is gone from the whole version
    once ``fa_missing_node`` is overridden; that cannot hold while
    ``en_missing_node``'s EN gap is open — the same defect class as ledger row
    ``8-h`` — so the second assertion names the node.
    """
    fa_missing, en_missing = atlas_v1.fa_missing_node, atlas_v1.en_missing_node
    assert "MISSING_LOCALE_PROJECTION" in blocking_report(atlas_v1.version).blocking_codes()
    assert parity_keys(atlas_v1.version) == {fa_missing.public_key, en_missing.public_key}

    # The plan's own line: a non-blank per-locale override covers the missing locale.
    AtlasNodeTranslation.objects.create(node=fa_missing, locale="fa", label_override="برچسب")

    assert parity_keys(atlas_v1.version) == {en_missing.public_key}


def test_parity_gate_covers_both_directions_and_passes_when_both_locales_resolve(atlas_v1):
    """The §20.1 gate proof (plan Phase 0, correction 1): EN ✗ and FA ✗ both block."""
    report = blocking_report(atlas_v1.version)
    parity = {
        issue.node_key
        for issue in report.blocking
        if issue.code == "MISSING_LOCALE_PROJECTION"
    }
    assert parity == {atlas_v1.en_missing_node.public_key, atlas_v1.fa_missing_node.public_key}
    assert "MISSING_LOCALE_PROJECTION" in BLOCKING_CODES
    assert "MISSING_LOCALE_PROJECTION" not in WARNING_CODES

    # Overriding the locale that already resolves is not a fallback in disguise:
    # the missing locale stays missing.
    atlas_v1.override(atlas_v1.fa_missing_node, locale="en", label="Label")
    atlas_v1.override(atlas_v1.en_missing_node, locale="fa", label="برچسب")
    assert parity_keys(atlas_v1.version) == {
        atlas_v1.en_missing_node.public_key,
        atlas_v1.fa_missing_node.public_key,
    }

    # The plan's snippet overrides each node in the locale that already resolved;
    # the per-locale override has to cover the *missing* locale instead (Step 3:
    # "label_override non-blank ⇒ resolvable without a canonical row").
    atlas_v1.override(atlas_v1.en_missing_node, locale="en", label="Label")
    atlas_v1.override(atlas_v1.fa_missing_node, locale="fa", label="برچسب")

    assert parity_keys(atlas_v1.version) == set()
    assert "MISSING_LOCALE_PROJECTION" not in blocking_report(atlas_v1.version).blocking_codes()


def test_a_clean_mirror_reports_no_parity_issue(atlas_active_version):
    """The control: a servable mirror (no one-sided nodes) passes the gate."""
    assert parity_keys(atlas_active_version.version) == set()
    assert validate_locale_projection(atlas_active_version.version) == []


def test_invisible_nodes_and_relations_do_not_participate(atlas_v1):
    """A hidden node is not parity-gated — and its visible relations go dangling.

    Ledger row ``8-h``: the plan hides one one-sided node, which leaves the *other*
    one blocking its own parity check, so its first assertion could never hold; both
    are hidden here. The visibility rule itself is what the test proves: a hidden
    node is neither published nor traversed, so its missing projection is not an
    issue — while the two visible relations that still point at it are.
    """
    hidden = [atlas_v1.fa_missing_node, atlas_v1.en_missing_node]
    AtlasNode.objects.filter(pk__in=[node.pk for node in hidden]).update(visible=False)
    report = blocking_report(atlas_v1.version)

    assert "MISSING_LOCALE_PROJECTION" not in report.blocking_codes()
    # …and hiding them does not turn their unpublished canonical halves into a
    # reference blocker either: the canonical rules are scoped to the projection.
    assert "CANONICAL_SOURCE_UNPUBLISHED" not in report.blocking_codes()
    assert "DANGLING_NODE_HIDDEN_RELATION" in report.blocking_codes()

    dangling = {
        issue.relation_key
        for issue in report.blocking
        if issue.code == "DANGLING_NODE_HIDDEN_RELATION"
    }
    assert dangling == {
        relation.public_key
        for relation in atlas_v1.version.relations.filter(
            visible=True, target_id__in=[node.pk for node in hidden]
        )
    }
    assert len(dangling) == 2


def test_a_hidden_relation_to_a_hidden_node_is_not_reported(atlas_v1):
    """The dangling rule is about *visible* relations (spec §20.1's wording)."""
    node = atlas_v1.fa_missing_node
    AtlasNode.objects.filter(pk=node.pk).update(visible=False)
    assert codes(validate_visibility(atlas_v1.version)) == {"DANGLING_NODE_HIDDEN_RELATION"}

    AtlasRelation.objects.filter(target=node).update(visible=False)
    assert validate_visibility(atlas_v1.version) == []


# ---------------------------------------------------------------------------
# Group copy (spec §5.5) and taxonomy lifecycle (spec §20.1)
# ---------------------------------------------------------------------------


def test_a_group_without_both_locale_labels_blocks_publish(atlas_v1):
    untranslated = _group(version=atlas_v1.version)
    bilingual = _group(version=atlas_v1.version)
    assert group_copy_keys(atlas_v1.version) == {untranslated.public_key, bilingual.public_key}

    _group_translation(bilingual, locale="en", label="Group EN")
    assert bilingual.public_key in group_copy_keys(atlas_v1.version)  # FA still missing

    _group_translation(bilingual, locale="fa", label="گروه")
    assert group_copy_keys(atlas_v1.version) == {untranslated.public_key}

    # A blank label is not copy: the row exists, the localization does not.
    blank = _group(version=atlas_v1.version)
    _group_translation(blank, locale="en", label="   ")
    _group_translation(blank, locale="fa", label="")
    assert blank.public_key in group_copy_keys(atlas_v1.version)
    assert "GROUP_LOCALE_MISSING" in BLOCKING_CODES


def test_a_retired_group_is_judged_like_any_other(atlas_v1):
    """No qualifier in the rule text, so no visibility-style exemption is invented.

    This mirrors the scope decision ``validation.py`` already records for Task 9:
    §20.1's rule texts are not visibility-conditional, and a retired row that is
    invalid would pass the gate silently and reopen on the next re-activation.
    """
    retired = _group(version=atlas_v1.version, active=False)
    assert group_copy_keys(atlas_v1.version) == {retired.public_key}


def test_an_inactive_node_type_blocks_publish(atlas_v1):
    AtlasNodeType.objects.filter(pk=atlas_v1.identity.node_type_id).update(active=False)
    issues = validate_taxonomy(atlas_v1.version)

    assert [issue.node_key for issue in issues] == [atlas_v1.identity.public_key]
    assert codes(issues) == {"NODE_TYPE_INACTIVE"}
    assert issues[0].message_token == "atlas.nodeTypeInactive"
    assert "NODE_TYPE_INACTIVE" in BLOCKING_CODES and "NODE_TYPE_INACTIVE" not in WARNING_CODES


def test_an_active_taxonomy_reports_nothing(atlas_active_version):
    assert validate_taxonomy(atlas_active_version.version) == []


def test_an_inactive_type_blocks_even_when_only_a_hidden_node_uses_it(atlas_v1):
    """Every node is judged, visible or not — Task 9's scope decision for relations."""
    AtlasNode.objects.filter(pk=atlas_v1.identity.pk).update(visible=False)
    AtlasNodeType.objects.filter(pk=atlas_v1.identity.node_type_id).update(active=False)

    assert codes(validate_taxonomy(atlas_v1.version)) == {"NODE_TYPE_INACTIVE"}


# ---------------------------------------------------------------------------
# Canonical references (spec §5.4)
# ---------------------------------------------------------------------------


def test_a_missing_canonical_key_blocks_publish(atlas_v1):
    node_type = _node_type(key="project", canonical_source="project")
    node = _node(version=atlas_v1.version, node_type=node_type, canonical_translation_key=None)

    issues = validate_canonical_refs(atlas_v1.version)
    assert codes_for_node(issues, node) == {"CANONICAL_SOURCE_MISSING"}
    assert "CANONICAL_SOURCE_MISSING" in BLOCKING_CODES

    # An override supplies copy, never a reference: §5.4 requires the key itself.
    atlas_v1.override(node, locale="en", label="Label")
    atlas_v1.override(node, locale="fa", label="برچسب")
    assert codes_for_node(validate_canonical_refs(atlas_v1.version), node) == {
        "CANONICAL_SOURCE_MISSING"
    }


def test_an_unpublished_canonical_record_is_reported_as_unpublished(atlas_v1):
    """The record exists but does not pass ``objects.public()`` in a locale without an override."""
    node_type = _node_type(key="publication-link", canonical_source="publication")
    key = uuid4()
    for locale in ("en", "fa"):
        Publication.objects.create(
            locale=locale,
            slug=f"draft-publication-{locale}",
            title=f"Draft publication {locale}",
            status="draft",
            published_at=None,
            translation_key=key,
            abstract="Draft abstract",
        )
    node = _node(version=atlas_v1.version, node_type=node_type, canonical_translation_key=key)

    assert codes_for_node(validate_canonical_refs(atlas_v1.version), node) == {
        "CANONICAL_SOURCE_UNPUBLISHED"
    }
    assert node.public_key in parity_keys(atlas_v1.version)  # the gap is real in both gates

    # The code is about a locale that *lacks an override*: one override is not enough…
    atlas_v1.override(node, locale="en", label="Label")
    assert codes_for_node(validate_canonical_refs(atlas_v1.version), node) == {
        "CANONICAL_SOURCE_UNPUBLISHED"
    }
    # …both locales covered, and the record's non-publication stops being an issue.
    atlas_v1.override(node, locale="fa", label="برچسب")
    assert codes_for_node(validate_canonical_refs(atlas_v1.version), node) == set()


def test_a_key_with_no_row_at_all_is_missing_not_unpublished(atlas_v1):
    """The two codes answer different questions: is there a record? does the locale project?"""
    node_type = _node_type(key="project", canonical_source="project")
    node = _node(version=atlas_v1.version, node_type=node_type, canonical_translation_key=uuid4())

    assert codes_for_node(validate_canonical_refs(atlas_v1.version), node) == set()
    # One issue per locale that cannot project — no locale field exists on Issue,
    # so the count is what states "both directions".
    assert len(
        [
            issue
            for issue in validate_locale_projection(atlas_v1.version)
            if issue.node_key == node.public_key
        ]
    ) == 2


def test_an_ambiguous_canonical_reference_blocks_publish(atlas_v1):
    """Two published rows for one ``(family, translation_key, locale)`` — never guessed."""
    node = atlas_v1.areas[0]
    ResearchTopic.objects.create(
        locale="en",
        slug="research-topic-en-twin",
        title="Research topic en twin",
        status="published",
        published_at=PUBLISHED_AT,
        translation_key=node.canonical_translation_key,
        summary="Twin summary",
    )

    issues = validate_canonical_refs(atlas_v1.version)
    assert codes_for_node(issues, node) == {"AMBIGUOUS_CANONICAL_REF"}
    assert "AMBIGUOUS_CANONICAL_REF" in BLOCKING_CODES
    # An ambiguous locale is not a *missing* one: the parity gate does not double-report it.
    assert node.public_key not in parity_keys(atlas_v1.version)


@pytest.mark.parametrize(
    "ambiguous_locale",
    ("en", "fa"),
    ids=("en-ambiguous-fa-empty", "fa-ambiguous-en-empty"),
)
def test_an_ambiguous_locale_does_not_mask_the_other_locale_s_missing_projection(
    atlas_v1, ambiguous_locale
):
    """F3: one locale's ambiguity masks only itself, never the other locale's gap.

    ``resolve_canonical_pair`` abandons *every* remaining locale when one of them is
    ambiguous, so abandoning the whole node (the reviewed ``except …: continue``)
    dropped one of the two facts this node carries: two published rows in one locale
    (``AMBIGUOUS_CANONICAL_REF`` — the reference itself is the defect) and **no row at
    all** in the other (``MISSING_LOCALE_PROJECTION`` — the locale cannot project).
    The gate still blocked, so this is report precision, not a hole; the two codes
    answer different questions and neither may mask the other.
    """
    missing_locale = "fa" if ambiguous_locale == "en" else "en"
    node_type = _node_type("ambiguity-probe", canonical_source="research_topic")
    key = uuid4()
    for index in range(2):
        ResearchTopic.objects.create(
            locale=ambiguous_locale,
            slug=f"research-topic-{ambiguous_locale}-probe-{index}",
            title=f"Research topic {ambiguous_locale} probe {index}",
            status="published",
            published_at=PUBLISHED_AT,
            translation_key=key,
            summary="Twin summary",
        )
    node = _node(version=atlas_v1.version, node_type=node_type, canonical_translation_key=key)

    # The other locale has no row at all: a missing projection, not an unpublished
    # record — the refs gate names the ambiguity alone, never an alias of the gap.
    assert not ResearchTopic.objects.filter(
        translation_key=key, locale=missing_locale
    ).exists()
    assert codes_for_node(validate_canonical_refs(atlas_v1.version), node) == {
        "AMBIGUOUS_CANONICAL_REF"
    }

    assert node.public_key in parity_keys(atlas_v1.version)
    # Exactly one issue, and it is the missing locale's: the ambiguous locale is not
    # *missing* (the refs gate names it), so a count of two would mean the handler
    # turned the ambiguity into a gap as well.
    assert len(
        [
            issue
            for issue in validate_locale_projection(atlas_v1.version)
            if issue.node_key == node.public_key
        ]
    ) == 1


# ---------------------------------------------------------------------------
# Cross-cutting: code vocabulary, issue shape, purity
# ---------------------------------------------------------------------------


def test_every_task_ten_code_is_blocking_and_carries_its_message_token(atlas_dag, atlas_v1):
    version = atlas_v1.version
    AtlasNodeType.objects.filter(pk=atlas_v1.identity.node_type_id).update(active=False)
    atlas_v1.override(atlas_v1.fa_missing_node, locale="fa", label="برچسب")
    _node(
        version=version,
        node_type=_node_type(key="project", canonical_source="project"),
        canonical_translation_key=None,
    )
    _group(version=version)
    AtlasNode.objects.filter(pk=atlas_v1.areas[1].pk).update(visible=False)
    atlas_dag.close_cycle()

    issues = [
        *blocking_report(version).blocking,
        *validate_hierarchy(atlas_dag.version),
    ]
    reported = codes(issues)
    assert {
        "HIERARCHY_CYCLE",
        "MISSING_LOCALE_PROJECTION",
        "CANONICAL_SOURCE_MISSING",
        "CANONICAL_SOURCE_UNPUBLISHED",
        "NODE_TYPE_INACTIVE",
        "GROUP_LOCALE_MISSING",
        "DANGLING_NODE_HIDDEN_RELATION",
    } <= reported

    for issue in issues:
        assert issue.code in ATLAS_ISSUE_CODES
        assert issue.code in BLOCKING_CODES and issue.code not in WARNING_CODES
        assert [issue.node_key, issue.relation_key, issue.group_key].count(None) >= 2
        assert issue.message_token == expected_token(issue.code)
    assert set(ATLAS_ISSUE_CODES) == set(BLOCKING_CODES) | set(WARNING_CODES)


def test_validation_never_reshapes_the_graph(atlas_v1):
    """Spec §20.3: a validator returns issues and never a repaired graph."""
    before_nodes = {
        node.public_key: (node.visible, node.node_type_id, node.canonical_translation_key)
        for node in atlas_v1.version.nodes.all()
    }
    before_relations = {
        relation.pk: relation.public_key for relation in atlas_v1.version.relations.all()
    }
    before_layout = dict(atlas_v1.version.layout)

    blocking_report(atlas_v1.version)

    assert {
        node.public_key: (node.visible, node.node_type_id, node.canonical_translation_key)
        for node in atlas_v1.version.nodes.all()
    } == before_nodes
    assert {
        relation.pk: relation.public_key for relation in atlas_v1.version.relations.all()
    } == before_relations
    assert dict(atlas_v1.version.layout) == before_layout
