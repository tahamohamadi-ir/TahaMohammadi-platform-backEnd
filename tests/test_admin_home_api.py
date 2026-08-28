"""Admin home composition API tests (Track AB-02).

Covers GET/PUT/POST /api/v1/admin/home-modules/{locale}: auth matrix
(anonymous 401, non-staff 403, OTP guard, CSRF), the If-Match precondition
(428 missing / 409 stale), stable validation tokens (UNKNOWN_KEY,
DUPLICATE_ORDER, BAD_ENUM), replace-all semantics (upsert + delete-via-PUT),
fa/en isolation, and the audit row per successful PUT.
"""

import json

import pytest
from django.core.cache import cache
from django.test import Client
from django.utils import timezone

from apps.content.models import (
    HomeModule,
    HomeModuleKey,
    LifecycleStatus,
    Locale,
)
from apps.security.models import AuditLog

BASE = "/api/v1/admin/home-modules"


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


def _row(
    locale,
    key,
    order,
    *,
    visible=True,
    selection_mode="manual",
    provenance_note="",
    status=LifecycleStatus.PUBLISHED,
):
    return HomeModule.objects.create(
        locale=locale,
        key=key,
        visible=visible,
        order=order,
        selection_mode=selection_mode,
        provenance_note=provenance_note,
        status=status,
        published_at=timezone.now() if status == LifecycleStatus.PUBLISHED else None,
    )


def _slot(key, order, **overrides):
    payload = {
        "key": key,
        "visible": True,
        "order": order,
        "selection_mode": "manual",
        "provenance_note": "",
    }
    payload.update(overrides)
    return payload


def _put(client, locale, modules, *, if_match=None):
    headers = {}
    if if_match is not None:
        headers["HTTP_IF_MATCH"] = if_match
    return client.put(
        f"{BASE}/{locale}",
        data=json.dumps({"modules": modules}),
        content_type="application/json",
        **headers,
    )


def _validate(client, locale, modules):
    return client.post(
        f"{BASE}/{locale}/validate",
        data=json.dumps({"modules": modules}),
        content_type="application/json",
    )


def assert_json(response, status_code):
    assert response.status_code == status_code
    assert response["content-type"].startswith("application/json")
    return response.json()


def test_get_returns_all_rows_ordered_including_drafts(admin_api_client):
    _row(Locale.FA, HomeModuleKey.CTA, 3, visible=True)
    _row(Locale.FA, HomeModuleKey.IDENTITY, 1, visible=False)
    _row(Locale.FA, HomeModuleKey.GRAPH, 2, status=LifecycleStatus.DRAFT)
    data = assert_json(admin_api_client.get(f"{BASE}/fa"), 200)
    assert set(data) == {"revision", "modules"}
    assert [m["key"] for m in data["modules"]] == ["identity", "graph", "cta"]
    assert set(data["modules"][0]) == {
        "key",
        "visible",
        "order",
        "selection_mode",
        "provenance_note",
    }
    assert data["modules"][0]["visible"] is False
    assert data["modules"][1]["selection_mode"] == "manual"


def test_get_revision_is_iso_timestamp(admin_api_client):
    _row(Locale.FA, HomeModuleKey.CTA, 1)
    data = assert_json(admin_api_client.get(f"{BASE}/fa"), 200)
    assert data["revision"].endswith("Z")
    assert len(data["revision"]) == 24


def test_get_empty_locale_returns_empty_modules(admin_api_client):
    data = assert_json(admin_api_client.get(f"{BASE}/fa"), 200)
    assert data == {"revision": "", "modules": []}


def test_get_unknown_locale_404(admin_api_client):
    data = assert_json(admin_api_client.get(f"{BASE}/xx"), 404)
    assert data["code"] == "NOT_FOUND"


def test_put_roundtrip_get_modify_put_get(admin_api_client):
    _row(Locale.FA, HomeModuleKey.IDENTITY, 1)
    _row(Locale.FA, HomeModuleKey.CTA, 2)
    before = assert_json(admin_api_client.get(f"{BASE}/fa"), 200)
    modules = [
        _slot("cta", 1),
        _slot(
            "identity",
            2,
            visible=False,
            selection_mode="rule",
            provenance_note="hidden for now",
        ),
        _slot("graph", 3, selection_mode="hybrid"),
    ]
    saved = assert_json(_put(admin_api_client, "fa", modules, if_match=before["revision"]), 200)
    assert set(saved) == {"revision"}
    after = assert_json(admin_api_client.get(f"{BASE}/fa"), 200)
    assert [m["key"] for m in after["modules"]] == ["cta", "identity", "graph"]
    assert after["modules"][1] == {
        "key": "identity",
        "visible": False,
        "order": 2,
        "selection_mode": "rule",
        "provenance_note": "hidden for now",
    }
    assert after["revision"] != before["revision"]
    assert AuditLog.objects.filter(
        action="home_modules.update", model_name="home", object_id="fa"
    ).count() == 1


def test_put_without_if_match_is_428_precondition_required(admin_api_client):
    response = _put(admin_api_client, "fa", [_slot("cta", 1)])
    data = assert_json(response, 428)
    assert data["code"] == "PRECONDITION_REQUIRED"


def test_put_stale_if_match_is_409_stale_revision(admin_api_client):
    _row(Locale.FA, HomeModuleKey.CTA, 1)
    data = assert_json(
        _put(admin_api_client, "fa", [_slot("cta", 1)], if_match="2020-01-01T00:00:00.000Z"),
        409,
    )
    assert data["code"] == "STALE_REVISION"


def test_put_unknown_key_rejected_and_atomic(admin_api_client):
    _row(Locale.FA, HomeModuleKey.CTA, 1)
    before = assert_json(admin_api_client.get(f"{BASE}/fa"), 200)
    modules = [_slot("cta", 1), _slot("bogus-slot", 2)]
    data = assert_json(_put(admin_api_client, "fa", modules, if_match=before["revision"]), 400)
    assert data["fields"]["modules[1].key"] == ["UNKNOWN_KEY"]
    after = assert_json(admin_api_client.get(f"{BASE}/fa"), 200)
    assert [m["key"] for m in after["modules"]] == ["cta"]
    assert after["revision"] == before["revision"]


def test_put_duplicate_order_rejected(admin_api_client):
    modules = [_slot("cta", 1), _slot("identity", 1)]
    data = assert_json(_put(admin_api_client, "fa", modules, if_match='""'), 400)
    assert data["fields"]["modules"] == ["DUPLICATE_ORDER"]


def test_put_order_gap_rejected(admin_api_client):
    modules = [_slot("cta", 1), _slot("identity", 3)]
    data = assert_json(_put(admin_api_client, "fa", modules, if_match='""'), 400)
    assert data["fields"]["modules"] == ["DUPLICATE_ORDER"]


def test_put_bad_selection_mode_rejected(admin_api_client):
    modules = [_slot("cta", 1, selection_mode="auto")]
    data = assert_json(_put(admin_api_client, "fa", modules, if_match='""'), 400)
    assert data["fields"]["modules[0].selection_mode"] == ["BAD_ENUM"]


def test_put_duplicate_key_rejected(admin_api_client):
    modules = [_slot("cta", 1), _slot("cta", 2)]
    data = assert_json(_put(admin_api_client, "fa", modules, if_match='""'), 400)
    assert data["fields"]["modules[1].key"] == ["DUPLICATE_KEY"]


def test_put_empty_locale_bootstraps_from_empty_revision(admin_api_client):
    modules = [_slot("cta", 1, visible=True)]
    saved = assert_json(_put(admin_api_client, "fa", modules, if_match='""'), 200)
    assert saved["revision"] != ""
    after = assert_json(admin_api_client.get(f"{BASE}/fa"), 200)
    assert [m["key"] for m in after["modules"]] == ["cta"]


def test_put_delete_via_omitted_row(admin_api_client):
    _row(Locale.FA, HomeModuleKey.CTA, 1)
    _row(Locale.FA, HomeModuleKey.IDENTITY, 2)
    before = assert_json(admin_api_client.get(f"{BASE}/fa"), 200)
    modules = [_slot("identity", 1)]
    assert_json(_put(admin_api_client, "fa", modules, if_match=before["revision"]), 200)
    after = assert_json(admin_api_client.get(f"{BASE}/fa"), 200)
    assert [m["key"] for m in after["modules"]] == ["identity"]
    assert not HomeModule.objects.filter(locale=Locale.FA, key=HomeModuleKey.CTA).exists()


def test_put_unknown_locale_404(admin_api_client):
    data = assert_json(
        _put(admin_api_client, "xx", [_slot("cta", 1)], if_match='""'), 404
    )
    assert data["code"] == "NOT_FOUND"


def test_validate_happy_returns_empty_dict(admin_api_client):
    modules = [_slot("cta", 1), _slot("identity", 2, selection_mode="hybrid")]
    assert assert_json(_validate(admin_api_client, "fa", modules), 200) == {}
    assert not HomeModule.objects.filter(locale=Locale.FA).exists()


def test_validate_reports_all_tokens_at_once(admin_api_client):
    modules = [
        _slot("bogus", 2, selection_mode="auto"),
        _slot("cta", 2, selection_mode="hybrid"),
    ]
    data = assert_json(_validate(admin_api_client, "fa", modules), 400)
    assert data["code"] == "VALIDATION"
    assert data["fields"]["modules[0].key"] == ["UNKNOWN_KEY"]
    assert data["fields"]["modules[0].selection_mode"] == ["BAD_ENUM"]
    assert data["fields"]["modules"] == ["DUPLICATE_ORDER"]


def test_validate_unknown_locale_404(admin_api_client):
    data = assert_json(
        _validate(admin_api_client, "xx", [_slot("cta", 1)]), 404
    )
    assert data["code"] == "NOT_FOUND"


def test_anonymous_get_is_401_auth_required():
    data = assert_json(Client().get(f"{BASE}/fa"), 401)
    assert data["code"] == "AUTH_REQUIRED"


def test_non_staff_user_is_403_forbidden(staff_api_client):
    data = assert_json(staff_api_client.get(f"{BASE}/fa"), 403)
    assert data["code"] == "FORBIDDEN"


def test_staff_without_verified_otp_is_403_otp_required(csrf_client, admin_user):
    csrf_client.force_login(admin_user)
    data = assert_json(csrf_client.get(f"{BASE}/fa"), 403)
    assert data["code"] == "OTP_REQUIRED"


def test_put_without_csrf_token_is_403_csrf_failed(csrf_client, admin_user, totp_device):
    csrf_client.force_login(admin_user)
    session = csrf_client.session
    session["otp_device_id"] = totp_device.persistent_id
    session.save()
    response = csrf_client.put(
        f"{BASE}/fa",
        data=json.dumps({"modules": []}),
        content_type="application/json",
    )
    data = assert_json(response, 403)
    assert data["code"] == "CSRF_FAILED"


def test_fa_and_en_are_isolated(admin_api_client):
    _row(Locale.FA, HomeModuleKey.IDENTITY, 1)
    before_en = assert_json(admin_api_client.get(f"{BASE}/en"), 200)
    assert before_en == {"revision": "", "modules": []}
    modules = [_slot("projects", 1)]
    assert_json(
        _put(admin_api_client, "en", modules, if_match=before_en["revision"]), 200
    )
    fa_after = assert_json(admin_api_client.get(f"{BASE}/fa"), 200)
    assert [m["key"] for m in fa_after["modules"]] == ["identity"]
    en_after = assert_json(admin_api_client.get(f"{BASE}/en"), 200)
    assert [m["key"] for m in en_after["modules"]] == ["projects"]
    assert HomeModule.objects.filter(locale=Locale.EN).count() == 1
