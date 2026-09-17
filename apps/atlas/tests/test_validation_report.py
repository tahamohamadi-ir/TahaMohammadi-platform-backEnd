"""Task 12 — the aggregate validation report and the payload contract check.

What this file pins, and why (each item is a recorded ruling or review finding,
not a preference — see ``plans/LEDGER-A-DOMAIN-API.md``):

* :func:`validate_version` aggregates **six** validator functions. The plan's
  Task 12 step names four; ``DANGLING_NODE_HIDDEN_RELATION`` lives in
  ``validate_visibility``, so a four-function aggregate would silently drop a
  blocker (the plan's own Task 10 amendment says "Task 12 must aggregate all six
  functions").
* the frozen vocabulary is spec §20.1 + §20.2 + ``DIRECTION_NOT_OVERRIDABLE``:
  that code is a real, tested publish blocker that occurs **zero** times in the
  spec, so a literal "equal to §20.1 + §20.2 exactly" step would delete it
  (ledger rows ``9-cf`` / ``9-note``; the spec gap is reported for the owner).
* ``DUPLICATE_RELATION`` is de-duplicated **per conflicting pair** by the report:
  when an undirected row and a reversed directed row carry different key
  spellings, the validator reports one issue per shared key — two rows where
  there is one conflict (ledger row ``9-note``).
* ``MISSING_LOCALE_PROJECTION`` is de-duplicated **per node + code** by the
  report: an ``Issue`` carries no locale, so a node missing both locales emits
  two indistinguishable rows (ledger row ``12-note`` a). The raw validators keep
  emitting the per-locale facts — the report is what collapses them.
* canonical resolutions are computed **once per node** and shared with every
  validator that needs them (Task 10 review INFO: the six validators cost
  133/333/413 queries at 30/80/100 nodes because ``resolve_canonical`` ran per
  node per locale).
* any ordering claim is falsified by **permuting row order** and comparing whole
  reports (ruling R11).
* the payload contract check is the serving-time gate Task 15 calls on Task 14's
  projection; its interface is stated in the evidence file.

Scope decisions this file also pins (recorded in the module docstring of
``apps/atlas/validation.py``):

* the graph-shape warnings (isolation, degree, scale, single-level hierarchy)
  judge the **visible** graph — the served payload is visible-only and §20.2
  names "visible" where it means it;
* ``UNUSED_NODE_TYPE`` / ``UNUSED_RELATION_TYPE`` judge the version's whole
  graph ("no node in this version") and fire once per version: an ``Issue``
  carries no type key, so a per-type row would be an unnameable duplicate;
* ``OVERLAPPING_PINS`` names each pinned node that violates separation.
"""

import json
from copy import deepcopy
from uuid import uuid4

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.atlas import validation
from apps.atlas.models import AtlasNode, AtlasNodeType
from apps.atlas.tests.factories import (
    NODE_TYPE_DEFAULTS,
    _apply_placeholder_layout,
    _group,
    _identity_node_type,
    _membership,
    _node,
    _node_type,
    _relation,
    _relation_type,
    _seed_pair,
    _version,
    atlas_active_version,
    atlas_scale_fixture,
    atlas_v1,
)
from apps.atlas.validation import (
    ATLAS_ISSUE_CODES,
    BLOCKING_CODES,
    PIN_BOUND,
    SCALE_WARN_THRESHOLDS,
    WARNING_CODES,
    validate_canonical_refs,
    validate_hierarchy,
    validate_layout,
    validate_locale_projection,
    validate_payload_contract,
    validate_pins,
    validate_relations,
    validate_taxonomy,
    validate_version,
    validate_visibility,
    warning_issues,
)
from apps.content.models import ResearchTopic

__all__ = [
    "atlas_active_version",
    "atlas_scale_fixture",
    "atlas_v1",
]

pytestmark = pytest.mark.django_db

WARNING_CODES_FROZEN = (
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

BLOCKING_CODES_FROZEN = (
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def expected_token(code: str) -> str:
    """``SCALE_NODES`` → ``atlas.scaleNodes`` — the token a code spells."""
    head, *rest = code.lower().split("_")
    return "atlas." + head + "".join(part.capitalize() for part in rest)


def codes(issues) -> set[str]:
    """The distinct codes ``issues`` reports."""
    return {issue.code for issue in issues}


def keys_for(issues, code: str, attribute: str = "node_key") -> set[str | None]:
    """The entity keys ``issues`` reports for one code."""
    return {getattr(issue, attribute) for issue in issues if issue.code == code}


def naive_blocking(version):
    """The six validators called one by one — the aggregate's naive shape.

    Task 10's test file composes the same six by hand because
    ``validate_version`` is Task 12's; keeping the shape here lets a test show
    that sharing resolutions changed no verdict and saved queries.
    """
    return [
        *validate_relations(version),
        *validate_hierarchy(version),
        *validate_visibility(version),
        *validate_locale_projection(version),
        *validate_taxonomy(version),
        *validate_canonical_refs(version),
    ]


def structural_type():
    """The structural node type of this file's small graphs (no canonical record)."""
    node_type, _created = AtlasNodeType.objects.get_or_create(
        key="structural", defaults=dict(NODE_TYPE_DEFAULTS, canonical_source="none")
    )
    return node_type


def clean_node(version, node_type, key, *, label="label", summary="summary", locales=("en", "fa")):
    """A visible node that resolves in every ``locales`` entry (both, by default)."""
    node = _node(version=version, node_type=node_type, public_key=key)
    for locale in locales:
        node.translations.create(
            locale=locale,
            label_override=f"{label} {locale}",
            summary_override="" if summary is None else f"{summary} {locale}",
        )
    return node


def small_version(*, label, node_keys=(), node_type=None, locales=("en", "fa")):
    """A draft version of clean visible nodes with a stored placeholder layout."""
    version = _version(status="draft", label=label)
    node_type = structural_type() if node_type is None else node_type
    nodes = [clean_node(version, node_type, key, locales=locales) for key in node_keys]
    _apply_placeholder_layout(version, nodes)
    return version, nodes


# ---------------------------------------------------------------------------
# The report API (Task 9 review LOW-4: the type shipped with no test)
# ---------------------------------------------------------------------------


def test_the_report_aggregates_every_validator_group(atlas_v1):
    """One version carrying a defect from every validator group.

    Dropping any *single* function from the aggregate makes exactly this test
    fail, because every group's distinctive code is asserted here — including
    ``validate_visibility``'s (the plan's four-function step would have lost
    ``DANGLING_NODE_HIDDEN_RELATION``).
    """
    version = atlas_v1.version
    identity_type = atlas_v1.identity.node_type
    AtlasNodeType.objects.filter(pk=identity_type.pk).update(active=False)
    AtlasNode.objects.filter(pk=atlas_v1.en_missing_node.pk).update(visible=False)
    missing_canonical = _node(
        version=version,
        node_type=_node_type(key="canonical-less", canonical_source="project"),
        canonical_translation_key=None,
        public_key="canonical-less-00000001",
    )
    now_pinned_and_unstored = clean_node(version, identity_type, "identity-99999999")
    AtlasNode.objects.filter(pk=now_pinned_and_unstored.pk).update(pin_x=1.0, pin_y=None)
    for key in ("peer-of", "sibling-of"):
        undirected = _relation_type(
            key, hierarchy_role=True, directed_default=False, overridable_direction=True
        )
        _relation(
            source=atlas_v1.areas[0],
            target=atlas_v1.areas[1],
            relation_type=undirected,
            version=version,
        )
    _apply_placeholder_layout(version, list(version.nodes.all()))
    atlas_v1.corrupt_layout(lambda layout: layout.pop(now_pinned_and_unstored.public_key, None))

    report = validate_version(version)

    assert {
        "RELATION_TYPE_NOT_ALLOWED",  # validate_relations
        "HIERARCHY_CYCLE",  # validate_hierarchy
        "DANGLING_NODE_HIDDEN_RELATION",  # validate_visibility
        "MISSING_LOCALE_PROJECTION",  # validate_locale_projection
        "NODE_TYPE_INACTIVE",  # validate_taxonomy
        "CANONICAL_SOURCE_MISSING",  # validate_canonical_refs
        "MISSING_LAYOUT",  # validate_layout
        "INVALID_PIN",  # validate_pins
    } <= set(report.blocking_codes())
    assert missing_canonical.public_key in keys_for(report.blocking, "CANONICAL_SOURCE_MISSING")
    assert set(report.blocking_codes()) <= set(BLOCKING_CODES)
    assert set(report.warning_codes()) <= set(WARNING_CODES)


def test_report_shape_is_stable_and_json_serializable(atlas_v1):
    """The plan's Step 1 snippet, run literally."""
    payload = validate_version(atlas_v1.version).to_dict()
    assert set(payload) == {"blocking", "warnings"}
    assert all(
        set(issue) <= {"code", "nodeKey", "relationKey", "groupKey", "messageToken"}
        for issue in payload["blocking"]
    )
    json.dumps(payload)


def test_the_report_is_sorted_by_code_then_entity_key(atlas_v1):
    """Stable sorting by ``(code, relationKey/nodeKey/groupKey)`` (plan Step 3)."""
    report = validate_version(atlas_v1.version)
    for issues in (report.blocking, report.warnings):
        assert issues == sorted(
            issues,
            key=lambda issue: (
                issue.code,
                issue.relation_key or "",
                issue.node_key or "",
                issue.group_key or "",
            ),
        )


def test_the_report_codes_are_the_distinct_sorted_codes(atlas_v1):
    """``blocking_codes()`` / ``warning_codes()`` are the distinct sorted codes."""
    report = validate_version(atlas_v1.version)
    assert report.blocking_codes() == sorted({issue.code for issue in report.blocking})
    assert report.warning_codes() == sorted({issue.code for issue in report.warnings})
    assert report.blocking_codes(), "the fixture must have blockers for this to mean anything"


def test_the_wire_spelling_is_camel_case_and_omits_unset_keys(atlas_v1):
    """``to_dict`` is the admin's wire shape: camelCase, one key per entity."""
    payload = validate_version(atlas_v1.version).to_dict()
    for issue in payload["blocking"] + payload["warnings"]:
        assert set(issue) <= {"code", "nodeKey", "relationKey", "groupKey", "messageToken"}
        assert issue["code"] in ATLAS_ISSUE_CODES
        assert issue["messageToken"] == expected_token(issue["code"])
        assert list(issue)[0] == "code" and list(issue)[-1] == "messageToken"
        names = [name for name in ("nodeKey", "relationKey", "groupKey") if name in issue]
        assert len(names) <= 1
        assert set(issue) >= {"code", "messageToken"}


# ---------------------------------------------------------------------------
# The frozen vocabulary
# ---------------------------------------------------------------------------


def test_the_frozen_vocabulary_is_the_two_spec_tables_plus_the_plan_code():
    """Spec §20.1 + §20.2 + ``DIRECTION_NOT_OVERRIDABLE`` (ledger row ``9-cf``).

    ``DIRECTION_NOT_OVERRIDABLE`` occurs zero times in the spec and is a real,
    tested publish blocker (a ``directed`` value contradicting ``directed_default``
    while ``overridable_direction`` is false violates §5.3). Freezing the tables
    "exactly" would delete it, so the frozen tuple is pinned here instead.
    """
    assert BLOCKING_CODES == BLOCKING_CODES_FROZEN
    assert WARNING_CODES == WARNING_CODES_FROZEN
    assert ATLAS_ISSUE_CODES == BLOCKING_CODES + WARNING_CODES
    assert not set(BLOCKING_CODES) & set(WARNING_CODES)
    assert "DIRECTION_NOT_OVERRIDABLE" in BLOCKING_CODES
    assert len(BLOCKING_CODES) == 18 and len(WARNING_CODES) == 11


def _emission_sites() -> set[str]:
    """Every code literal this module passes to ``_issue`` — its live emission sites."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(validation))
    emitted: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Name) or node.func.id != "_issue":
            continue
        first = node.args[0] if node.args else None
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            emitted.add(first.value)
    return emitted


def test_every_frozen_code_has_a_live_emission_site_and_nothing_else_is_emitted():
    """Vocabulary freeze, both directions (Task 10 review's grouping check).

    Adding a code to the tuple that nothing emits fails here; so does an
    emission site whose code was dropped from the tuple.
    """
    emitted = _emission_sites()
    assert emitted == set(ATLAS_ISSUE_CODES)
    assert "MULTIPLE_PARENTS_ALLOWED" not in emitted  # a legal shape has no code


def test_scale_thresholds_are_the_plan_constants_with_one_home():
    """``SCALE_WARN_THRESHOLDS`` is the constant the warning rules read."""
    assert SCALE_WARN_THRESHOLDS == {"nodes": 100, "relations": 250, "hubs": 12}
    assert PIN_BOUND > 0


# ---------------------------------------------------------------------------
# Warnings (§20.2) — one test per code, each proving producibility
# ---------------------------------------------------------------------------


def _two_connected_nodes(label):
    """Two clean nodes joined by one directed relation — the warnings' base graph."""
    version, nodes = small_version(
        label=label, node_keys=("structural-00000001", "structural-00000002")
    )
    _relation(
        source=nodes[0],
        target=nodes[1],
        relation_type=_relation_type("links", directed_default=True),
        version=version,
    )
    return version, nodes


def test_isolated_node_warns_only_for_unconnected_unmembered_nodes():
    version = _version(status="draft", label="task12-isolated")
    node_type = structural_type()
    first = clean_node(version, node_type, "structural-00000001")
    second = clean_node(version, node_type, "structural-00000002")
    stray = clean_node(version, node_type, "structural-00000003")
    _relation(
        source=first,
        target=second,
        relation_type=_relation_type("links", directed_default=True),
        version=version,
    )
    _apply_placeholder_layout(version, list(version.nodes.all()))

    issues = warning_issues(version)
    assert keys_for(issues, "ISOLATED_NODE") == {stray.public_key}
    assert [issue.message_token for issue in issues if issue.code == "ISOLATED_NODE"] == [
        "atlas.isolatedNode"
    ]
    assert "ISOLATED_NODE" in WARNING_CODES


def test_a_group_membership_alone_clears_isolation():
    version, nodes = small_version(
        label="task12-isolated-group", node_keys=("structural-00000001",)
    )
    group = _group(version=version, public_key="group-00000001")
    _membership(group, nodes[0])
    assert "ISOLATED_NODE" not in codes(warning_issues(version))


def test_no_inbound_and_no_outbound_relations_follow_direction():
    version, nodes = _two_connected_nodes("task12-direction")
    issues = warning_issues(version)
    assert keys_for(issues, "NO_INBOUND_RELATIONS") == {nodes[0].public_key}
    assert keys_for(issues, "NO_OUTBOUND_RELATIONS") == {nodes[1].public_key}
    assert keys_for(issues, "NO_INBOUND_RELATIONS", "message_token") == {
        "atlas.noInboundRelations"
    }
    assert keys_for(issues, "NO_OUTBOUND_RELATIONS", "message_token") == {
        "atlas.noOutboundRelations"
    }


def test_an_undirected_relation_counts_as_both_directions():
    """An undirected row is traversable both ways, like the hierarchy rule reads it."""
    version, nodes = small_version(
        label="task12-undirected", node_keys=("structural-00000001", "structural-00000002")
    )
    _relation(
        source=nodes[0],
        target=nodes[1],
        relation_type=_relation_type("links", directed_default=False),
        version=version,
    )
    issues = warning_issues(version)
    assert "NO_INBOUND_RELATIONS" not in codes(issues)
    assert "NO_OUTBOUND_RELATIONS" not in codes(issues)


def test_high_degree_hub_needs_more_than_twelve_relations():
    """``> 12 relations on one node`` (spec §12.7) — the threshold constant is read."""
    version = _version(status="draft", label="task12-hub")
    hub = clean_node(version, structural_type(), "structural-00000001")
    targets = [
        clean_node(version, structural_type(), f"structural-{index:08x}")
        for index in range(2, 15)
    ]
    relation_type = _relation_type("links", directed_default=True)
    for target in targets[:12]:
        _relation(source=hub, target=target, relation_type=relation_type, version=version)
    _apply_placeholder_layout(version, list(version.nodes.all()))
    assert "HIGH_DEGREE_HUB" not in codes(warning_issues(version))

    _relation(source=hub, target=targets[12], relation_type=relation_type, version=version)
    issues = warning_issues(version)
    assert keys_for(issues, "HIGH_DEGREE_HUB") == {hub.public_key}
    assert SCALE_WARN_THRESHOLDS["hubs"] == 12


def test_summary_missing_is_about_the_summary_not_the_label():
    version = _version(status="draft", label="task12-summary")
    node_type = structural_type()
    labelled = clean_node(version, node_type, "structural-00000001", summary=None)
    full = clean_node(version, node_type, "structural-00000002")
    _apply_placeholder_layout(version, list(version.nodes.all()))

    assert keys_for(warning_issues(version), "SUMMARY_MISSING") == {labelled.public_key}

    labelled.translations.filter(locale="fa").update(summary_override="خلاصه")
    assert "SUMMARY_MISSING" not in codes(warning_issues(version))
    assert full.public_key not in keys_for(warning_issues(version), "SUMMARY_MISSING")


def test_summary_missing_reads_a_canonical_summary_too():
    """A resolved canonical summary (spec §5.4.2 precedence) clears the warning."""
    version = _version(status="draft", label="task12-summary-canonical")
    area_type = _node_type(key="research-area", **dict(NODE_TYPE_DEFAULTS))
    with_summary = _seed_pair(
        ResearchTopic, uuid4(), en={"summary": "Research summary"}, fa={"summary": "خلاصه"}
    )
    without_summary = _seed_pair(ResearchTopic, uuid4(), en={"title": "T"}, fa={"title": "ت"})
    _node(
        version=version,
        node_type=area_type,
        public_key="research-area-00000001",
        canonical_translation_key=with_summary[0].translation_key,
    )
    _node(
        version=version,
        node_type=area_type,
        public_key="research-area-00000002",
        canonical_translation_key=without_summary[0].translation_key,
    )
    _apply_placeholder_layout(version, list(version.nodes.all()))

    issues = warning_issues(version)
    assert keys_for(issues, "SUMMARY_MISSING") == {"research-area-00000002"}
    assert "MISSING_LOCALE_PROJECTION" not in codes(issues)  # parity still resolves


def test_unused_node_type_names_no_type_because_the_issue_shape_cannot():
    """One unnameable row per version, not one per type (see the module docstring)."""
    version = _version(status="draft", label="task12-unused-types")
    _node_type(key="spare-one", **dict(NODE_TYPE_DEFAULTS))
    _node_type(key="spare-two", **dict(NODE_TYPE_DEFAULTS))
    clean_node(version, structural_type(), "structural-00000001")
    _apply_placeholder_layout(version, list(version.nodes.all()))

    issues = [issue for issue in warning_issues(version) if issue.code == "UNUSED_NODE_TYPE"]
    assert len(issues) == 1
    assert issues[0].node_key is None and issues[0].group_key is None
    assert issues[0].message_token == "atlas.unusedNodeType"


def test_unused_relation_type_is_judged_in_the_version():
    version, nodes = small_version(
        label="task12-unused-relations", node_keys=("structural-00000001", "structural-00000002")
    )
    _relation(
        source=nodes[0],
        target=nodes[1],
        relation_type=_relation_type("used"),
        version=version,
    )
    _relation_type("spare")

    issues = [issue for issue in warning_issues(version) if issue.code == "UNUSED_RELATION_TYPE"]
    assert len(issues) == 1
    assert issues[0].relation_key is None


def test_overlapping_pins_warn_per_pinned_node_outside_one_group():
    version, nodes = small_version(
        label="task12-pins", node_keys=("structural-00000001", "structural-00000002")
    )
    first, second = nodes
    AtlasNode.objects.filter(pk=first.pk).update(pin_x=0.0, pin_y=0.0)
    AtlasNode.objects.filter(pk=second.pk).update(pin_x=30.0, pin_y=0.0)  # radii 12 + 12 < 30
    assert "OVERLAPPING_PINS" not in codes(
        [issue for issue in warning_issues(version) if issue.code == "OVERLAPPING_PINS"]
    )

    AtlasNode.objects.filter(pk=second.pk).update(pin_x=10.0)  # inside the radii sum
    assert keys_for(warning_issues(version), "OVERLAPPING_PINS") == {
        first.public_key,
        second.public_key,
    }

    group = _group(version=version, public_key="group-00000001")
    _membership(group, first)
    _membership(group, second)
    assert "OVERLAPPING_PINS" not in codes(warning_issues(version))


def test_invalid_pin_is_a_blocker_and_overlapping_pins_are_warnings():
    assert "INVALID_PIN" in BLOCKING_CODES and "INVALID_PIN" not in WARNING_CODES
    assert "OVERLAPPING_PINS" in WARNING_CODES and "OVERLAPPING_PINS" not in BLOCKING_CODES


def test_scale_warnings_fire_above_thresholds(atlas_scale_fixture):
    """The plan's Step 1 snippet, run literally."""
    report = validate_version(atlas_scale_fixture.version(visible_nodes=101))
    assert "SCALE_NODES" in report.warning_codes()
    assert "SCALE_NODES" not in report.blocking_codes()


def test_scale_relations_fires_above_two_hundred_and_fifty(atlas_scale_fixture):
    below = validate_version(atlas_scale_fixture.version(visible_nodes=101, relations=250))
    assert "SCALE_RELATIONS" not in below.warning_codes()
    above = validate_version(atlas_scale_fixture.version(visible_nodes=101, relations=251))
    assert "SCALE_RELATIONS" in above.warning_codes()
    assert "SCALE_RELATIONS" not in above.blocking_codes()


def test_the_overlapping_pin_rule_reads_the_engine_radii_including_the_wider_anchor():
    """The identity anchor draws at ``anchor_ratio`` × the widest domain radius (§12.2 step 2).

    With the anchor's *plain* importance radius the two pins are 3.6 units apart;
    the engine's anchor rule makes them overlap, and the warning must read the
    engine's own constants rather than a second copy of the numbers.
    """
    version, nodes = small_version(label="task12-anchor-pins", node_keys=("structural-00000001",))
    AtlasNode.objects.filter(pk=nodes[0].pk).update(importance=100, pin_x=30.0, pin_y=0.0)
    _node(
        version=version,
        node_type=_identity_node_type(),
        public_key="identity-00000001",
        importance=20,
        pin_x=0.0,
        pin_y=0.0,
    )
    _apply_placeholder_layout(version, list(version.nodes.all()))

    assert keys_for(warning_issues(version), "OVERLAPPING_PINS") == {
        "identity-00000001",
        "structural-00000001",
    }
    AtlasNode.objects.filter(public_key="structural-00000001").update(pin_x=60.0)
    assert "OVERLAPPING_PINS" not in codes(warning_issues(version))


def test_single_level_hierarchy_warns_without_a_hierarchy_role_relation():
    version, nodes = small_version(
        label="task12-single-level", node_keys=("structural-00000001", "structural-00000002")
    )
    _relation(
        source=nodes[0],
        target=nodes[1],
        relation_type=_relation_type("links", hierarchy_role=False),
        version=version,
    )
    assert "SINGLE_LEVEL_HIERARCHY" in codes(warning_issues(version))

    _relation(
        source=nodes[0],
        target=nodes[1],
        relation_type=_relation_type("parent-of", hierarchy_role=True),
        version=version,
    )
    assert "SINGLE_LEVEL_HIERARCHY" not in codes(warning_issues(version))


def test_two_undirected_hierarchy_rows_on_one_pair_are_a_cycle():
    """Ledger row ``12-note`` b: §5.6 permits several types on a pair, not a closed walk.

    Two undirected hierarchy relations between one pair are two *distinct*
    relations, so walking one out and the other back is a closed walk of distinct
    relations — a cycle, and it is reported. The sentence lives in the rule's own
    docstring; this test is its executable half.
    """
    version, nodes = small_version(
        label="task12-two-undirected", node_keys=("structural-00000001", "structural-00000002")
    )
    first, second = nodes
    _relation(
        source=first,
        target=second,
        relation_type=_relation_type("peer-of", hierarchy_role=True, directed_default=False),
        version=version,
    )
    _relation(
        source=second,
        target=first,
        relation_type=_relation_type("sibling-of", hierarchy_role=True, directed_default=False),
        version=version,
    )
    issues = validate_hierarchy(version)
    assert [issue.code for issue in issues] == ["HIERARCHY_CYCLE"]
    assert issues[0].node_key == first.public_key
    assert "HIERARCHY_CYCLE" in validate_version(version).blocking_codes()


def test_parallel_hierarchy_rows_in_one_direction_are_not_a_cycle():
    """Two relations of one direction close no walk — the legal parallel case."""
    version, nodes = small_version(
        label="task12-parallel", node_keys=("structural-00000001", "structural-00000002")
    )
    first, second = nodes
    for key in ("peer-of", "sibling-of"):
        _relation(
            source=first,
            target=second,
            relation_type=_relation_type(key, hierarchy_role=True, directed_default=True),
            version=version,
        )
    assert "HIERARCHY_CYCLE" not in codes(validate_hierarchy(version))


# ---------------------------------------------------------------------------
# Blocking additions: layout, pins, and the issue-level de-duplications
# ---------------------------------------------------------------------------


def test_missing_layout_blocks_a_visible_node_without_a_stored_coordinate(atlas_active_version):
    version = atlas_active_version.version
    assert validate_layout(version) == []
    atlas_active_version.corrupt_layout(
        lambda layout: layout.pop(atlas_active_version.identity.public_key)
    )

    issues = validate_layout(version)
    assert [issue.code for issue in issues] == ["MISSING_LAYOUT"]
    assert issues[0].node_key == atlas_active_version.identity.public_key
    assert issues[0].message_token == "atlas.missingLayout"
    assert "MISSING_LAYOUT" in validate_version(version).blocking_codes()

    # A stored value that is not three finite numbers is no coordinate either.
    # ``NaN`` cannot even be stored: the JSONField's ``JSON_VALID`` check rejects
    # it (`sqlite3.IntegrityError: CHECK constraint failed`), so the finiteness
    # branch of ``_stored_coordinate`` is defence in depth and the shapes below
    # are the reachable ones.
    atlas_active_version.corrupt_layout(
        lambda layout: layout.__setitem__(
            atlas_active_version.identity.public_key, [0.0, "x", 0.0]
        )
    )
    assert keys_for(validate_layout(version), "MISSING_LAYOUT") == {
        atlas_active_version.identity.public_key
    }
    atlas_active_version.corrupt_layout(
        lambda layout: layout.__setitem__(atlas_active_version.identity.public_key, [0.0, 1.0])
    )
    assert keys_for(validate_layout(version), "MISSING_LAYOUT") == {
        atlas_active_version.identity.public_key
    }
    atlas_active_version.corrupt_layout(
        lambda layout: layout.__setitem__(
            atlas_active_version.identity.public_key, {"x": 0.0, "y": 0.0, "z": 0.0}
        )
    )
    assert keys_for(validate_layout(version), "MISSING_LAYOUT") == {
        atlas_active_version.identity.public_key
    }


def test_invalid_pin_covers_pairing_finiteness_and_bounds(atlas_active_version):
    version = atlas_active_version.version
    node = atlas_active_version.identity
    assert validate_pins(version) == []

    AtlasNode.objects.filter(pk=node.pk).update(pin_x=1.0, pin_y=None)
    assert keys_for(validate_pins(version), "INVALID_PIN") == {node.public_key}

    AtlasNode.objects.filter(pk=node.pk).update(pin_x=float("nan"), pin_y=0.0)
    assert keys_for(validate_pins(version), "INVALID_PIN") == {node.public_key}

    AtlasNode.objects.filter(pk=node.pk).update(pin_x=PIN_BOUND + 1.0, pin_y=0.0)
    assert keys_for(validate_pins(version), "INVALID_PIN") == {node.public_key}

    AtlasNode.objects.filter(pk=node.pk).update(pin_x=0.0, pin_y=0.0)
    assert validate_pins(version) == []
    assert "INVALID_PIN" not in validate_version(version).blocking_codes()


def test_a_pin_z_without_a_pin_pair_is_invalid():
    version, nodes = small_version(label="task12-pin-z", node_keys=("structural-00000001",))
    AtlasNode.objects.filter(pk=nodes[0].pk).update(pin_z=5.0)
    assert keys_for(validate_pins(version), "INVALID_PIN") == {nodes[0].public_key}


def test_missing_locale_projection_is_collapsed_to_one_row_per_node():
    """Ledger row ``12-note`` a: the report collapses the per-locale rows."""
    version, nodes = small_version(
        label="task12-parity-dedup", node_keys=("structural-00000001",), locales=()
    )
    node = nodes[0]
    raw = validate_locale_projection(version)
    assert [issue.code for issue in raw] == ["MISSING_LOCALE_PROJECTION"] * 2, (
        "the raw validator keeps one row per locale — its facts are per locale"
    )

    report = validate_version(version)
    assert [
        issue for issue in report.blocking if issue.code == "MISSING_LOCALE_PROJECTION"
    ] == [raw[0]]
    assert node.public_key in keys_for(report.blocking, "MISSING_LOCALE_PROJECTION")


def test_a_conflicting_relation_pair_is_reported_once_whatever_the_key_spellings():
    """Ledger row ``9-note``: one conflict, however the two rows spell the key."""
    version, nodes = small_version(
        label="task12-duplicate-dedup",
        node_keys=("structural-00000001", "structural-00000002"),
    )
    first, second = nodes
    related = _relation_type("related-to", directed_default=False, overridable_direction=True)
    _relation(source=first, target=second, relation_type=related, version=version)
    _relation(source=second, target=first, relation_type=related, version=version, directed=True)

    raw = [issue for issue in validate_relations(version) if issue.code == "DUPLICATE_RELATION"]
    assert {issue.relation_key for issue in raw} == {
        f"{first.public_key}~related-to~{second.public_key}",
        f"{second.public_key}~related-to~{first.public_key}",
    }, "the raw validator reports one issue per shared key spelling"

    report = validate_version(version)
    collapsed = [issue for issue in report.blocking if issue.code == "DUPLICATE_RELATION"]
    assert len(collapsed) == 1
    assert collapsed[0].relation_key == f"{first.public_key}~related-to~{second.public_key}"


def test_a_mirrored_undirected_pair_is_still_reported_once():
    """When the spellings coincide the validator already reports once; the report keeps it."""
    version, nodes = small_version(
        label="task12-duplicate-mirror",
        node_keys=("structural-00000001", "structural-00000002"),
    )
    first, second = nodes
    related = _relation_type("related-to", directed_default=False)
    _relation(source=first, target=second, relation_type=related, version=version)
    _relation(source=second, target=first, relation_type=related, version=version)

    report = validate_version(version)
    assert len([issue for issue in report.blocking if issue.code == "DUPLICATE_RELATION"]) == 1


def test_two_directed_rows_between_one_pair_are_legal():
    """Opposite directed rows share no identity — only the undirected mirror does."""
    version, nodes = small_version(
        label="task12-directed-pair",
        node_keys=("structural-00000001", "structural-00000002"),
    )
    first, second = nodes
    links = _relation_type("links", directed_default=True)
    _relation(source=first, target=second, relation_type=links, version=version)
    _relation(source=second, target=first, relation_type=links, version=version)

    assert "DUPLICATE_RELATION" not in codes(validate_relations(version))


def test_the_report_keeps_one_row_per_code_and_entity():
    """A repeated (code, entity) fact is reported once — the shape has no locale."""
    version, nodes = small_version(
        label="task12-one-row", node_keys=("structural-00000001",), locales=()
    )
    report = validate_version(version)
    seen = [
        (issue.code, issue.node_key, issue.relation_key, issue.group_key)
        for issue in report.blocking + report.warnings
    ]
    assert len(seen) == len(set(seen))
    assert seen, "a version with a node missing both locales must report something"


# ---------------------------------------------------------------------------
# R11 — row order must not move the verdict
# ---------------------------------------------------------------------------


def _permutation_graph(node_type, relation_types, *, reverse: bool):
    """One logical graph, built in two insertion orders (ruling R11).

    Contains a two-undirected-row hierarchy cycle, a conflicting relation pair
    with *different* key spellings, a direction override violation and an
    isolated node.
    """
    version = _version(status="draft", label=f"task12-permutation-{reverse}")
    keys = [f"structural-{index:08x}" for index in range(1, 6)]
    order = list(reversed(keys)) if reverse else keys
    nodes = {}
    for key in order:
        nodes[key] = clean_node(version, node_type, key)

    rows = [
        ("peer-of", keys[0], keys[1], False),
        ("sibling-of", keys[1], keys[0], False),
        ("related-to", keys[2], keys[3], False),
        ("related-to", keys[3], keys[2], True),
    ]
    if reverse:
        rows = list(reversed(rows))
    for index, (type_key, source, target, directed) in enumerate(rows):
        _relation(
            source=nodes[source],
            target=nodes[target],
            relation_type=relation_types[type_key],
            version=version,
            directed=directed,
            sort_order=index if not reverse else len(rows) - index,
        )
    _apply_placeholder_layout(version, list(version.nodes.all()))
    return version


def test_the_report_does_not_follow_node_or_relation_row_order():
    """The same graph in both insertion orders must produce the same report."""
    node_type = structural_type()
    relation_types = {
        "peer-of": _relation_type("peer-of", hierarchy_role=True, directed_default=False),
        "sibling-of": _relation_type("sibling-of", hierarchy_role=True, directed_default=False),
        "related-to": _relation_type(
            "related-to", hierarchy_role=False, directed_default=False, overridable_direction=False
        ),
    }
    ascending = _permutation_graph(node_type, relation_types, reverse=False)
    descending = _permutation_graph(node_type, relation_types, reverse=True)

    first, second = validate_version(ascending), validate_version(descending)
    assert first.to_dict() == second.to_dict()
    assert set(first.blocking_codes()) == {
        "DUPLICATE_RELATION",
        "HIERARCHY_CYCLE",
        "DIRECTION_NOT_OVERRIDABLE",
    }
    assert set(first.warning_codes()) == {
        "ISOLATED_NODE",
        "NO_INBOUND_RELATIONS",
        "NO_OUTBOUND_RELATIONS",
    }
    assert first.blocking_codes() == sorted(first.blocking_codes())


# ---------------------------------------------------------------------------
# Sharing canonical resolutions (Task 10 review INFO: 133/333/413 queries)
# ---------------------------------------------------------------------------


def _canonical_graph(size: int):
    """A version whose visible nodes each reference one missing canonical locale.

    The reviewer's construction: every node has a canonical key with no row at
    all and only the EN locale overridden — so ``validate_canonical_refs``
    resolves both locales and checks FA's existence once per node, while
    ``validate_locale_projection`` resolves the missing FA locale again. That
    duplicate is what sharing removes.
    """
    version = _version(status="draft", label=f"task12-canonical-{size}")
    node_type = _node_type(key="research-area", **dict(NODE_TYPE_DEFAULTS))
    for index in range(size):
        node = _node(
            version=version,
            node_type=node_type,
            public_key=f"research-area-{index:08x}",
            canonical_translation_key=uuid4(),
        )
        node.translations.create(locale="en", label_override="label en")
    return version


def test_the_report_resolves_every_node_once_and_shares_the_result(monkeypatch):
    size = 12
    version = _canonical_graph(size)

    calls = []
    real = validation.resolve_canonical

    def counting(source, translation_key, locale):
        calls.append((source, translation_key, locale))
        return real(source, translation_key, locale)

    monkeypatch.setattr(validation, "resolve_canonical", counting)

    naive = naive_blocking(version)
    naive_calls = len(calls)
    del calls[:]

    report = validate_version(version)
    shared_calls = len(calls)

    assert shared_calls == 2 * size, "one resolution per locale per node, computed once"
    assert naive_calls == 3 * size, "the naive shape resolves FA twice (parity and refs)"
    canonical_codes = {
        "MISSING_LOCALE_PROJECTION",
        "GROUP_LOCALE_MISSING",
        "CANONICAL_SOURCE_MISSING",
        "CANONICAL_SOURCE_UNPUBLISHED",
        "AMBIGUOUS_CANONICAL_REF",
    }
    assert {
        (issue.code, issue.node_key, issue.group_key)
        for issue in report.blocking
        if issue.code in canonical_codes
    } == {
        (issue.code, issue.node_key, issue.group_key)
        for issue in naive
        if issue.code in canonical_codes
    }
    # A partial shared map never changes a verdict: the validator falls back to
    # computing the facts of the nodes the map does not carry.
    assert validate_canonical_refs(version, facts={}) == validate_canonical_refs(version)
    assert validate_locale_projection(version, facts={}) == validate_locale_projection(version)


def test_sharing_resolutions_does_not_change_a_single_verdict_including_ambiguity():
    """A locale's ambiguity and a locale's unpublished row both survive sharing."""
    version = _version(status="draft", label="task12-ambiguity")
    area_type = _node_type(key="research-area", **dict(NODE_TYPE_DEFAULTS))
    ambiguous = _seed_pair(ResearchTopic, uuid4(), en={"summary": "EN"}, fa={"summary": "FA"})
    ResearchTopic.objects.create(
        translation_key=ambiguous[0].translation_key,
        locale="fa",
        title="Second FA twin",
        slug="second-fa-twin",
        status="published",
        published_at=ambiguous[1].published_at,
    )
    unpublished = _seed_pair(
        ResearchTopic, uuid4(), en={"summary": "EN"}, fa={"status": "draft", "published_at": None}
    )
    _node(
        version=version,
        node_type=area_type,
        public_key="research-area-00000001",
        canonical_translation_key=ambiguous[0].translation_key,
    )
    _node(
        version=version,
        node_type=area_type,
        public_key="research-area-00000002",
        canonical_translation_key=unpublished[0].translation_key,
    )
    _apply_placeholder_layout(version, list(version.nodes.all()))

    wanted = {
        "MISSING_LOCALE_PROJECTION",
        "CANONICAL_SOURCE_UNPUBLISHED",
        "AMBIGUOUS_CANONICAL_REF",
    }
    naive = {issue.code for issue in naive_blocking(version) if issue.code in wanted}
    shared = {issue.code for issue in validate_version(version).blocking if issue.code in wanted}
    assert naive == wanted
    assert shared == naive


def test_the_report_costs_fewer_queries_than_the_naive_aggregation():
    """Recorded numbers: the naive shape's duplicate resolution is gone.

    The construction is the reviewer's (see :func:`_canonical_graph`); both
    counts are measured and the inequality is asserted, so a regression that
    re-adds per-validator resolution fails here rather than being noticed in
    production.
    """
    version = _canonical_graph(30)
    with CaptureQueriesContext(connection) as naive:
        naive_blocking(version)
    with CaptureQueriesContext(connection) as shared:
        validate_version(version)
    assert len(shared.captured_queries) < len(naive.captured_queries)


# ---------------------------------------------------------------------------
# The payload contract check (PAYLOAD_CONTRACT_INVALID and friends)
# ---------------------------------------------------------------------------


def valid_payload() -> dict:
    """The spec §10.2 shape, minimal but complete — two nodes, one relation."""
    return {
        "contractVersion": "atlas01-1.0.0",
        "locale": "en",
        "version": {"id": 7, "revision": "7-x", "nodeCount": 2, "relationCount": 1},
        "nodeTypes": [{"key": "research-area", "label": "Research area"}],
        "relationTypes": [{"key": "related-to", "label": "related"}],
        "groups": [
            {"key": "group-00000001", "label": "Group", "nodeKeys": ["research-area-00000001"]}
        ],
        "nodes": [
            {
                "key": "research-area-00000001",
                "type": "research-area",
                "label": "One",
                "position": {"x": 1.0, "y": 2.0, "z": 3.0},
            },
            {
                "key": "research-area-00000002",
                "type": "research-area",
                "label": "Two",
                "position": {"x": 0.0, "y": 0.0, "z": 0.0},
            },
        ],
        "relations": [
            {
                "key": "research-area-00000001~related-to~research-area-00000002",
                "type": "related-to",
                "source": "research-area-00000001",
                "target": "research-area-00000002",
                "directed": False,
            }
        ],
    }


def test_a_valid_projection_passes_the_contract_check():
    assert validate_payload_contract(valid_payload()) == []


def test_the_contract_check_reports_a_broken_catalog_reference():
    for mutate in (
        lambda payload: payload["nodes"][0].update(type="nowhere"),
        lambda payload: payload["relations"][0].update(type="nowhere"),
        lambda payload: payload["relations"][0].update(source="nowhere-00000001"),
        lambda payload: payload["relations"][0].update(target="nowhere-00000001"),
        lambda payload: payload["groups"][0].update(nodeKeys=["nowhere-00000001"]),
    ):
        payload = valid_payload()
        mutate(payload)
        issues = validate_payload_contract(payload)
        assert [issue.code for issue in issues] == ["PAYLOAD_CONTRACT_INVALID"]


def test_the_contract_check_reports_key_grammar_and_coordinate_finiteness():
    for mutate in (
        lambda payload: payload["nodes"][0].update(key="Not A Key"),
        lambda payload: payload["groups"][0].update(key="Not A Key"),
        lambda payload: payload["nodes"][0]["position"].update(x=float("nan")),
        lambda payload: payload["nodes"][0]["position"].update(y=float("inf")),
        lambda payload: payload["nodes"][0].update(position="nope"),
        lambda payload: payload["nodes"][0]["position"].pop("z"),
    ):
        payload = valid_payload()
        mutate(payload)
        issues = validate_payload_contract(payload)
        assert "PAYLOAD_CONTRACT_INVALID" in codes(issues)


def test_a_node_without_a_position_is_missing_layout_not_a_broken_contract():
    payload = valid_payload()
    del payload["nodes"][1]["position"]
    issues = validate_payload_contract(payload)
    assert [issue.code for issue in issues] == ["MISSING_LAYOUT"]
    assert issues[0].node_key == "research-area-00000002"


def test_the_contract_check_reports_duplicate_keys_per_collection():
    payload = valid_payload()
    payload["nodes"].append(dict(payload["nodes"][0]))
    payload["version"]["nodeCount"] = 3
    issues = validate_payload_contract(payload)
    assert [issue.code for issue in issues] == ["DUPLICATE_PUBLIC_KEY"]
    assert issues[0].node_key == "research-area-00000001"

    payload = valid_payload()
    payload["groups"].append({"key": "group-00000001", "label": "Other", "nodeKeys": []})
    payload["relations"].append(dict(payload["relations"][0]))
    payload["version"]["relationCount"] = 2
    issues = validate_payload_contract(payload)
    assert [issue.code for issue in issues] == ["DUPLICATE_PUBLIC_KEY"] * 2
    assert codes(issues) == {"DUPLICATE_PUBLIC_KEY"}


def test_a_duplicate_catalog_entry_is_a_contract_defect_not_a_duplicate_key():
    """A catalog is a vocabulary, not a collection of entities (see the check's docstring)."""
    payload = valid_payload()
    payload["nodeTypes"].append(dict(payload["nodeTypes"][0]))
    issues = validate_payload_contract(payload)
    assert [issue.code for issue in issues] == ["PAYLOAD_CONTRACT_INVALID"]

    payload = valid_payload()
    payload["relationTypes"].append(dict(payload["relationTypes"][0]))
    assert [issue.code for issue in validate_payload_contract(payload)] == [
        "PAYLOAD_CONTRACT_INVALID"
    ]


def test_the_contract_check_reports_a_broken_envelope():
    for mutate in (
        lambda payload: payload.pop("relations"),
        lambda payload: payload.pop("nodes"),
        lambda payload: payload.update(locale="de"),
        lambda payload: payload.update(contractVersion=""),
        lambda payload: payload.update(nodes="not a list"),
        lambda payload: payload["version"].update(nodeCount=99),
        lambda payload: payload["version"].update(relationCount=99),
        lambda payload: payload.update(version=None),
    ):
        payload = valid_payload()
        mutate(payload)
        issues = validate_payload_contract(payload)
        assert "PAYLOAD_CONTRACT_INVALID" in codes(issues), payload


def test_the_contract_check_is_total_and_never_raises():
    for payload in (None, [], "nope", {"nodes": "nope"}, {"nodes": [None]}, {"nodes": [{}]}):
        issues = validate_payload_contract(payload)  # type: ignore[arg-type]
        assert codes(issues) <= {
            "PAYLOAD_CONTRACT_INVALID",
            "MISSING_LAYOUT",
            "DUPLICATE_PUBLIC_KEY",
        }


def test_the_contract_check_is_pure_and_does_not_reshape_its_input(django_assert_num_queries):
    payload = valid_payload()
    snapshot = deepcopy(payload)
    with django_assert_num_queries(0):
        assert validate_payload_contract(payload) == []
    assert payload == snapshot

    malformed = valid_payload()
    malformed["nodes"][0]["position"]["x"] = float("nan")
    snapshot = deepcopy(malformed)
    with django_assert_num_queries(0):
        validate_payload_contract(malformed)
    assert malformed == snapshot


def test_the_contract_check_does_not_follow_catalog_or_entity_order():
    payload = valid_payload()
    payload["nodes"].append(dict(payload["nodes"][0]))
    payload["version"]["nodeCount"] = 3
    reversed_payload = deepcopy(payload)
    reversed_payload["nodes"] = list(reversed(reversed_payload["nodes"]))
    reversed_payload["relations"] = list(reversed(reversed_payload["relations"]))
    assert validate_payload_contract(payload) == validate_payload_contract(reversed_payload)


def test_the_contract_codes_are_blocking_codes_that_never_appear_in_a_version_report():
    """The payload gate is the serving-time gate; ``validate_version`` is the publish gate."""
    assert {"PAYLOAD_CONTRACT_INVALID", "DUPLICATE_PUBLIC_KEY"} <= set(BLOCKING_CODES)
    version, _nodes = small_version(label="task12-gates", node_keys=("structural-00000001",))
    reported = codes(validate_version(version).blocking)
    assert "PAYLOAD_CONTRACT_INVALID" not in reported
    assert "DUPLICATE_PUBLIC_KEY" not in reported


def test_the_contract_check_orders_its_issues_like_the_report():
    payload = valid_payload()
    del payload["nodes"][1]["position"]
    payload["nodes"].append(dict(payload["nodes"][0]))
    payload["version"]["nodeCount"] = 3
    issues = validate_payload_contract(payload)
    assert issues == sorted(
        issues,
        key=lambda issue: (
            issue.code,
            issue.relation_key or "",
            issue.node_key or "",
            issue.group_key or "",
        ),
    )
    assert all(issue.message_token == expected_token(issue.code) for issue in issues)
