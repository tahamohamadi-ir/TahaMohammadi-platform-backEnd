"""Admin graph authoring API tests (Track AB-05).

Covers /api/v1/admin/graph/*: the full lifecycle (create -> PUT payload ->
validation 400 -> fix -> PUT 200 -> activate 200 -> previous archived ->
GET versions counts), the If-Match precondition (428 missing / 409 stale),
draft-only guards (409 IMMUTABLE_ACTIVE on PUT, 409 ALREADY_ACTIVE on
activate), VALIDATION_BLOCKED activation via a real unpublished Article
related record, the auth matrix (anonymous 401, non-staff 403, OTP, CSRF),
and exact camelCase GET payloads.
"""

import json

import pytest
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.test import Client
from django.utils import timezone

from apps.content.models import (
    Article,
    GraphNode,
    GraphNodeRelated,
    GraphVersion,
    LifecycleStatus,
    Locale,
)
from apps.security.models import AuditLog

BASE = "/api/v1/admin/graph"

_pair_counter = {"n": 0}


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def totp_device(db, admin_user):
    from django_otp.plugins.otp_totp.models import TOTPDevice

    return TOTPDevice.objects.create(user=admin_user, name="default", confirmed=True)


@pytest.fixture
def csrf_client():
    """CSRF-enforcing client used for the enforcement tests (no token set)."""
    return Client(enforce_csrf_checks=True)


@pytest.fixture
def admin_api_client(csrf_client, admin_user, totp_device):
    """Authenticated staff client with a verified OTP session and CSRF token."""
    csrf_client.force_login(admin_user)
    session = csrf_client.session
    session["otp_device_id"] = totp_device.persistent_id
    session.save()
    token = csrf_client.get("/api/v1/admin/auth/csrf").json()["csrfToken"]
    csrf_client.defaults["HTTP_X_CSRFTOKEN"] = token
    return csrf_client


@pytest.fixture
def staff_api_client(csrf_client, user):
    """Authenticated NON-staff client (forbidden per the admin auth matrix)."""
    csrf_client.force_login(user)
    return csrf_client


def past() -> timezone.datetime:
    return timezone.now() - timezone.timedelta(days=1)


def make_article(**overrides) -> Article:
    _pair_counter["n"] += 1
    n = _pair_counter["n"]
    defaults = {
        "locale": Locale.FA,
        "slug": f"graph-admin-fixture-{n}",
        "title": f"Graph admin fixture {n}",
        "status": LifecycleStatus.PUBLISHED,
        "published_at": past(),
    }
    defaults.update(overrides)
    return Article.objects.create(**defaults)


def _node(node_id="root", **overrides):
    node = {
        "id": node_id,
        "type": "concept",
        "label": node_id.replace("-", " ").title(),
        "accessibleLabel": f"{node_id} accessible",
        "colorRole": "brand",
        "iconRole": "dot",
        "weight": 0,
        "position": {"x": 0.0, "y": 0.0},
        "relatedRecords": [],
    }
    node.update(overrides)
    return node


def _edge(source="root", target="leaf", **overrides):
    edge = {
        "source": source,
        "target": target,
        "relationType": "relates-to",
        "directed": True,
        "weight": 0,
    }
    edge.update(overrides)
    return edge


def _create_version(client, locale="fa"):
    return client.post(
        f"{BASE}/versions",
        data=json.dumps({"locale": locale}),
        content_type="application/json",
    )


def _put_payload(client, version_id, payload, *, if_match=None):
    headers = {}
    if if_match is not None:
        headers["HTTP_IF_MATCH"] = if_match
    return client.put(
        f"{BASE}/versions/{version_id}/payload",
        data=json.dumps(payload),
        content_type="application/json",
        **headers,
    )


def _activate(client, version_id):
    return client.post(f"{BASE}/versions/{version_id}/activate")


def _detail(client, version_id):
    return client.get(f"{BASE}/versions/{version_id}")


def assert_json(response, status_code):
    assert response.status_code == status_code, response.content
    assert response["content-type"].startswith("application/json")
    return response.json()


def test_full_lifecycle_create_put_activate_archive(admin_api_client):
    created = assert_json(_create_version(admin_api_client, "fa"), 201)
    version_id = created["id"]
    assert created["status"] == "draft"
    assert created["nodeCount"] == 0 and created["edgeCount"] == 0

    listing = assert_json(admin_api_client.get(f"{BASE}/versions"), 200)
    assert set(listing[0]) == {
        "id",
        "locale",
        "status",
        "createdAt",
        "updatedAt",
        "nodeCount",
        "edgeCount",
    }
    assert [row["id"] for row in listing] == [version_id]

    detail = assert_json(_detail(admin_api_client, version_id), 200)
    payload = {
        "nodes": [_node("root"), _node("leaf")],
        "edges": [_edge(source="root", target="leaf")],
    }
    saved = assert_json(
        _put_payload(admin_api_client, version_id, payload, if_match=detail["updatedAt"]),
        200,
    )
    assert set(saved) == {"revision"}
    assert saved["revision"].endswith("Z")

    after = assert_json(_detail(admin_api_client, version_id), 200)
    assert after["updatedAt"] == saved["revision"]
    assert [node["id"] for node in after["nodes"]] == ["leaf", "root"]

    listing = assert_json(admin_api_client.get(f"{BASE}/versions"), 200)
    assert listing[0]["nodeCount"] == 2 and listing[0]["edgeCount"] == 1

    activated = assert_json(_activate(admin_api_client, version_id), 200)
    assert activated == {"id": version_id, "status": "active"}
    assert AuditLog.objects.filter(
        action="graph.activate", model_name="graph", object_id=str(version_id)
    ).count() == 1

    second = assert_json(_create_version(admin_api_client, "fa"), 201)
    second_detail = assert_json(_detail(admin_api_client, second["id"]), 200)
    assert_json(
        _put_payload(
            admin_api_client,
            second["id"],
            payload,
            if_match=second_detail["updatedAt"],
        ),
        200,
    )
    assert_json(_activate(admin_api_client, second["id"]), 200)

    statuses = {
        row["id"]: row["status"]
        for row in assert_json(admin_api_client.get(f"{BASE}/versions"), 200)
    }
    assert statuses[version_id] == "draft"
    assert statuses[second["id"]] == "active"


def test_post_versions_unknown_locale_404(admin_api_client):
    data = assert_json(_create_version(admin_api_client, "xx"), 404)
    assert data["code"] == "NOT_FOUND"


def test_post_versions_allows_multiple_drafts_per_locale(admin_api_client):
    first = assert_json(_create_version(admin_api_client, "fa"), 201)
    second = assert_json(_create_version(admin_api_client, "fa"), 201)
    assert first["id"] != second["id"]
    assert first["status"] == second["status"] == "draft"


def test_put_roundtrip_exact_camel_keys(admin_api_client):
    article = make_article()
    created = assert_json(_create_version(admin_api_client, "fa"), 201)
    payload = {
        "nodes": [
            {
                "id": "root",
                "type": "concept",
                "label": "Root",
                "summary": "Root summary",
                "accessibleLabel": "Root accessible",
                "colorRole": "brand",
                "iconRole": "dot",
                "weight": 1,
                "position": {"x": 1.5, "y": -2.0, "z": 3.0},
                "relatedRecords": [{"family": "article", "id": str(article.pk)}],
            },
            _node("leaf"),
        ],
        "edges": [
            {
                "source": "root",
                "target": "leaf",
                "relationType": "relates-to",
                "directed": True,
                "weight": 0,
                "explanation": "because",
            }
        ],
        "groups": [{"name": "Core", "nodeIds": ["root"]}],
    }
    detail = assert_json(_detail(admin_api_client, created["id"]), 200)
    assert_json(
        _put_payload(admin_api_client, created["id"], payload, if_match=detail["updatedAt"]),
        200,
    )
    data = assert_json(_detail(admin_api_client, created["id"]), 200)
    assert set(data) == {
        "id",
        "locale",
        "status",
        "createdAt",
        "updatedAt",
        "nodes",
        "edges",
        "groups",
    }
    # GET orders nodes by node_id: "leaf" < "root".
    assert data["nodes"][0] == {
        "id": "leaf",
        "type": "concept",
        "label": "Leaf",
        "accessibleLabel": "leaf accessible",
        "colorRole": "brand",
        "iconRole": "dot",
        "weight": 0,
        "position": {"x": 0.0, "y": 0.0},
        "relatedRecords": [],
    }
    assert data["nodes"][1] == {
        "id": "root",
        "type": "concept",
        "label": "Root",
        "summary": "Root summary",
        "accessibleLabel": "Root accessible",
        "colorRole": "brand",
        "iconRole": "dot",
        "weight": 1,
        "position": {"x": 1.5, "y": -2.0, "z": 3.0},
        "relatedRecords": [{"family": "article", "id": str(article.pk)}],
    }
    assert data["edges"][0] == {
        "id": "root->leaf:relates-to",
        "source": "root",
        "target": "leaf",
        "relationType": "relates-to",
        "directed": True,
        "weight": 0,
        "explanation": "because",
    }
    assert data["groups"] == [{"name": "Core", "nodeIds": ["root"]}]


def test_put_validation_issues_400_is_atomic(admin_api_client):
    created = assert_json(_create_version(admin_api_client, "fa"), 201)
    good = {"nodes": [_node("a")], "edges": []}
    detail = assert_json(_detail(admin_api_client, created["id"]), 200)
    assert_json(
        _put_payload(admin_api_client, created["id"], good, if_match=detail["updatedAt"]),
        200,
    )
    before = assert_json(_detail(admin_api_client, created["id"]), 200)
    broken = {
        "nodes": [_node("dup"), _node("dup", accessibleLabel="")],
        "edges": [],
    }
    data = assert_json(
        _put_payload(
            admin_api_client, created["id"], broken, if_match=before["updatedAt"]
        ),
        400,
    )
    assert set(data) == {"issues"}
    codes = {issue["code"] for issue in data["issues"]}
    assert {"DUPLICATE_NODE_ID", "MISSING_ACCESSIBLE_LABEL"} <= codes
    after = assert_json(_detail(admin_api_client, created["id"]), 200)
    assert [node["id"] for node in after["nodes"]] == ["a"]
    assert after["updatedAt"] == before["updatedAt"]


def test_put_bad_weight_rejected(admin_api_client):
    created = assert_json(_create_version(admin_api_client, "fa"), 201)
    detail = assert_json(_detail(admin_api_client, created["id"]), 200)
    payload = {"nodes": [_node("a", weight=2)], "edges": []}
    data = assert_json(
        _put_payload(admin_api_client, created["id"], payload, if_match=detail["updatedAt"]),
        400,
    )
    assert data["issues"] == [
        {
            "code": "BAD_WEIGHT",
            "nodeId": "a",
            "messageToken": "graph.badWeight",
        }
    ]


def test_activate_blocked_by_unpublished_related_then_passes(admin_api_client):
    article = make_article(
        status=LifecycleStatus.DRAFT, published_at=None
    )
    version = GraphVersion.objects.create(locale="fa")
    node = GraphNode.objects.create(
        version=version,
        node_id="a",
        label="A",
        type="concept",
        accessible_label="A accessible",
        color_role="brand",
        icon_role="dot",
        weight=1,
        pos_x=0.0,
        pos_y=0.0,
    )
    GraphNodeRelated.objects.create(
        node=node,
        content_type=ContentType.objects.get_for_model(Article),
        object_id=article.pk,
    )

    blocked = assert_json(_activate(admin_api_client, version.pk), 409)
    assert blocked["code"] == "VALIDATION_BLOCKED"
    assert [issue["code"] for issue in blocked["issues"]] == ["BROKEN_RELATED"]
    assert blocked["issues"][0]["nodeId"] == "a"
    version.refresh_from_db()
    assert version.status == "draft"

    article.status = LifecycleStatus.PUBLISHED
    article.published_at = past()
    article.save()
    activated = assert_json(_activate(admin_api_client, version.pk), 200)
    assert activated == {"id": version.pk, "status": "active"}
    version.refresh_from_db()
    assert version.status == "active"
    assert AuditLog.objects.filter(action="graph.activate").count() == 1


def test_activate_already_active_409(admin_api_client):
    created = assert_json(_create_version(admin_api_client, "fa"), 201)
    detail = assert_json(_detail(admin_api_client, created["id"]), 200)
    assert_json(
        _put_payload(
            admin_api_client, created["id"], {"nodes": [], "edges": []},
            if_match=detail["updatedAt"],
        ),
        200,
    )
    assert_json(_activate(admin_api_client, created["id"]), 200)
    data = assert_json(_activate(admin_api_client, created["id"]), 409)
    assert data["code"] == "ALREADY_ACTIVE"


def test_put_on_active_version_409_immutable_active(admin_api_client):
    created = assert_json(_create_version(admin_api_client, "fa"), 201)
    detail = assert_json(_detail(admin_api_client, created["id"]), 200)
    assert_json(
        _put_payload(
            admin_api_client, created["id"], {"nodes": [], "edges": []},
            if_match=detail["updatedAt"],
        ),
        200,
    )
    assert_json(_activate(admin_api_client, created["id"]), 200)
    active = assert_json(_detail(admin_api_client, created["id"]), 200)
    data = assert_json(
        _put_payload(
            admin_api_client,
            created["id"],
            {"nodes": [_node("x")], "edges": []},
            if_match=active["updatedAt"],
        ),
        409,
    )
    assert data["code"] == "IMMUTABLE_ACTIVE"


def test_put_missing_if_match_428(admin_api_client):
    created = assert_json(_create_version(admin_api_client, "fa"), 201)
    data = assert_json(
        _put_payload(admin_api_client, created["id"], {"nodes": [], "edges": []}), 428
    )
    assert data["code"] == "PRECONDITION_REQUIRED"


def test_put_stale_if_match_409(admin_api_client):
    created = assert_json(_create_version(admin_api_client, "fa"), 201)
    data = assert_json(
        _put_payload(
            admin_api_client,
            created["id"],
            {"nodes": [], "edges": []},
            if_match="2020-01-01T00:00:00.000Z",
        ),
        409,
    )
    assert data["code"] == "STALE_REVISION"


def test_get_validation_report_clean_and_broken_no_mutation(admin_api_client):
    created = assert_json(_create_version(admin_api_client, "fa"), 201)
    assert assert_json(
        admin_api_client.get(f"{BASE}/validation/{created['id']}"), 200
    ) == {"issues": []}

    article = make_article(status=LifecycleStatus.DRAFT, published_at=None)
    version = GraphVersion.objects.create(locale="fa")
    node = GraphNode.objects.create(
        version=version,
        node_id="a",
        label="A",
        type="concept",
        accessible_label="A accessible",
        color_role="brand",
        icon_role="dot",
        weight=0,
        pos_x=0.0,
        pos_y=0.0,
    )
    GraphNodeRelated.objects.create(
        node=node,
        content_type=ContentType.objects.get_for_model(Article),
        object_id=article.pk,
    )
    data = assert_json(admin_api_client.get(f"{BASE}/validation/{version.pk}"), 200)
    assert [issue["code"] for issue in data["issues"]] == ["BROKEN_RELATED"]
    version.refresh_from_db()
    assert version.status == "draft"
    assert (
        AuditLog.objects.filter(model_name="graph")
        .exclude(action="graph.create")
        .count()
        == 0
    )


def test_validation_unknown_version_404(admin_api_client):
    data = assert_json(admin_api_client.get(f"{BASE}/validation/999999"), 404)
    assert data["code"] == "NOT_FOUND"


def test_detail_unknown_version_404(admin_api_client):
    data = assert_json(admin_api_client.get(f"{BASE}/versions/999999"), 404)
    assert data["code"] == "NOT_FOUND"


def test_put_unknown_edge_endpoint_400(admin_api_client):
    created = assert_json(_create_version(admin_api_client, "fa"), 201)
    detail = assert_json(_detail(admin_api_client, created["id"]), 200)
    payload = {
        "nodes": [_node("a")],
        "edges": [_edge(source="a", target="ghost")],
    }
    data = assert_json(
        _put_payload(admin_api_client, created["id"], payload, if_match=detail["updatedAt"]),
        400,
    )
    assert data["code"] == "VALIDATION"
    assert data["fields"]["edges"] == ["UNKNOWN_EDGE_ENDPOINT"]


def test_put_duplicate_related_400(admin_api_client):
    article = make_article()
    created = assert_json(_create_version(admin_api_client, "fa"), 201)
    detail = assert_json(_detail(admin_api_client, created["id"]), 200)
    payload = {
        "nodes": [
            _node(
                "a",
                relatedRecords=[
                    {"family": "article", "id": str(article.pk)},
                    {"family": "article", "id": str(article.pk)},
                ],
            )
        ],
        "edges": [],
    }
    data = assert_json(
        _put_payload(admin_api_client, created["id"], payload, if_match=detail["updatedAt"]),
        400,
    )
    assert data["code"] == "VALIDATION"
    assert data["fields"]["nodes"] == ["DUPLICATE_RELATED"]


def test_put_group_member_guards_400(admin_api_client):
    created = assert_json(_create_version(admin_api_client, "fa"), 201)
    detail = assert_json(_detail(admin_api_client, created["id"]), 200)

    unknown = {
        "nodes": [_node("a")],
        "edges": [],
        "groups": [{"name": "G", "nodeIds": ["ghost"]}],
    }
    data = assert_json(
        _put_payload(admin_api_client, created["id"], unknown, if_match=detail["updatedAt"]),
        400,
    )
    assert data["fields"]["groups"] == ["UNKNOWN_GROUP_MEMBER"]

    fresh = assert_json(_detail(admin_api_client, created["id"]), 200)
    doubled = {
        "nodes": [_node("a")],
        "edges": [],
        "groups": [
            {"name": "G", "nodeIds": ["a"]},
            {"name": "H", "nodeIds": ["a"]},
        ],
    }
    data = assert_json(
        _put_payload(admin_api_client, created["id"], doubled, if_match=fresh["updatedAt"]),
        400,
    )
    assert data["fields"]["groups"] == ["DUPLICATE_GROUP_MEMBER"]


def test_fa_and_en_versions_are_isolated(admin_api_client):
    fa = assert_json(_create_version(admin_api_client, "fa"), 201)
    en = assert_json(_create_version(admin_api_client, "en"), 201)
    assert fa["locale"] == "fa" and en["locale"] == "en"
    fa_detail = assert_json(_detail(admin_api_client, fa["id"]), 200)
    assert_json(
        _put_payload(
            admin_api_client,
            fa["id"],
            {"nodes": [_node("fa-node")], "edges": []},
            if_match=fa_detail["updatedAt"],
        ),
        200,
    )
    en_detail = assert_json(_detail(admin_api_client, en["id"]), 200)
    assert en_detail["nodes"] == []


def test_anonymous_get_is_401_auth_required():
    data = assert_json(Client().get(f"{BASE}/versions"), 401)
    assert data["code"] == "AUTH_REQUIRED"


def test_non_staff_user_is_403_forbidden(staff_api_client):
    data = assert_json(staff_api_client.get(f"{BASE}/versions"), 403)
    assert data["code"] == "FORBIDDEN"


def test_staff_without_verified_otp_is_403_otp_required(csrf_client, admin_user):
    csrf_client.force_login(admin_user)
    data = assert_json(csrf_client.get(f"{BASE}/versions"), 403)
    assert data["code"] == "OTP_REQUIRED"


def test_put_without_csrf_token_is_403_csrf_failed(csrf_client, admin_user, totp_device):
    csrf_client.force_login(admin_user)
    session = csrf_client.session
    session["otp_device_id"] = totp_device.persistent_id
    session.save()
    response = csrf_client.post(
        f"{BASE}/versions",
        data=json.dumps({"locale": "fa"}),
        content_type="application/json",
    )
    data = assert_json(response, 403)
    assert data["code"] == "CSRF_FAILED"
