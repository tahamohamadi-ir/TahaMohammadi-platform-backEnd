"""Bulk archive + feature flag (Wave 5 / DEFER-0032 / S4)."""

from __future__ import annotations

import pytest
from django.test import Client, override_settings

from apps.content.models import Landing
from apps.security.models import AuditLog


@pytest.fixture
def totp_device(db, admin_user):
    from django_otp.plugins.otp_totp.models import TOTPDevice

    return TOTPDevice.objects.create(user=admin_user, name="default", confirmed=True)


@pytest.fixture
def csrf_client():
    return Client(enforce_csrf_checks=True)


@pytest.fixture
def admin_api_client(csrf_client, admin_user, totp_device):
    csrf_client.force_login(admin_user)
    session = csrf_client.session
    session["otp_device_id"] = totp_device.persistent_id
    session.save()
    token = csrf_client.get("/api/v1/admin/auth/csrf").json()["csrfToken"]
    csrf_client.defaults["HTTP_X_CSRFTOKEN"] = token
    return csrf_client


@pytest.mark.django_db
def test_bulk_archive_disabled_returns_404(admin_api_client):
    landing = Landing.objects.create(
        locale="en",
        slug="bulk-off",
        title="Bulk Off",
        status="draft",
    )
    with override_settings(FEATURE_ADMIN_BULK_ARCHIVE=False):
        response = admin_api_client.post(
            "/api/v1/admin/content/landing/bulk-archive",
            data={"ids": [landing.pk], "reason": "test"},
            content_type="application/json",
        )
    assert response.status_code == 404
    assert response.json()["code"] == "FEATURE_DISABLED"
    landing.refresh_from_db()
    assert landing.status == "draft"


@pytest.mark.django_db
@override_settings(FEATURE_ADMIN_BULK_ARCHIVE=True)
def test_bulk_archive_archives_and_audits(admin_api_client):
    a = Landing.objects.create(
        locale="en", slug="bulk-a", title="Bulk A", status="draft"
    )
    b = Landing.objects.create(
        locale="en", slug="bulk-b", title="Bulk B", status="published"
    )
    c = Landing.objects.create(
        locale="en", slug="bulk-c", title="Bulk C", status="archived"
    )
    response = admin_api_client.post(
        "/api/v1/admin/content/landing/bulk-archive",
        data={"ids": [a.pk, b.pk, c.pk], "reason": "qa bulk"},
        content_type="application/json",
    )
    assert response.status_code == 200
    body = response.json()
    assert body["archived"] == 2
    assert body["skipped"] == 1
    assert set(body["ids"]) == {a.pk, b.pk}

    a.refresh_from_db()
    b.refresh_from_db()
    c.refresh_from_db()
    assert a.status == "archived"
    assert b.status == "archived"
    assert c.status == "archived"

    assert AuditLog.objects.filter(action="lifecycle.bulk_archive").exists()
    assert AuditLog.objects.filter(
        action="lifecycle.draft->archived", object_id=str(a.pk)
    ).exists()
    assert AuditLog.objects.filter(
        action="lifecycle.published->archived", object_id=str(b.pk)
    ).exists()


@pytest.mark.django_db
def test_auth_me_includes_feature_flags(admin_api_client):
    response = admin_api_client.get("/api/v1/admin/auth/me")
    assert response.status_code == 200
    flags = response.json()["featureFlags"]
    assert "admin_bulk_archive" in flags
    assert isinstance(flags["admin_bulk_archive"], bool)
