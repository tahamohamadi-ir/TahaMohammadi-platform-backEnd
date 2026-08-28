"""Pure graph payload validator tests (Track AB-06) - table-driven, no DB.

Every code in ``GRAPH_ISSUE_CODES`` must be triggered by at least one case,
every emitted code must be a registered code, and every issue must carry the
``{code, nodeId?, edgeId?, messageToken}`` shape.
"""

import pytest

from apps.api.admin_graph_validate import (
    GRAPH_ISSUE_CODES,
    edge_public_id,
    validate_graph_payload,
)


def _node(node_id="n1", **overrides):
    node = {
        "id": node_id,
        "type": "concept",
        "label": "Node",
        "accessibleLabel": "Node accessible",
        "colorRole": "brand",
        "iconRole": "dot",
        "weight": 1,
        "position": {"x": 0.0, "y": 0.0},
        "relatedRecords": [],
    }
    node.update(overrides)
    return node


def _edge(source="a", target="b", **overrides):
    edge = {
        "id": "",
        "source": source,
        "target": target,
        "relationType": "relates-to",
        "directed": True,
        "weight": 0,
    }
    edge.update(overrides)
    return edge


def _payload(nodes=None, edges=None):
    return {"nodes": list(nodes or []), "edges": list(edges or [])}


def _ok_resolver(family, record_id):
    return True


def _failing_resolver(family, record_id):
    return False


def _codes(issues):
    return [issue["code"] for issue in issues]


# One triggering payload per registered code (BROKEN_RELATED pairs with the
# failing resolver; every other case is resolver-independent).
TRIGGER_CASES: dict[str, dict] = {
    "DUPLICATE_NODE_ID": _payload(
        nodes=[_node("dup"), _node("other"), _node("dup")],
    ),
    "SELF_EDGE": _payload(
        nodes=[_node("a"), _node("b")],
        edges=[_edge(source="a", target="a", id="self-1")],
    ),
    "DUPLICATE_EDGE": _payload(
        edges=[_edge(id="e1"), _edge(id="e2")],
    ),
    "BROKEN_RELATED": _payload(
        nodes=[_node("a", relatedRecords=[{"family": "article", "id": "404"}])],
    ),
    "MISSING_ACCESSIBLE_LABEL": _payload(
        nodes=[_node("a", accessibleLabel="")],
    ),
    "BAD_WEIGHT": _payload(
        nodes=[_node("a", weight=2)],
    ),
    "MISSING_POSITION": _payload(
        nodes=[_node("a", position={"x": 1})],
    ),
}


@pytest.mark.parametrize("code", GRAPH_ISSUE_CODES)
def test_every_registered_code_is_triggered_by_its_case(code):
    issues = validate_graph_payload(
        TRIGGER_CASES[code], related_resolver=_failing_resolver
    )
    assert code in _codes(issues)


def test_graph_issue_codes_are_the_exact_stable_strings():
    assert GRAPH_ISSUE_CODES == [
        "DUPLICATE_NODE_ID",
        "SELF_EDGE",
        "DUPLICATE_EDGE",
        "BROKEN_RELATED",
        "MISSING_ACCESSIBLE_LABEL",
        "BAD_WEIGHT",
        "MISSING_POSITION",
    ]


def test_valid_payload_is_clean():
    payload = _payload(
        nodes=[_node("a"), _node("b")],
        edges=[_edge(id="e1", source="a", target="b")],
    )
    assert validate_graph_payload(payload, related_resolver=_ok_resolver) == []


def test_empty_payload_is_clean():
    assert validate_graph_payload({}, related_resolver=_ok_resolver) == []


def test_issues_only_contain_registered_codes_and_shape():
    for case in TRIGGER_CASES.values():
        issues = validate_graph_payload(case, related_resolver=_failing_resolver)
        assert issues, "case must produce at least one issue"
        for issue in issues:
            assert issue["code"] in GRAPH_ISSUE_CODES
            assert set(issue) <= {"code", "nodeId", "edgeId", "messageToken"}
            assert issue["messageToken"]
            assert issue["messageToken"].startswith("graph.")


def test_duplicate_node_id_reports_the_repeated_id():
    issues = validate_graph_payload(
        _payload(nodes=[_node("dup"), _node("dup")]), related_resolver=_ok_resolver
    )
    assert _codes(issues) == ["DUPLICATE_NODE_ID"]
    assert issues[0]["nodeId"] == "dup"


@pytest.mark.parametrize(
    "accessible",
    ["", "   ", None],
)
def test_missing_accessible_label_variants(accessible):
    node = _node("a")
    if accessible is None:
        del node["accessibleLabel"]
    else:
        node["accessibleLabel"] = accessible
    issues = validate_graph_payload(_payload(nodes=[node]), related_resolver=_ok_resolver)
    assert _codes(issues) == ["MISSING_ACCESSIBLE_LABEL"]
    assert issues[0]["nodeId"] == "a"


@pytest.mark.parametrize(
    "weight",
    [1.5, -0.1, 2, "x", True, None, 0.5],
)
def test_bad_weight_variants(weight):
    issues = validate_graph_payload(
        _payload(nodes=[_node("a", weight=weight)]), related_resolver=_ok_resolver
    )
    assert _codes(issues) == ["BAD_WEIGHT"]
    assert issues[0]["nodeId"] == "a"


@pytest.mark.parametrize("weight", [0, 1])
def test_integral_zero_and_one_weights_are_valid(weight):
    assert (
        validate_graph_payload(
            _payload(nodes=[_node("a", weight=weight)]), related_resolver=_ok_resolver
        )
        == []
    )


def test_bad_weight_on_edge_reports_edge_id():
    issues = validate_graph_payload(
        _payload(edges=[_edge(id="heavy", weight=3)]), related_resolver=_ok_resolver
    )
    assert _codes(issues) == ["BAD_WEIGHT"]
    assert issues[0]["edgeId"] == "heavy"


@pytest.mark.parametrize(
    "position",
    [None, {}, {"y": 1}, {"x": "left", "y": 2}, {"x": 1, "y": 2, "z": "up"}],
)
def test_missing_position_variants(position):
    issues = validate_graph_payload(
        _payload(nodes=[_node("a", position=position)]), related_resolver=_ok_resolver
    )
    assert _codes(issues) == ["MISSING_POSITION"]
    assert issues[0]["nodeId"] == "a"


@pytest.mark.parametrize(
    "position",
    [{"x": 0, "y": -1.5}, {"x": 1, "y": 2, "z": 3}, {"x": 1, "y": 2, "z": None}],
)
def test_valid_position_variants(position):
    assert (
        validate_graph_payload(
            _payload(nodes=[_node("a", position=position)]), related_resolver=_ok_resolver
        )
        == []
    )


def test_self_edge_reports_edge_id():
    issues = validate_graph_payload(
        _payload(edges=[_edge(id="loop", source="a", target="a")]),
        related_resolver=_ok_resolver,
    )
    assert _codes(issues) == ["SELF_EDGE"]
    assert issues[0]["edgeId"] == "loop"


def test_duplicate_edge_reports_second_edge_id():
    issues = validate_graph_payload(
        _payload(edges=[_edge(id="e1"), _edge(id="e2")]), related_resolver=_ok_resolver
    )
    assert _codes(issues) == ["DUPLICATE_EDGE"]
    assert issues[0]["edgeId"] == "e2"


def test_duplicate_edge_falls_back_to_composed_public_id():
    issues = validate_graph_payload(
        _payload(edges=[_edge(id=""), _edge(id="")]), related_resolver=_ok_resolver
    )
    assert _codes(issues) == ["DUPLICATE_EDGE"]
    assert issues[0]["edgeId"] == edge_public_id("a", "b", "relates-to")


def test_swapped_undirected_pair_is_duplicate():
    issues = validate_graph_payload(
        _payload(
            edges=[
                _edge(id="e1", directed=False),
                _edge(id="e2", source="b", target="a", directed=False),
            ]
        ),
        related_resolver=_ok_resolver,
    )
    assert _codes(issues) == ["DUPLICATE_EDGE"]


def test_swapped_directed_edges_are_allowed():
    issues = validate_graph_payload(
        _payload(
            edges=[
                _edge(id="e1", directed=True),
                _edge(id="e2", source="b", target="a", directed=True),
            ]
        ),
        related_resolver=_ok_resolver,
    )
    assert issues == []


def test_swapped_undirected_with_different_relation_is_allowed():
    issues = validate_graph_payload(
        _payload(
            edges=[
                _edge(id="e1", directed=False, relationType="relates-to"),
                _edge(id="e2", source="b", target="a", directed=False, relationType="contrasts"),
            ]
        ),
        related_resolver=_ok_resolver,
    )
    assert issues == []


def test_undirected_then_directed_same_pair_is_duplicate():
    # same direction pair -> the exact (source, target, relation) key repeats.
    issues = validate_graph_payload(
        _payload(edges=[_edge(id="e1", directed=False), _edge(id="e2", directed=True)]),
        related_resolver=_ok_resolver,
    )
    assert _codes(issues) == ["DUPLICATE_EDGE"]


def test_broken_related_reports_node_id_and_resolver_args():
    calls: list[tuple[str, str]] = []

    def spy(family, record_id):
        calls.append((family, record_id))
        return False

    issues = validate_graph_payload(
        _payload(
            nodes=[_node("a", relatedRecords=[{"family": "article", "id": "404"}])]
        ),
        related_resolver=spy,
    )
    assert _codes(issues) == ["BROKEN_RELATED"]
    assert issues[0]["nodeId"] == "a"
    assert calls == [("article", "404")]


def test_malformed_related_entry_is_broken_without_calling_resolver():
    calls: list[tuple[str, str]] = []

    def spy(family, record_id):
        calls.append((family, record_id))
        return True

    issues = validate_graph_payload(
        _payload(nodes=[_node("a", relatedRecords=[{"family": 123, "id": "1"}])]),
        related_resolver=spy,
    )
    assert _codes(issues) == ["BROKEN_RELATED"]
    assert calls == []


def test_published_related_record_passes():
    payload = _payload(
        nodes=[
            _node(
                "a",
                relatedRecords=[
                    {"family": "article", "id": "7"},
                    {"family": "project", "id": "9"},
                ],
            )
        ]
    )
    assert validate_graph_payload(payload, related_resolver=_ok_resolver) == []


def test_edge_public_id_composition():
    assert edge_public_id("root", "leaf", "relates-to") == "root->leaf:relates-to"


def test_graph_related_families_match_the_hardcoded_wire_table():
    """AB-07 cross-check: the AB family table (apps/api/admin_common.py)
    equals the expected lowercase ``content_type.model`` wire strings the
    BK-05 public read serves (one payload everywhere)."""
    from apps.api.admin_common import GRAPH_RELATED_FAMILIES
    from apps.content.models import (
        Article,
        Book,
        Download,
        Landing,
        Profile,
        Project,
        Publication,
        ResearchStatement,
        ResearchTopic,
        Series,
        Talk,
    )

    assert GRAPH_RELATED_FAMILIES == {
        "landing": Landing,
        "profile": Profile,
        "article": Article,
        "series": Series,
        "researchtopic": ResearchTopic,
        "researchstatement": ResearchStatement,
        "project": Project,
        "publication": Publication,
        "book": Book,
        "talk": Talk,
        "download": Download,
    }
