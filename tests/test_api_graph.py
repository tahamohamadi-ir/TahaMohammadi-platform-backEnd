"""Public graph API tests (BK-05) - active-version projection, fail-closed, camelCase."""

from datetime import timedelta

import pytest
from django.contrib.contenttypes.models import ContentType
from django.test import Client
from django.utils import timezone

from apps.content.models import (
    Article,
    GraphEdge,
    GraphNode,
    GraphNodeRelated,
    GraphVersion,
    GraphVersionStatus,
    LifecycleStatus,
    Locale,
    TopicTag,
)

TOP_LEVEL_KEYS = {"nodes", "edges"}
FULL_NODE_KEYS = {
    "id",
    "type",
    "label",
    "summary",
    "accessibleLabel",
    "colorRole",
    "iconRole",
    "weight",
    "position",
    "relatedRecords",
}
MINIMAL_NODE_KEYS = {
    "id",
    "type",
    "label",
    "accessibleLabel",
    "weight",
    "relatedRecords",
}
EDGE_KEYS = {
    "id",
    "source",
    "target",
    "relationType",
    "directed",
    "weight",
    "explanation",
}
FORBIDDEN_KEYS = {
    "groups",
    "group",
    "groupId",
    "status",
    "locale",
    "version",
    "versionId",
    "created_at",
    "updated_at",
    "node_id",
    "pos_x",
    "pos_y",
    "pos_z",
    "color_role",
    "icon_role",
    "accessible_label",
    "related_records",
    "relation_type",
    "content_type",
    "object_id",
    "pk",
}

_pair_counter = {"n": 0}


def past():
    return timezone.now() - timedelta(days=1)


def make_version(locale, status=GraphVersionStatus.DRAFT):
    return GraphVersion.objects.create(locale=locale, status=status)


def make_node(version, node_id, **overrides):
    defaults = {
        "version": version,
        "node_id": node_id,
        "label": node_id.replace("-", " ").title(),
        "type": "concept",
        "color_role": "brand",
        "icon_role": "dot",
    }
    defaults.update(overrides)
    return GraphNode.objects.create(**defaults)


def make_edge(version, source, target, **overrides):
    defaults = {
        "version": version,
        "source": source,
        "target": target,
        "relation_type": "relates-to",
    }
    defaults.update(overrides)
    return GraphEdge.objects.create(**defaults)


def make_article(locale=Locale.FA, **overrides):
    _pair_counter["n"] += 1
    n = _pair_counter["n"]
    defaults = {
        "locale": locale,
        "slug": f"graph-api-fixture-{n}",
        "title": f"Graph API fixture {n}",
        "status": LifecycleStatus.PUBLISHED,
        "published_at": past(),
    }
    defaults.update(overrides)
    return Article.objects.create(**defaults)


def make_related(node, obj):
    return GraphNodeRelated.objects.create(
        node=node,
        content_type=ContentType.objects.get_for_model(type(obj)),
        object_id=obj.pk,
    )


@pytest.fixture
def api_client():
    return Client()


def assert_json(response, status_code):
    assert response.status_code == status_code
    assert response["content-type"].startswith("application/json")
    return response.json()


@pytest.fixture
def full_graph(db):
    """fa active version: one full node + one minimal node + two edges."""
    version = make_version(Locale.FA, GraphVersionStatus.ACTIVE)
    full = make_node(
        version,
        "research-fit",
        summary="Research directions that fit the profile.",
        accessible_label="Research fit",
        color_role="brand",
        icon_role="star",
        weight=4,
        pos_x=1.5,
        pos_y=-2.0,
        pos_z=3.25,
    )
    minimal = make_node(
        version,
        "identity",
        label="Identity",
        type="identity",
        summary="",
        accessible_label="",
        color_role="",
        icon_role="",
        weight=1,
    )
    make_edge(
        version,
        full,
        minimal,
        relation_type="relates-to",
        directed=True,
        weight=3,
        explanation="Both drive the research agenda.",
    )
    make_edge(
        version,
        minimal,
        full,
        relation_type="supports",
        directed=False,
        weight=0,
        explanation="",
    )
    return version


def test_active_fa_full_shape_exact_camel_keys(api_client, full_graph):
    data = assert_json(api_client.get("/api/graph/fa"), 200)
    assert set(data) == TOP_LEVEL_KEYS
    assert "groups" not in data
    assert [n["id"] for n in data["nodes"]] == ["identity", "research-fit"]
    minimal, full = data["nodes"]
    assert set(full) == FULL_NODE_KEYS
    assert set(minimal) == MINIMAL_NODE_KEYS
    assert FORBIDDEN_KEYS.isdisjoint(full) and FORBIDDEN_KEYS.isdisjoint(minimal)
    assert full["summary"].startswith("Research directions")
    assert full["accessibleLabel"] == "Research fit"
    assert full["colorRole"] == "brand"
    assert full["iconRole"] == "star"
    assert full["weight"] == 4
    assert full["position"] == {"x": 1.5, "y": -2.0, "z": 3.25}
    assert minimal["accessibleLabel"] == ""
    assert minimal["weight"] == 1
    assert minimal["relatedRecords"] == []


def test_blank_optional_node_keys_are_omitted(api_client, full_graph):
    data = assert_json(api_client.get("/api/graph/fa"), 200)
    minimal = data["nodes"][0]
    for absent in ("summary", "colorRole", "iconRole", "position"):
        assert absent not in minimal


def test_edge_shape_and_blank_explanation_omitted(api_client, full_graph):
    data = assert_json(api_client.get("/api/graph/fa"), 200)
    by_relation = {edge["relationType"]: edge for edge in data["edges"]}
    assert set(by_relation) == {"relates-to", "supports"}
    full = by_relation["relates-to"]
    minimal = by_relation["supports"]
    assert set(full) == EDGE_KEYS
    assert set(minimal) == EDGE_KEYS - {"explanation"}
    assert FORBIDDEN_KEYS.isdisjoint(full) and FORBIDDEN_KEYS.isdisjoint(minimal)
    assert full["explanation"].startswith("Both drive")
    assert full["directed"] is True
    assert full["weight"] == 3
    assert minimal["directed"] is False
    assert minimal["weight"] == 0


def test_stable_edge_id_composition(api_client, full_graph):
    """Edge id is content-derived ({source}->{target}:{relation}), never pk-based."""
    data = assert_json(api_client.get("/api/graph/fa"), 200)
    by_relation = {edge["relationType"]: edge for edge in data["edges"]}
    assert by_relation["relates-to"]["id"] == "research-fit->identity:relates-to"
    assert by_relation["supports"]["id"] == "identity->research-fit:supports"
    assert by_relation["relates-to"]["source"] == "research-fit"
    assert by_relation["relates-to"]["target"] == "identity"


def test_position_z_omitted_when_unpinned(api_client, db):
    version = make_version(Locale.FA, GraphVersionStatus.ACTIVE)
    make_node(version, "identity", pos_x=1.5, pos_y=-2.0)
    make_node(version, "work")
    data = assert_json(api_client.get("/api/graph/fa"), 200)
    by_id = {n["id"]: n for n in data["nodes"]}
    assert by_id["identity"]["position"] == {"x": 1.5, "y": -2.0}
    assert "position" not in by_id["work"]


@pytest.fixture
def related_graph(db):
    """fa active version, one node pointing at published/draft/dangling/gated rows."""
    version = make_version(Locale.FA, GraphVersionStatus.ACTIVE)
    node = make_node(version, "self")
    published = make_related(node, make_article(locale=Locale.FA))
    make_related(node, make_article(locale=Locale.FA, status=LifecycleStatus.DRAFT))
    GraphNodeRelated.objects.create(
        node=node,
        content_type=ContentType.objects.get_for_model(Article),
        object_id=999_999,
    )
    make_related(
        node,
        TopicTag.objects.create(name="Graph tag", slug="graph-tag", locale=Locale.FA),
    )
    return version, published


def test_related_records_published_only(api_client, related_graph):
    _, published = related_graph
    data = assert_json(api_client.get("/api/graph/fa"), 200)
    node = data["nodes"][0]
    assert node["relatedRecords"] == [{"family": "article", "id": str(published.pk)}]


def test_draft_version_never_served(api_client, db):
    version = make_version(Locale.FA)
    make_node(version, "identity")
    data = assert_json(api_client.get("/api/graph/fa"), 404)
    assert "detail" in data


def test_demoted_active_version_not_served(api_client, db):
    version = make_version(Locale.FA, GraphVersionStatus.ACTIVE)
    make_node(version, "identity")
    version.status = GraphVersionStatus.DRAFT
    version.save()
    assert_json(api_client.get("/api/graph/fa"), 404)


def test_draft_version_rows_never_leak_into_active_payload(api_client, db):
    active = make_version(Locale.FA, GraphVersionStatus.ACTIVE)
    make_node(active, "identity")
    draft = make_version(Locale.FA)
    make_node(draft, "secret-draft-node")
    data = assert_json(api_client.get("/api/graph/fa"), 200)
    assert [n["id"] for n in data["nodes"]] == ["identity"]


def test_fa_en_isolation(api_client, db):
    fa = make_version(Locale.FA, GraphVersionStatus.ACTIVE)
    make_node(fa, "identity")
    en = make_version(Locale.EN, GraphVersionStatus.ACTIVE)
    make_node(en, "work")
    fa_data = assert_json(api_client.get("/api/graph/fa"), 200)
    en_data = assert_json(api_client.get("/api/graph/en"), 200)
    assert [n["id"] for n in fa_data["nodes"]] == ["identity"]
    assert [n["id"] for n in en_data["nodes"]] == ["work"]


def test_invalid_locale_returns_404(api_client, db):
    response = api_client.get("/api/graph/xx")
    assert response.status_code == 404
    assert response["content-type"].startswith("application/json")
    assert "detail" in response.json()
    assert "Traceback" not in response.text


def test_active_version_without_rows_returns_empty_lists(api_client, db):
    """404 fires only when no active version exists, not on an empty graph."""
    make_version(Locale.FA, GraphVersionStatus.ACTIVE)
    data = assert_json(api_client.get("/api/graph/fa"), 200)
    assert data == {"nodes": [], "edges": []}
