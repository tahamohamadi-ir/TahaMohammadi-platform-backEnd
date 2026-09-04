"""BACKEND-211 / ADMIN-281: seed policy persisted and served to the admin."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from django.core.cache import cache

from apps.content.services.content_seed_import import apply_seed_settings
from apps.siteconfig.models import SiteSettings


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
def admin_api_client(db, admin_user, totp_device):
    from django.test import Client

    client = Client()
    client.force_login(admin_user)
    session = client.session
    session["otp_device_id"] = totp_device.persistent_id
    session.save()
    token = client.get("/api/v1/admin/auth/csrf").json()["csrfToken"]
    client.defaults["HTTP_X_CSRFTOKEN"] = token
    return client


def _write_policy(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "seed-settings.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.mark.django_db
def test_apply_seed_settings_persists_policy(tmp_path, db):
    path = _write_policy(
        tmp_path,
        {"show_phone": False, "public_cv_download": False, "locale_fallback": "none"},
    )
    settings_row = apply_seed_settings(path)
    assert settings_row.seed_policy == {
        "show_phone": False,
        "public_cv_download": False,
        "locale_fallback": "none",
    }
    # Value-level defaults still applied alongside the stored policy.
    assert settings_row.contact_phone == ""
    assert settings_row.current_cv_media is None


@pytest.mark.django_db
def test_missing_policy_file_leaves_seed_policy_empty(tmp_path, db):
    settings_row = apply_seed_settings(tmp_path / "missing.json")
    assert settings_row.seed_policy is None


@pytest.mark.django_db
def test_admin_site_response_carries_seed_policy(admin_api_client, db, tmp_path):
    path = _write_policy(tmp_path, {"show_phone": False})
    apply_seed_settings(path)

    response = admin_api_client.get("/api/v1/admin/site")
    assert response.status_code == 200
    assert response.json()["seedPolicy"] == {"show_phone": False}


@pytest.mark.django_db
def test_seed_policy_is_not_writable_via_update(admin_api_client, db, tmp_path):
    path = _write_policy(tmp_path, {"show_phone": False})
    apply_seed_settings(path)

    current = admin_api_client.get("/api/v1/admin/site").json()
    response = admin_api_client.put(
        "/api/v1/admin/site",
        data=json.dumps({"seedPolicy": {"show_phone": True}}),
        content_type="application/json",
        HTTP_IF_MATCH=current["updatedAt"],
    )
    assert response.status_code == 200
    settings_row = SiteSettings.get_singleton()
    assert settings_row.seed_policy == {"show_phone": False}
