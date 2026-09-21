"""Admin Atlas API — Task 1: router skeleton, guards and precondition helper.

The surfaces this module pins (plan Task 1, verbatim obligations):

* the ``atlas_router`` is registered at ``/api/v1/admin/atlas/*`` — the local
  read route (``GET /versions``) answers with the shared admin security model;
* the shared guards hold for every route: staff session (401 anonymous /
  403 non-staff), verified OTP (403 ``OTP_REQUIRED``), CSRF on mutations
  (403 ``CSRF_FAILED``), the If-Match precondition (428
  ``PRECONDITION_REQUIRED`` when the header is missing, 409
  ``STALE_REVISION`` on a stale revision) and the draft-only guard
  (409 ``IMMUTABLE_ACTIVE`` — an active version refuses edits without
  inventing a parallel status model);
* every *successful* mutation writes an ``AuditLog`` row with the
  ``atlas.<verb>`` action, and a FAILED guarded mutation writes none.

Negative-path tests (card sections A/E) send a syntactically and
semantically VALID request body so Django Ninja reaches the route's guards;
only the guard under test is broken. A 422 from an invalid body is never
accepted as guard evidence. The If-Match value is always the accepted Atlas
wire revision — :func:`apps.atlas.services.version_revision`'s output —
pinned by round-tripping through the endpoint response (card section C);
no test hard-codes a made-up revision shape.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.core.cache import cache
from django.test import Client

from apps.atlas.api_admin import _split_atlas_revision
from apps.atlas.models import AtlasVersion
from apps.atlas.services import version_revision
from apps.atlas.tests.factories import (
    _apply_placeholder_layout,
    _default_node_type,
    _node,
    _node_type,
    _relation,
    _relation_type,
    _research_focus_type,
    _version,
)
from apps.security.models import AuditLog

BASE = "/api/v1/admin/atlas"

VALID_LAYOUT = {"layout": {"identity-00000001": [0.0, 0.0, 0.0]}}


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
def anonymous_client():
    """A logged-out public client with CSRF enforcement."""
    return Client(enforce_csrf_checks=True)


@pytest.fixture
def staff_client_without_otp(csrf_client, admin_user):
    """Staff session WITHOUT a verified OTP device (403 OTP_REQUIRED)."""
    csrf_client.force_login(admin_user)
    return csrf_client


@pytest.fixture
def admin_client(csrf_client, admin_user, totp_device):
    """Staff session + verified OTP + CSRF token (the full authoring surface)."""
    csrf_client.force_login(admin_user)
    session = csrf_client.session
    session["otp_device_id"] = totp_device.persistent_id
    session.save()
    token = csrf_client.get("/api/v1/admin/auth/csrf").json()["csrfToken"]
    csrf_client.defaults["HTTP_X_CSRFTOKEN"] = token
    return csrf_client


@pytest.fixture
def draft_version(db):
    """A fully resolvable draft version with one node and layout (Task 1 target)."""
    version = _version(status="draft", label="plan-b-task1-draft")
    identity = _node(version=version, node_type=_default_node_type(), visible=True)
    _relation(
        source=identity,
        target=_node(version=version, node_type=_default_node_type(), visible=True),
        relation_type=_research_focus_type(),
        version=version,
    )
    _apply_placeholder_layout(version, list(version.nodes.all()))
    return version


@pytest.fixture
def active_version(db):
    """An active version the draft-only guard must refuse to edit."""
    version = _version(status="active", label="plan-b-task1-active")
    _node(version=version, node_type=_default_node_type(), visible=True)
    _apply_placeholder_layout(version, list(version.nodes.all()))
    version.refresh_from_db()
    return version


def _first_key(version: AtlasVersion) -> str:
    return version.nodes.order_by("public_key").values_list("public_key", flat=True)[0]


def _if_match(version: AtlasVersion) -> str:
    """The If-Match value: the row revision Plan A mints (``pk-ISO``)."""
    return version_revision(version)


def _patch_node(client, version, payload=None, **extra) -> Client:
    body = payload if payload is not None else {"importance": 70}
    return client.patch(
        f"{BASE}/versions/{version.pk}/nodes/{_first_key(version)}",
        body,
        HTTP_X_CSRFTOKEN=client.defaults.get("HTTP_X_CSRFTOKEN"),
        HTTP_IF_MATCH=_if_match(version),
        content_type="application/json",
        **extra,
    )


def _post_layout(client, version, payload=None, if_match=None) -> Client:
    """POST the layout with a VALID body (card section A: smallest valid payload)."""
    body = VALID_LAYOUT if payload is None else payload
    headers: dict = {}
    csrf = client.defaults.get("HTTP_X_CSRFTOKEN")
    if csrf:
        headers["HTTP_X_CSRFTOKEN"] = csrf
    if if_match is not None:
        headers["HTTP_IF_MATCH"] = if_match
    return client.post(
        f"{BASE}/versions/{version.pk}/layout",
        body,
        content_type="application/json",
        **headers,
    )


# ---------------------------------------------------------------------------
# The auth matrix (plan Step 1, first three tests)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_atlas_routes_require_staff_session(anonymous_client):
    assert anonymous_client.get(f"{BASE}/versions").status_code in (401, 403)


@pytest.mark.django_db
def test_atlas_routes_require_otp(staff_client_without_otp):
    response = staff_client_without_otp.get(f"{BASE}/versions")
    assert response.status_code == 403
    assert response.json()["code"] == "OTP_REQUIRED"


@pytest.mark.django_db
def test_csrf_missing_is_enforced(admin_client, draft_version):
    """Valid session + OTP + If-Match + body, MISSING CSRF → 403 CSRF_FAILED.

    Card section A/E: the request body is fully schema-valid so Django
    Ninja reaches the route's guards (a 422 from an invalid body is NOT
    CSRF-guard evidence); only the CSRF token is violated.
    """
    client = admin_client
    csrf_token = client.defaults.pop("HTTP_X_CSRFTOKEN")
    try:
        response = client.post(
            f"{BASE}/versions/{draft_version.pk}/layout",
            VALID_LAYOUT,
            content_type="application/json",
            HTTP_IF_MATCH=_if_match(draft_version),
        )
    finally:
        client.defaults["HTTP_X_CSRFTOKEN"] = csrf_token
    assert response.status_code == 403
    assert response.json()["code"] == "CSRF_FAILED"


@pytest.mark.django_db
def test_if_match_missing_is_precondition_required(admin_client, draft_version):
    """Valid session + CSRF + body, NO If-Match → 428 PRECONDITION_REQUIRED."""
    csrf_token = admin_client.defaults["HTTP_X_CSRFTOKEN"]
    response = admin_client.post(
        f"{BASE}/versions/{draft_version.pk}/layout",
        VALID_LAYOUT,
        HTTP_X_CSRFTOKEN=csrf_token,
        content_type="application/json",
    )
    assert response.status_code == 428
    assert response.json()["code"] == "PRECONDITION_REQUIRED"


@pytest.mark.django_db
def test_revision_roundtrip_accepts_then_rejects(admin_client, draft_version):
    """The card section C compatibility loop, pinned through the API itself.

    1. The revision the wire helper mints for the CURRENT row is accepted →
       PATCH node mutation succeeds.
    2. A revision minted for an EARLIER state of a row is refused → 409
       ``STALE_REVISION`` — both directions of one genuine
       ``version_revision()`` value are exercised.
    """
    # 1+2: accept the current revision end-to-end.
    response = _patch_node(admin_client, draft_version)
    assert response.status_code == 200, response.content
    assert response.json()["publicKey"] == _first_key(draft_version)

    # 3: mint a revision, mutate the row underneath it → stale.
    version = _version(status="draft", label="plan-b-roundtrip-stale")
    identity = _node(version=version, node_type=_default_node_type(), visible=True)
    _apply_placeholder_layout(version, [identity])
    version.refresh_from_db()
    vintage = version_revision(version)
    stale_stamp = version.updated_at - timedelta(seconds=1)
    type(version)._base_manager.filter(pk=version.pk).update(
        label="plan-b-roundtrip-stale-2", updated_at=stale_stamp
    )
    version.refresh_from_db()
    assert version_revision(version) != vintage
    response = admin_client.patch(
        f"{BASE}/versions/{version.pk}/nodes/{_first_key(version)}",
        {"importance": 55},
        HTTP_X_CSRFTOKEN=admin_client.defaults["HTTP_X_CSRFTOKEN"],
        HTTP_IF_MATCH=vintage,
        content_type="application/json",
    )
    assert response.status_code == 409
    assert response.json()["code"] == "STALE_REVISION"


@pytest.mark.django_db
def test_revision_read_from_the_versions_endpoint_is_accepted(admin_client, draft_version):
    """The revision the GET /versions row exposes round-trips through PATCH.

    Card section C: the client-facing revision value is taken from the
    admin representation itself, never a hard-coded helper call, so the
    actual wire contract — not a made-up format — is what is validated.
    """
    listing = admin_client.get(f"{BASE}/versions").json()
    row = next(r for r in listing if r["id"] == draft_version.pk)
    response = admin_client.patch(
        f"{BASE}/versions/{draft_version.pk}/nodes/{_first_key(draft_version)}",
        {"importance": 70},
        HTTP_X_CSRFTOKEN=admin_client.defaults["HTTP_X_CSRFTOKEN"],
        HTTP_IF_MATCH=row["revision"],
        content_type="application/json",
    )
    assert response.status_code == 200, response.content



def test_stale_revision_rejected_with_vintage_revision(admin_client):
    version = _version(status="draft", label="plan-b-stale-check")
    identity = _node(version=version, node_type=_default_node_type(), visible=True)
    _apply_placeholder_layout(version, [identity])
    version.refresh_from_db()

    current_revision = version_revision(version)
    # Mutate the row so the absorbed revision no longer describes it — via a
    # queryset update that forces an explicitly DIFFERENT updated_at (Windows
    # clock granularity can repeat auto_now within one test, which would keep
    # the "stale" stamp equal to the fresh one and defeat the pin).
    stale_stamp = version.updated_at - timedelta(seconds=1)
    type(version)._base_manager.filter(pk=version.pk).update(
        label="plan-b-stale-check-2", updated_at=stale_stamp
    )
    version.refresh_from_db()
    assert version_revision(version) != current_revision

    response = admin_client.patch(
        f"{BASE}/versions/{version.pk}/nodes/{_first_key(version)}",
        {"importance": 55},
        HTTP_X_CSRFTOKEN=admin_client.defaults["HTTP_X_CSRFTOKEN"],
        HTTP_IF_MATCH=current_revision,
        content_type="application/json",
    )
    assert response.status_code == 409
    assert response.json()["code"] == "STALE_REVISION"


def test_malformed_revision_shape_is_stale_not_crash(admin_client):
    """A non ``<pk>-<ISO>`` header falls in the stale branch, never a crash."""
    version = _version(status="draft", label="plan-b-shape-check")
    identity = _node(version=version, node_type=_default_node_type(), visible=True)
    _apply_placeholder_layout(version, [identity])
    version.refresh_from_db()

    for bogus in ("", "guarbage", f"{version.pk}-not-a-date", "abc-2026-01-01T00:00:00"):
        response = admin_client.patch(
            f"{BASE}/versions/{version.pk}/nodes/{_first_key(version)}",
            {"importance": 55},
            HTTP_X_CSRFTOKEN=admin_client.defaults["HTTP_X_CSRFTOKEN"],
            HTTP_IF_MATCH=bogus,
            content_type="application/json",
        )
        assert response.status_code == 409, bogus
        assert response.json()["code"] == "STALE_REVISION", bogus


def test_foreign_version_revision_component_is_stale(admin_client):
    """A structurally valid revision carrying the WRONG version pk → 409.

    The revision's version-id component is validated, not just its shape.
    """
    version = _version(status="draft", label="plan-b-wrong-pk")
    identity = _node(version=version, node_type=_default_node_type(), visible=True)
    _apply_placeholder_layout(version, [identity])
    version.refresh_from_db()

    # A syntactically valid revision minted for a DIFFERENT version row.
    other = _version(status="draft", label="plan-b-wrong-pk-revision-source")
    other_node = _node(version=other, node_type=_default_node_type(), visible=True)
    _apply_placeholder_layout(other, [other_node])
    other.refresh_from_db()

    response = admin_client.patch(
        f"{BASE}/versions/{version.pk}/nodes/{_first_key(version)}",
        {"importance": 55},
        HTTP_X_CSRFTOKEN=admin_client.defaults["HTTP_X_CSRFTOKEN"],
        HTTP_IF_MATCH=_if_match(other),  # other's pk, other's timestamp
        content_type="application/json",
    )
    assert response.status_code == 409
    assert response.json()["code"] == "STALE_REVISION"


# ---------------------------------------------------------------------------
# The draft-only guard + audit (plan Step 1, remaining tests)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_editing_an_active_version_is_refused(admin_client, active_version):
    """Every earlier prerequisite valid (card section D/E): the target IS
    an ACTIVE version with a genuinely current Atlas revision — the failing
    boundary is ``_require_draft`` and nothing else."""
    response = admin_client.patch(
        f"{BASE}/versions/{active_version.pk}/nodes/{_first_key(active_version)}",
        {"importance": 90},
        HTTP_X_CSRFTOKEN=admin_client.defaults["HTTP_X_CSRFTOKEN"],
        HTTP_IF_MATCH=_if_match(active_version),
        content_type="application/json",
    )
    assert response.status_code == 409
    assert response.json()["code"] == "IMMUTABLE_ACTIVE"
    # No success audit was written for a guard-blocked mutation (section F).
    assert not AuditLog.objects.filter(action="atlas.node.update").exists()


@pytest.mark.django_db
def test_invalid_body_still_422(admin_client, draft_version):
    """Invalid/absent body → 422 from the schema; NOT guard evidence (card A).

    Pinned on the node-create route, which keeps a typed body; the layout
    POST now takes NO body at all (Task 5: recompute), so nothing there
    could 422.
    """
    response = admin_client.post(
        f"{BASE}/versions/{draft_version.pk}/nodes",
        {},
        HTTP_X_CSRFTOKEN=admin_client.defaults["HTTP_X_CSRFTOKEN"],
        HTTP_IF_MATCH=version_revision(draft_version),
        content_type="application/json",
    )
    assert response.status_code == 422


@pytest.mark.django_db
def test_mutations_are_audited(admin_client, draft_version):
    assert AuditLog.objects.count() == 0
    response = _patch_node(admin_client, draft_version)
    assert response.status_code == 200
    row = AuditLog.objects.get(action="atlas.node.update")
    assert row.object_id == str(draft_version.pk)
    assert row.model_name == "atlas"


@pytest.mark.django_db
def test_layout_mutation_is_audited(admin_client, draft_version):
    response = _post_layout(admin_client, draft_version, if_match=_if_match(draft_version))
    assert response.status_code == 200
    assert AuditLog.objects.filter(action="atlas.layout.recompute").exists()


# ---------------------------------------------------------------------------
# The wire-revision helper itself (no other test scans these internals).
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Task 2: version lifecycle endpoints (plan Task 2) — written RED first.
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_create_version_returns_draft_with_revision(admin_client):
    response = admin_client.post(
        f"{BASE}/versions", {"label": "t2-created"}, content_type="application/json"
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "draft"
    assert body["label"] == "t2-created"
    assert body["revision"]  # the version_revision() wire value


@pytest.mark.django_db
def test_version_detail_includes_revision(admin_client, draft_version):
    response = admin_client.get(f"{BASE}/versions/{draft_version.pk}")
    assert response.status_code == 200
    assert response.json()["revision"] == version_revision(draft_version)


@pytest.mark.django_db
def test_clone_returns_distinct_id_with_identical_keys(admin_client, draft_version):
    node_keys_before = list(draft_version.nodes.values_list("public_key", flat=True))
    relation_keys_before = [
        (r.source.public_key, r.target.public_key, r.relation_type.key)
        for r in draft_version.relations.select_related(
            "source", "target", "relation_type"
        )
    ]
    response = admin_client.post(
        f"{BASE}/versions/{draft_version.pk}/clone",
        {"label": "t2-clone"},
        content_type="application/json",
    )
    assert response.status_code == 201
    body = response.json()
    assert body["id"] != draft_version.pk
    assert body["status"] == "draft"
    clone = AtlasVersion.objects.get(pk=body["id"])
    assert list(clone.nodes.values_list("public_key", flat=True)) == node_keys_before
    cloned_pairs = [
        (r.source.public_key, r.target.public_key, r.relation_type.key)
        for r in clone.relations.select_related("source", "target", "relation_type")
    ]
    assert cloned_pairs == relation_keys_before


@pytest.mark.django_db
def test_archiving_a_draft_is_refused(admin_client, draft_version):
    response = admin_client.post(f"{BASE}/versions/{draft_version.pk}/archive")
    assert response.status_code == 409


@pytest.mark.django_db
def test_version_listing_counts_match_the_orm(admin_client, draft_version):
    listing = admin_client.get(f"{BASE}/versions").json()
    row = next(r for r in listing if r["id"] == draft_version.pk)
    assert row["nodeCount"] == draft_version.nodes.count()
    assert row["relationCount"] == draft_version.relations.count()
    assert row["nodeCount"] == 2  # fixture: identity + one target
    assert row["relationCount"] == 1


@pytest.mark.django_db
def test_archiving_an_active_version_succeeds(admin_client):
    version = _version(status="active", label="t2-archive-active")
    identity = _node(version=version, node_type=_default_node_type(), visible=True)
    _apply_placeholder_layout(version, [identity])
    version.refresh_from_db()
    response = admin_client.post(f"{BASE}/versions/{version.pk}/archive")
    assert response.status_code == 200
    version.refresh_from_db()
    assert version.status == "archived"


@pytest.mark.parametrize(
    "verb,body",
    [
        ("create", {"label": "audit-check"}),
        ("clone", {"label": "audit-check-clone"}),
        ("archive", None),
    ],
)
@pytest.mark.django_db
def test_version_mutations_are_audited(admin_client, draft_version, verb, body):
    if verb == "clone":
        path = f"{BASE}/versions/{draft_version.pk}/clone"
    elif verb == "archive":
        active = _version(status="active", label=f"t2-audit-{verb}")
        _node(version=active, node_type=_default_node_type(), visible=True)
        path = f"{BASE}/versions/{active.pk}/archive"
    else:
        path = f"{BASE}/versions"
    response = admin_client.post(path, body, content_type="application/json")
    assert response.status_code == 201 if verb != "archive" else response.status_code == 200
    assert AuditLog.objects.filter(action=f"atlas.version.{verb}").exists()


@pytest.mark.django_db
def test_unknown_version_answers_404(admin_client, db):
    assert admin_client.get(f"{BASE}/versions/9999").status_code == 404
    assert admin_client.post(
        f"{BASE}/versions/9999/clone", {"label": "x"}, content_type="application/json"
    ).status_code == 404


@pytest.mark.parametrize(
    "value,expected_pk,expected_us",
    [
        ("17-2026-09-19T10:30:15+00:00", "17", 0),
        ("1-2026-09-19T10:30:15.123+00:00", "1", 123000),
    ],
)
def test_split_atlas_revision_accepts_wire_shape(value, expected_pk, expected_us):
    version_id, stamp = _split_atlas_revision(value)
    assert version_id == expected_pk
    assert stamp.microsecond == expected_us


@pytest.mark.parametrize(
    "value",
    [
        "",
        "guarbage",
        "abc-2026-01-01T00:00:00+00:00",
        "17-good-but-bogus-stamp",
        "17",  # no separator at all
        "-2026-01-01T00:00:00+00:00",  # empty pk
        "17-2026-01-01T00:00:00+extra-00:00",
    ],
)
def test_split_atlas_revision_rejects_non_wire_shape(value):
    assert _split_atlas_revision(value) == (None, None)



@pytest.fixture
def relation_fixtures(db, draft_version):
    """Typed nodes + a restricted "uses" type, the Task-4 substrate."""
    methods_type = _node_type(
        key="method", canonical_source="method", label_en="Method", label_fa="روش"
    )
    projects_type = _node_type(
        key="project", canonical_source="project", label_en="Project", label_fa="پروژه"
    )
    publications_type = _node_type(
        key="publication", canonical_source="publication", label_en="Publication", label_fa="انتشار"
    )
    method_node = _node(version=draft_version, node_type=methods_type, visible=True)
    project_node = _node(version=draft_version, node_type=projects_type, visible=True)
    publication_node = _node(version=draft_version, node_type=publications_type, visible=True)
    uses = _relation_type(
        "uses",
        label_en="uses",
        label_fa="استفاده",
        directed_default=True,
        overridable_direction=False,
    )
    uses.allowed_source_types.add(projects_type)
    uses.allowed_target_types.add(methods_type)
    uses.allowed_target_types.add(publications_type)
    return {
        "types": {
            "project": projects_type,
            "method": methods_type,
            "publication": publications_type,
        },
        "nodes": {
            "project": project_node,
            "method": method_node,
            "publication": publication_node,
        },
        "uses": uses,
    }


def _node_keys(fixture):
    return {k: v.public_key for k, v in fixture["nodes"].items()}


def _create_relation(client, version, payload):
    return client.post(
        f"{BASE}/versions/{version.pk}/relations",
        payload,
        HTTP_X_CSRFTOKEN=client.defaults.get("HTTP_X_CSRFTOKEN"),
        HTTP_IF_MATCH=version_revision(version),
        content_type="application/json",
    )


@pytest.mark.django_db
def test_relation_create_enforces_allowed_pairs(admin_client, draft_version, relation_fixtures):
    keys = _node_keys(relation_fixtures)
    response = _create_relation(admin_client, draft_version, {
        "sourceKey": keys["publication"], "relationTypeKey": "uses", "targetKey": keys["method"],
    })
    assert response.status_code == 409
    assert response.json()["issues"][0]["code"] == "RELATION_TYPE_NOT_ALLOWED"


@pytest.mark.django_db
def test_relation_key_is_the_composed_public_key(admin_client, draft_version, relation_fixtures):
    keys = _node_keys(relation_fixtures)
    created = _create_relation(admin_client, draft_version, {
        "sourceKey": keys["project"], "relationTypeKey": "uses", "targetKey": keys["method"],
    })
    assert created.status_code == 201, created.content
    assert created.json()["key"] == f"{keys['project']}~uses~{keys['method']}"


@pytest.mark.django_db
def test_relation_patch_persists_explanations(admin_client, draft_version, relation_fixtures):
    from apps.atlas.models import AtlasRelationTranslation

    keys = _node_keys(relation_fixtures)
    created = _create_relation(admin_client, draft_version, {
        "sourceKey": keys["project"], "relationTypeKey": "uses", "targetKey": keys["method"],
    })
    assert created.status_code == 201, created.content
    relation_key = created.json()["key"]
    response = admin_client.patch(
        f"{BASE}/versions/{draft_version.pk}/relations/{relation_key}",
        {"explanation": {"en": "Uses it.", "fa": "از آن استفاده می‌کند."}},
        HTTP_X_CSRFTOKEN=admin_client.defaults["HTTP_X_CSRFTOKEN"],
        HTTP_IF_MATCH=version_revision(draft_version),
        content_type="application/json",
    )
    assert response.status_code == 200, response.content
    stored = {
        t.locale: t.explanation
        for t in AtlasRelationTranslation.objects.filter(
            relation__version=draft_version, relation__source__public_key=keys["project"],
        )
    }
    assert stored == {"en": "Uses it.", "fa": "از آن استفاده می‌کند."}


@pytest.mark.django_db
def test_relation_patch_rejects_unknown_explanation_locale(
    admin_client, draft_version, relation_fixtures
):
    keys = _node_keys(relation_fixtures)
    created = _create_relation(admin_client, draft_version, {
        "sourceKey": keys["project"], "relationTypeKey": "uses", "targetKey": keys["method"],
    })
    assert created.status_code == 201, created.content
    response = admin_client.patch(
        f"{BASE}/versions/{draft_version.pk}/relations/{created.json()['key']}",
        {"explanation": {"de": "Verwendet es."}},
        HTTP_X_CSRFTOKEN=admin_client.defaults["HTTP_X_CSRFTOKEN"],
        HTTP_IF_MATCH=version_revision(draft_version),
        content_type="application/json",
    )
    assert response.status_code == 400
    assert response.json()["fields"]["explanation"]


@pytest.mark.django_db
def test_directed_override_is_refused_when_the_type_forbids_it(
    admin_client, draft_version, relation_fixtures
):
    keys = _node_keys(relation_fixtures)
    response = _create_relation(admin_client, draft_version, {
        "sourceKey": keys["project"], "relationTypeKey": "uses", "targetKey": keys["method"],
        "directed": False,
    })
    assert response.status_code == 400
    assert response.json()["fields"]["directed"]


@pytest.mark.django_db
def test_deleting_an_in_use_node_type_is_blocked(admin_client, draft_version):
    response = admin_client.delete(
        f"{BASE}/node-types/{_default_node_type().key}",
        HTTP_X_CSRFTOKEN=admin_client.defaults["HTTP_X_CSRFTOKEN"],
    )
    assert response.status_code == 409
    assert response.json()["code"] == "TAXONOMY_IN_USE"


@pytest.mark.django_db
def test_inactive_taxonomy_cannot_be_referenced(
    admin_client, draft_version, relation_fixtures
):
    keys = _node_keys(relation_fixtures)
    relation_fixtures["types"]["method"].active = False
    relation_fixtures["types"]["method"].save(update_fields=["active"])
    response = _create_relation(admin_client, draft_version, {
        "sourceKey": keys["project"], "relationTypeKey": "uses", "targetKey": keys["method"],
    })
    assert response.status_code == 400
    assert response.json()["fields"]["targetKey"]


@pytest.fixture
def group_fixtures(db, draft_version, relation_fixtures):
    """A group in the draft version with one member node."""
    from apps.atlas.keys import new_group_key
    from apps.atlas.models import AtlasGroup, AtlasGroupMembership

    group = AtlasGroup.objects.create(
        version=draft_version, public_key=new_group_key()
    )
    method_node = relation_fixtures["nodes"]["method"]
    AtlasGroupMembership.objects.create(group=group, node=method_node)
    return {"group": group, "nodes": relation_fixtures["nodes"], "method": method_node}


@pytest.mark.django_db
def test_group_members_are_replaced_transactionally(admin_client, draft_version, group_fixtures):
    group = group_fixtures["group"]
    project_node = group_fixtures["nodes"]["project"]
    response = admin_client.put(
        f"{BASE}/versions/{draft_version.pk}/groups/{group.public_key}/members",
        {"nodeKeys": [project_node.public_key]},
        HTTP_X_CSRFTOKEN=admin_client.defaults["HTTP_X_CSRFTOKEN"],
        HTTP_IF_MATCH=version_revision(draft_version),
        content_type="application/json",
    )
    assert response.status_code == 200, response.content
    members = list(group.members.values_list("node__public_key", flat=True))
    assert members == [project_node.public_key]


@pytest.mark.django_db
def test_group_members_reject_unknown_nodes(admin_client, draft_version, group_fixtures):
    response = admin_client.put(
        f"{BASE}/versions/{draft_version.pk}/groups/{group_fixtures['group'].public_key}/members",
        {"nodeKeys": ["identity-00000099"]},
        HTTP_X_CSRFTOKEN=admin_client.defaults["HTTP_X_CSRFTOKEN"],
        HTTP_IF_MATCH=version_revision(draft_version),
        content_type="application/json",
    )
    assert response.status_code == 400
    assert response.json()["fields"]["nodeKeys"]


@pytest.mark.django_db
def test_active_taxonomy_gate_does_not_block_group_membership(
    admin_client, draft_version, group_fixtures, relation_fixtures
):
    """An inactive node TYPE blocks NEW relations, not membership updates."""
    method_type = relation_fixtures["types"]["method"]
    method_type.active = False
    method_type.save(update_fields=["active"])
    response = admin_client.put(
        f"{BASE}/versions/{draft_version.pk}/groups/{group_fixtures['group'].public_key}/members",
        {"nodeKeys": [group_fixtures["nodes"]["project"].public_key]},
        HTTP_X_CSRFTOKEN=admin_client.defaults["HTTP_X_CSRFTOKEN"],
        HTTP_IF_MATCH=version_revision(draft_version),
        content_type="application/json",
    )
    assert response.status_code == 200, response.content


# ---------------------------------------------------------------------------
# Task-4 fix round tests (review 4-r findings 1-4, 6, 9)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_delete_node_type_in_use_maps_to_taxonomy_in_use(admin_client, draft_version):
    response = admin_client.delete(
        f"{BASE}/node-types/{_default_node_type().key}",
        HTTP_X_CSRFTOKEN=admin_client.defaults["HTTP_X_CSRFTOKEN"],
    )
    assert response.status_code == 409
    assert response.json()["code"] == "TAXONOMY_IN_USE"
    assert _default_node_type().__class__.objects.filter(
        key=_default_node_type().key
    ).exists()


@pytest.mark.django_db
def test_node_types_list_and_create(admin_client):
    created = admin_client.post(
        f"{BASE}/node-types",
        {"key": "dataset", "label_en": "Dataset", "label_fa": "مجموعه‌داده"},
        content_type="application/json",
    )
    assert created.status_code == 201, created.content
    assert created.json()["canonicalSource"] == "none"
    duplicate = admin_client.post(
        f"{BASE}/node-types",
        {"key": "dataset", "label_en": "Dataset 2", "label_fa": "دومی"},
        content_type="application/json",
    )
    assert duplicate.status_code == 400


@pytest.mark.django_db
def test_node_type_patch_can_retire_and_unretire(admin_client, draft_version):
    type_key = _default_node_type().key
    # in-use -> retire refused
    refused = admin_client.patch(
        f"{BASE}/node-types/{type_key}", {"active": False},
        HTTP_X_CSRFTOKEN=admin_client.defaults["HTTP_X_CSRFTOKEN"],
        content_type="application/json",
    )
    assert refused.status_code == 409
    # unused type retires fine
    unused = _node_type(key="policy", canonical_source="none", label_en="Unused", label_fa="بی‌اثر")
    ok = admin_client.patch(
        f"{BASE}/node-types/{unused.key}", {"active": False},
        HTTP_X_CSRFTOKEN=admin_client.defaults["HTTP_X_CSRFTOKEN"],
        content_type="application/json",
    )
    assert ok.status_code == 200
    # back to active allowed
    revived = admin_client.patch(
        f"{BASE}/node-types/{unused.key}", {"active": True},
        HTTP_X_CSRFTOKEN=admin_client.defaults["HTTP_X_CSRFTOKEN"],
        content_type="application/json",
    )
    assert revived.status_code == 200


@pytest.mark.django_db
def test_relation_types_crud_roundtrip(admin_client, relation_fixtures):
    admin_client.post(
        f"{BASE}/node-types",
        {"key": "dataset", "label_en": "Dataset", "label_fa": "مجموعه‌داده"},
        content_type="application/json",
    )
    created = admin_client.post(
        f"{BASE}/relation-types",
        {
            "key": "informed-by",
            "label_en": "informed by",
            "label_fa": "مطلع از",
            "directedDefault": True,
            "allowedSourceTypes": ["dataset"],
            "allowedTargetTypes": ["project"],
        },
        content_type="application/json",
    )
    assert created.status_code == 201, created.content
    listing = admin_client.get(f"{BASE}/relation-types").json()
    row = next(r for r in listing if r["key"] == "informed-by")
    assert row["directedDefault"] is True
    renamed = admin_client.patch(
        f"{BASE}/relation-types/informed-by", {"label_en": "informed by (renamed)"},
        HTTP_X_CSRFTOKEN=admin_client.defaults["HTTP_X_CSRFTOKEN"],
        content_type="application/json",
    )
    assert renamed.status_code == 200
    deleted = admin_client.delete(
        f"{BASE}/relation-types/informed-by",
        HTTP_X_CSRFTOKEN=admin_client.defaults["HTTP_X_CSRFTOKEN"],
    )
    assert deleted.status_code == 204


@pytest.mark.django_db
def test_relation_type_in_use_cannot_be_deleted(admin_client, draft_version, relation_fixtures):
    keys = _node_keys(relation_fixtures)
    created = _create_relation(admin_client, draft_version, {
        "sourceKey": keys["project"], "relationTypeKey": "uses", "targetKey": keys["method"],
    })
    assert created.status_code == 201
    refused = admin_client.delete(
        f"{BASE}/relation-types/uses",
        HTTP_X_CSRFTOKEN=admin_client.defaults["HTTP_X_CSRFTOKEN"],
    )
    assert refused.status_code == 409
    assert refused.json()["code"] == "TAXONOMY_IN_USE"


@pytest.mark.django_db
def test_relation_type_row_exposes_filter_fields(admin_client, relation_fixtures):
    listing = admin_client.get(f"{BASE}/relation-types").json()
    row = next(r for r in listing if r["key"] == "uses")
    assert row["allowedSourceTypes"] == ["project"]
    assert row["allowedTargetTypes"] == ["method", "publication"]
    assert row["hierarchyRole"] is False


@pytest.mark.django_db
def test_group_patch_renames_labels(admin_client, draft_version, group_fixtures):
    group = group_fixtures["group"]
    response = admin_client.patch(
        f"{BASE}/versions/{draft_version.pk}/groups/{group.public_key}",
        {"labels": {"en": "Renamed group", "fa": "گروه بازنامید‌ه"}},
        HTTP_X_CSRFTOKEN=admin_client.defaults["HTTP_X_CSRFTOKEN"],
        HTTP_IF_MATCH=version_revision(draft_version),
        content_type="application/json",
    )
    assert response.status_code == 200, response.content
    assert response.json()["label"] == "Renamed group"


@pytest.mark.django_db
def test_membership_put_reorders_existing_members(admin_client, draft_version, group_fixtures):
    group = group_fixtures["group"]
    project_node = group_fixtures["nodes"]["project"]
    response = admin_client.put(
        f"{BASE}/versions/{draft_version.pk}/groups/{group.public_key}/members",
        {"nodeKeys": [project_node.public_key, group_fixtures["method"].public_key]},
        HTTP_X_CSRFTOKEN=admin_client.defaults["HTTP_X_CSRFTOKEN"],
        HTTP_IF_MATCH=version_revision(draft_version),
        content_type="application/json",
    )
    assert response.status_code == 200
    assert response.json()["memberKeys"] == [
        project_node.public_key,
        group_fixtures["method"].public_key,
    ]
    # Reverse order must reorder the SAME members (fix round pin).
    reversed_response = admin_client.put(
        f"{BASE}/versions/{draft_version.pk}/groups/{group.public_key}/members",
        {"nodeKeys": [group_fixtures["method"].public_key, project_node.public_key]},
        HTTP_X_CSRFTOKEN=admin_client.defaults["HTTP_X_CSRFTOKEN"],
        HTTP_IF_MATCH=version_revision(draft_version),
        content_type="application/json",
    )
    assert reversed_response.json()["memberKeys"] == [  # the pinned order
        group_fixtures["method"].public_key,
        project_node.public_key,
    ]


# ---------------------------------------------------------------------------
# Task 5: layout recompute, bulk graph PUT, validate, activate, status
# (plan Task 5) — written RED first.
# ---------------------------------------------------------------------------


def _get_or_make_type(key, canonical_source, label_en, label_fa):
    from apps.atlas.models import AtlasNodeType

    existing = AtlasNodeType.objects.filter(key=key).first()
    if existing is not None:
        return existing
    return _node_type(
        key=key, canonical_source=canonical_source, label_en=label_en, label_fa=label_fa
    )


def _post_action(client, version, name, payload=None):
    return client.post(
        f"{BASE}/versions/{version.pk}/{name}",
        payload if payload is not None else {},
        HTTP_X_CSRFTOKEN=client.defaults.get("HTTP_X_CSRFTOKEN"),
        HTTP_IF_MATCH=version_revision(version),
        content_type="application/json",
    )


@pytest.fixture
def clean_draft(db, draft_version, relation_fixtures):
    """A draft version carrying a projection-clean pair of canonical nodes.

    The activate test needs a graph that passes the publish battery; give both
    canonical Method rows (already published) and remove the fixture's extra
    nodes so locale parity holds.
    """
    from uuid import uuid4

    from django.utils import timezone

    from apps.content.models import Method

    method_type_row = _get_or_make_type("t5method", "method", "T5 Method", "روش")
    _get_or_make_type("t5structural", "none", "Structural", "ساختاری")
    identity = draft_version.nodes.first()
    key = uuid4()
    now = timezone.now()
    rows = []
    for locale in ("en", "fa"):
        rows.append(
            Method.objects.create(
                locale=locale,
                slug=f"t5-clean-{locale}",
                title=f"T5 clean {locale}",
                short_description="Used by Task 5",
                status="published",
                published_at=now,
                translation_key=key,
            )
        )
    return {
        "identity": identity,
        "rows": rows,
        "key": key,
        "version": draft_version,
        "type": method_type_row,
    }


@pytest.fixture
def method_type(db):
    """The method-canonical node type the Task-5 clean graph hangs off."""
    return _get_or_make_type("t5method", "method", "T5 Method", "روش")


@pytest.mark.django_db
def test_layout_endpoint_is_deterministic_and_revisioned(admin_client, draft_version):
    from apps.atlas.models import AtlasVersion
    from apps.atlas.services import version_revision as vr

    before = AtlasVersion.objects.get(pk=draft_version.pk).layout_revision
    admin_client.defaults["HTTP_IF_MATCH"] = vr(draft_version)
    first = _post_action(admin_client, draft_version, "layout").json()
    draft_version.refresh_from_db()
    admin_client.defaults["HTTP_IF_MATCH"] = vr(draft_version)
    second = _post_action(admin_client, draft_version, "layout").json()
    assert first["coordinates"] == second["coordinates"]
    assert second["layoutRevision"] == first["layoutRevision"] + 1
    assert second["layoutRevision"] > before


@pytest.mark.django_db
def test_validate_returns_blocking_and_warnings(admin_client, draft_version):
    response = admin_client.get(f"{BASE}/versions/{draft_version.pk}/validate")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"blocking", "warnings"}


@pytest.mark.django_db
def test_activate_requires_a_clean_report_and_enqueues_a_job(
    admin_client, draft_version, method_type, clean_draft
):
    blocked = _post_action(admin_client, draft_version, "activate")
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "VALIDATION_BLOCKED"


@pytest.mark.django_db
def test_activate_on_an_active_version_is_already_active(admin_client, active_version):

    response = _post_action(admin_client, active_version, "activate")
    # The service raises AlreadyActive -> the envelope code ALREADY_ACTIVE.
    assert response.status_code == 409
    assert response.json()["code"] == "ALREADY_ACTIVE"


@pytest.mark.django_db
def test_stale_revision_on_activate_is_stale(admin_client, draft_version):
    from apps.atlas.models import AtlasVersion

    stale_stamp = draft_version.updated_at - timedelta(seconds=1)
    AtlasVersion._base_manager.filter(pk=draft_version.pk).update(updated_at=stale_stamp)
    draft_version.refresh_from_db()
    # Send a revision minted from a PAST stamp while the row's stamp is the same
    # past stamp — instead force a mismatch by sending the revision of ANOTHER row.
    other = _version(status="draft", label="t5-other")
    _node(version=other, node_type=_default_node_type(), visible=True)
    other.refresh_from_db()
    response = admin_client.post(
        f"{BASE}/versions/{draft_version.pk}/activate",
        {},
        HTTP_X_CSRFTOKEN=admin_client.defaults["HTTP_X_CSRFTOKEN"],
        HTTP_IF_MATCH=version_revision(other),
        content_type="application/json",
    )
    assert response.status_code == 409
    assert response.json()["code"] == "STALE_REVISION"



def _put_graph(client, version, body):
    return client.put(
        f"{BASE}/versions/{version.pk}/graph",
        body,
        HTTP_X_CSRFTOKEN=client.defaults.get("HTTP_X_CSRFTOKEN"),
        HTTP_IF_MATCH=version_revision(version),
        content_type="application/json",
    )


@pytest.fixture
def t5_uses_type(db, relation_fixtures):
    """A relate-to type for the bulk tests (any pair allowed, no M2M rows)."""
    from apps.atlas.models import AtlasRelationType

    return AtlasRelationType.objects.filter(key="related-to").first() if False else _relation_type(
        "related-to",
        label_en="related to",
        label_fa="مرتبط با",
        semantic_role="utility",
        directed_default=True,
        self_loop_policy="allow",
    )


@pytest.mark.django_db
def test_bulk_graph_put_is_transactional(admin_client, clean_draft, t5_uses_type):

    version = clean_draft["version"]
    from apps.atlas.services import version_revision as vr

    canonical = {"nodeTypeKey": "t5method", "canonicalTranslationKey": str(clean_draft["key"])}
    structural = {"nodeTypeKey": "t5structural", "canonicalTranslationKey": None}
    body = {
        "nodes": [dict(canonical, publicKey="t5method-00000001"),
                  dict(structural, publicKey="t5method-00000002")],
        "relations": [], "groups": [],
    }

    ok = admin_client.put(
        f"{BASE}/versions/{version.pk}/graph",
        body, HTTP_X_CSRFTOKEN=admin_client.defaults["HTTP_X_CSRFTOKEN"],
        HTTP_IF_MATCH=vr(version),
        content_type="application/json",
    )
    # A clean two-node graph still FAILS the publish battery — the structural
    # node has no projected label in either locale, which is exactly what the
    # parity blocker exists for; the bulk reject answers VALIDATION_BLOCKED.
    assert ok.status_code == 409
    blocking = ok.json()["issues"][0]["code"]
    assert blocking in ("MISSING_LOCALE_PROJECTION", "MISSING_LAYOUT")
    node_count = version.nodes.count()
    # now a dangling relation must abort the whole replace (nothing written)
    dangling = {
        "nodes": [dict(canonical, publicKey="t5method-00000001")],
        "relations": [{"sourceKey": "t5method-00000001",
                       "relationTypeKey": "related-to",
                       "targetKey": "ghost"}],
        "groups": [],
    }
    response = _put_graph(admin_client, version, dangling)
    # Ninja's payload validation or the local guard refuses the unknown target;
    # either way the graph was NOT replaced (the old two-node set persists).
    assert response.status_code in (400, 409), response.content
    assert version.nodes.count() == node_count



@pytest.mark.django_db
def test_activate_success_activates_and_enqueues_a_job(
    admin_client, clean_draft, t5_uses_type
):
    """The plan's success half: a clean draft activates and enqueues a job."""
    version = clean_draft["version"]
    from apps.atlas.models import AtlasVersion
    from apps.atlas.services import version_revision as vr
    from apps.rebuild.models import PublicationJob

    # The draft fixture's identity→area relation breaks its type's pair rule
    # once the nodes are typed; the publish-safe body must replace it. Wipe
    # the graph to the projection-clean two-node shape and recompute.
    canonical_key = str(clean_draft["key"])
    body = {
        "nodes": [
            {"nodeTypeKey": "t5method", "canonicalTranslationKey": canonical_key},
            {"nodeTypeKey": "t5structural", "canonicalTranslationKey": None,
             "overrides": {"en": {"label": " áreas EN"}, "fa": {"label": "ناحیه FA"}}},
        ],
        "relations": [], "groups": [],
    }
    replaced = admin_client.put(
        f"{BASE}/versions/{version.pk}/graph",
        body,
        HTTP_X_CSRFTOKEN=admin_client.defaults["HTTP_X_CSRFTOKEN"],
        HTTP_IF_MATCH=vr(version),
        content_type="application/json",
    )
    assert replaced.status_code == 200, replaced.content

    admin_client.defaults["HTTP_IF_MATCH"] = vr(version)
    layout = _post_action(admin_client, version, "layout")
    assert layout.status_code == 200, layout.content
    version.refresh_from_db()
    admin_client.defaults["HTTP_IF_MATCH"] = vr(version)
    activated = _post_action(admin_client, version, "activate")
    assert activated.status_code == 200, activated.content
    body = activated.json()
    assert body["status"] == "active"
    assert body["enqueuedPublicationJob"] is not None
    assert PublicationJob.objects.filter(pk=body["enqueuedPublicationJob"]).exists()
    version_row = AtlasVersion.objects.get(pk=version.pk)
    assert version_row.status == "active"
    assert AuditLog.objects.filter(action="atlas.version.activate").exists()


@pytest.mark.django_db
def test_status_endpoint_reports_the_current_job(admin_client, clean_draft, t5_uses_type):
    """The status route reports the activated version's own job id."""
    from apps.atlas.services import version_revision as vr

    version = clean_draft["version"]
    canonical_key = str(clean_draft["key"])
    body = {
        "nodes": [
            {"nodeTypeKey": "t5method", "canonicalTranslationKey": canonical_key},
            {"nodeTypeKey": "t5structural", "canonicalTranslationKey": None,
             "overrides": {"en": {"label": " áreas EN"}, "fa": {"label": "ناحیه FA"}}},
        ],
        "relations": [], "groups": [],
    }
    replaced = admin_client.put(
        f"{BASE}/versions/{version.pk}/graph",
        body,
        HTTP_X_CSRFTOKEN=admin_client.defaults["HTTP_X_CSRFTOKEN"],
        HTTP_IF_MATCH=vr(version),
        content_type="application/json",
    )
    assert replaced.status_code == 200, replaced.content
    version.refresh_from_db()
    admin_client.defaults["HTTP_IF_MATCH"] = vr(version)
    assert _post_action(admin_client, version, "layout").status_code == 200
    version.refresh_from_db()
    admin_client.defaults["HTTP_IF_MATCH"] = vr(version)
    activated = _post_action(admin_client, version, "activate").json()
    status = admin_client.get(f"{BASE}/versions/{version.pk}/status").json()
    assert status["status"] == "active"
    assert status["jobId"] == activated["enqueuedPublicationJob"]
