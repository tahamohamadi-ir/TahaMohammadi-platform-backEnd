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
    """Invalid/absent body → 422 from the schema; NOT guard evidence (card A)."""
    response = admin_client.post(
        f"{BASE}/versions/{draft_version.pk}/layout",
        {},
        HTTP_X_CSRFTOKEN=admin_client.defaults["HTTP_X_CSRFTOKEN"],
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
    assert AuditLog.objects.filter(action="atlas.layout.replace").exists()


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


# ---------------------------------------------------------------------------
# Task 3: node endpoints + canonical picker (plan Task 3) — written RED first.
# ---------------------------------------------------------------------------


def _create_node(client, version, payload):
    """POST a node with a valid session/CSRF/If-Match (card section A)."""
    return client.post(
        f"{BASE}/versions/{version.pk}/nodes",
        payload,
        HTTP_X_CSRFTOKEN=client.defaults.get("HTTP_X_CSRFTOKEN"),
        HTTP_IF_MATCH=version_revision(version),
        content_type="application/json",
    )


@pytest.fixture
def method_pair(db):
    """A published EN+FA Method pair and a method-canonical node type."""
    from django.utils import timezone

    from apps.content.models import Method

    method_type = _node_type(
        key="method", canonical_source="method", label_en="Method", label_fa="روش"
    )
    from uuid import uuid4
    key = uuid4()
    now = timezone.now()
    en = Method.objects.create(
        locale="en", slug="t3-method-en", title="T3 method EN",
        status="published", published_at=now, translation_key=key,
    )
    fa = Method.objects.create(
        locale="fa", slug="t3-method-fa", title="روش تی‌۳",
        status="published", published_at=now, translation_key=key,
    )
    return {"type": method_type, "en": en, "fa": fa, "translation_key": key}


@pytest.fixture
def draft_only_method(db):
    """A Method whose ONLY row is a draft — never publishable (Task 3 pin)."""
    from uuid import uuid4

    from apps.content.models import Method

    fake_key = uuid4()
    row = Method.objects.create(
        locale="en", slug="t3-draft-only", title="Draft only",
        status="draft", translation_key=fake_key,
    )
    row.refresh_from_db()
    _node_type(key="method", canonical_source="method", label_en="Method", label_fa="روش")
    return row


@pytest.mark.django_db
def test_create_node_validates_the_canonical_pair(admin_client, draft_version, method_pair):
    version = draft_version
    version.nodes.all().delete()  # a clean topology for this test
    response = _create_node(admin_client, version, {
        "nodeTypeKey": "method", "canonicalSource": "method",
        "canonicalTranslationKey": str(method_pair["translation_key"]), "importance": 45,
    })
    assert response.status_code == 201, response.content
    assert response.json()["publicKey"].startswith("method-")
    assert response.json()["localeStatus"] == {"en": True, "fa": True}


@pytest.mark.django_db
def test_create_node_rejects_an_unpublishable_canonical_record(
    admin_client, draft_version, draft_only_method
):
    version = draft_version
    response = _create_node(admin_client, version, {
        "nodeTypeKey": "method", "canonicalSource": "method",
        "canonicalTranslationKey": str(draft_only_method.translation_key),
    })
    assert response.status_code == 400
    assert response.json()["fields"]["canonicalTranslationKey"]


@pytest.mark.django_db
def test_pin_requires_both_coordinates(admin_client, draft_version):
    node = _node(version=draft_version, node_type=_default_node_type(), visible=True)
    response = admin_client.patch(
        f"{BASE}/versions/{draft_version.pk}/nodes/{node.public_key}",
        {"importance": 50, "pin": {"x": 4.0}},
        HTTP_X_CSRFTOKEN=admin_client.defaults["HTTP_X_CSRFTOKEN"],
        HTTP_IF_MATCH=version_revision(draft_version),
        content_type="application/json",
    )
    assert response.status_code == 400
    assert "pin" in response.json()["fields"]


@pytest.mark.django_db
def test_node_public_key_is_never_editable(admin_client, draft_version):
    node = _node(version=draft_version, node_type=_default_node_type(), visible=True)
    response = admin_client.patch(
        f"{BASE}/versions/{draft_version.pk}/nodes/{node.public_key}",
        {"importance": 50, "publicKey": "attacker-chosen"},
        HTTP_X_CSRFTOKEN=admin_client.defaults["HTTP_X_CSRFTOKEN"],
        HTTP_IF_MATCH=version_revision(draft_version),
        content_type="application/json",
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_canonical_candidates_are_publish_gated(admin_client, draft_only_method):
    rows = admin_client.get(f"{BASE}/canonical-candidates?source=method").json()
    assert all(row["publishable"]["en"] or row["publishable"]["fa"] for row in rows)
    assert str(draft_only_method.translation_key) not in {
        row["translationKey"] for row in rows if row["publishable"]["en"]
    }
