"""BACKEND-190: server permission matrix for every admin mutation.

Fills the WORKFLOW-API-MAP coverage gaps: the shared staff/OTP/CSRF guard
pair is asserted with a NON-staff session (403 FORBIDDEN) for every mutating
operation the admin client ships, plus the preview-link guard matrix. The
enforcement already exists (`_require_admin_otp` / `_check_csrf`); these
tests pin it so a future refactor cannot silently drop a guard.
"""

from __future__ import annotations

import json

import pytest
from django.core.cache import cache
from django.test import Client

from apps.content.models import Landing, LifecycleStatus


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
    return Client(enforce_csrf_checks=True)


@pytest.fixture
def admin_api_client(csrf_client, admin_user, totp_device):
    """Staff session + verified OTP + CSRF token."""
    csrf_client.force_login(admin_user)
    session = csrf_client.session
    session["otp_device_id"] = totp_device.persistent_id
    session.save()
    token = csrf_client.get("/api/v1/admin/auth/csrf").json()["csrfToken"]
    csrf_client.defaults["HTTP_X_CSRFTOKEN"] = token
    return csrf_client


@pytest.fixture
def staff_api_client(csrf_client, user):
    """Authenticated NON-staff client — every admin op must answer 403."""
    csrf_client.force_login(user)
    return csrf_client


def _post_json(client, path, payload):
    return client.post(
        path,
        data=json.dumps(payload),
        content_type="application/json",
    )


def _put_json(client, path, payload):
    return client.put(
        path,
        data=json.dumps(payload),
        content_type="application/json",
    )


def _assert_forbidden(response, label: str) -> None:
    assert response.status_code == 403, label
    assert response.json()["code"] == "FORBIDDEN", label


@pytest.mark.django_db
def test_content_mutations_reject_non_staff(staff_api_client, db):
    landing = Landing.objects.create(
        locale="en", slug="home", title="Home", body="v1"
    )
    cases = [
        (
            "create",
            lambda: _post_json(
                staff_api_client,
                "/api/v1/admin/content/article",
                {"title": "T", "slug": "t", "locale": "en", "status": "draft"},
            ),
        ),
        (
            "update",
            lambda: _put_json(
                staff_api_client,
                f"/api/v1/admin/content/article/{landing.pk}",
                {"title": "T"},
            ),
        ),
        (
            "transition",
            lambda: _post_json(
                staff_api_client,
                f"/api/v1/admin/content/landing/{landing.pk}/transition",
                {"to": "published"},
            ),
        ),
        (
            "revisions-create",
            lambda: _post_json(
                staff_api_client,
                f"/api/v1/admin/content/landing/{landing.pk}/revisions",
                {"note": "n"},
            ),
        ),
        (
            "revisions-restore",
            lambda: staff_api_client.post(
                f"/api/v1/admin/content/landing/{landing.pk}/revisions/1/restore"
            ),
        ),
        (
            "bulk-archive",
            lambda: _post_json(
                staff_api_client,
                "/api/v1/admin/content/landing/bulk-archive",
                {"ids": [landing.pk]},
            ),
        ),
        (
            "preview-link",
            lambda: staff_api_client.post(
                f"/api/v1/admin/content/landing/{landing.pk}/preview-link"
            ),
        ),
    ]
    for label, call in cases:
        _assert_forbidden(call(), f"content:{label}")


@pytest.mark.django_db
def test_content_reads_reject_non_staff(staff_api_client, db):
    landing = Landing.objects.create(
        locale="en", slug="home", title="Home", body="v1"
    )
    cases = [
        ("list", lambda: staff_api_client.get("/api/v1/admin/content/article")),
        (
            "detail",
            lambda: staff_api_client.get(
                f"/api/v1/admin/content/landing/{landing.pk}"
            ),
        ),
        (
            "revisions",
            lambda: staff_api_client.get(
                f"/api/v1/admin/content/landing/{landing.pk}/revisions"
            ),
        ),
        (
            "schema",
            lambda: staff_api_client.get("/api/v1/admin/content/schema"),
        ),
    ]
    for label, call in cases:
        _assert_forbidden(call(), f"content-read:{label}")


@pytest.mark.django_db
def test_media_mutations_reject_non_staff(staff_api_client, db):
    cases = [
        (
            "upload",
            lambda: staff_api_client.post("/api/v1/admin/media"),
        ),
        (
            "update",
            lambda: _put_json(
                staff_api_client,
                "/api/v1/admin/media/1",
                {"title": "T"},
            ),
        ),
        (
            "delete",
            lambda: staff_api_client.delete("/api/v1/admin/media/1"),
        ),
        (
            "list",
            lambda: staff_api_client.get("/api/v1/admin/media"),
        ),
        (
            "licenses",
            lambda: staff_api_client.get("/api/v1/admin/media/licenses"),
        ),
        (
            "orphans",
            lambda: staff_api_client.get("/api/v1/admin/media/orphans"),
        ),
    ]
    for label, call in cases:
        _assert_forbidden(call(), f"media:{label}")


@pytest.mark.django_db
def test_site_and_approval_reads_reject_non_staff(staff_api_client, db):
    cases = [
        ("site-get", lambda: staff_api_client.get("/api/v1/admin/site")),
        (
            "site-put",
            lambda: _put_json(
                staff_api_client, "/api/v1/admin/site", {"brandName": "X"}
            ),
        ),
        (
            "approval-queue",
            lambda: staff_api_client.get("/api/v1/admin/approval-queue"),
        ),
    ]
    for label, call in cases:
        _assert_forbidden(call(), f"site:{label}")


@pytest.mark.django_db
def test_preview_link_guard_matrix(csrf_client, admin_user, totp_device, db):
    """Anonymous 401 / OTP 403 / CSRF 403 / unsupported entity 404."""
    landing = Landing.objects.create(
        locale="en", slug="home", title="Home", body="v1"
    )
    anonymous = Client(enforce_csrf_checks=True)
    response = anonymous.post(
        f"/api/v1/admin/content/landing/{landing.pk}/preview-link"
    )
    assert response.status_code == 401
    assert response.json()["code"] == "AUTH_REQUIRED"

    csrf_client.force_login(admin_user)  # staff, no verified OTP
    response = csrf_client.post(
        f"/api/v1/admin/content/landing/{landing.pk}/preview-link"
    )
    assert response.status_code == 403
    assert response.json()["code"] == "OTP_REQUIRED"

    # Verified OTP but no CSRF header.
    session = csrf_client.session
    session["otp_device_id"] = totp_device.persistent_id
    session.save()
    response = csrf_client.post(
        f"/api/v1/admin/content/landing/{landing.pk}/preview-link"
    )
    assert response.status_code == 403
    assert response.json()["code"] == "CSRF_FAILED"

    # Full session + CSRF: unsupported entity answers the contract 404
    # (allowlist is landing/profile/article — article WOULD succeed).
    from apps.content.models import Project  # noqa: PLC0415 — test-local import

    project = Project.objects.create(
        locale="en",
        slug="no-preview",
        title="No preview",
        status=LifecycleStatus.DRAFT,
    )
    token = csrf_client.get("/api/v1/admin/auth/csrf").json()["csrfToken"]
    csrf_client.defaults["HTTP_X_CSRFTOKEN"] = token
    response = csrf_client.post(
        f"/api/v1/admin/content/project/{project.pk}/preview-link"
    )
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"
    assert "not supported" in response.json()["message"]


@pytest.mark.django_db
def test_staff_without_otp_is_rejected_across_surfaces(
    csrf_client, admin_user, totp_device, db
):
    """Staff + verified-OTP pair: staff WITHOUT OTP gets 403 OTP_REQUIRED."""
    landing = Landing.objects.create(
        locale="en", slug="home", title="Home", body="v1"
    )
    csrf_client.force_login(admin_user)  # no OTP device session
    cases = [
        (
            "content-create",
            lambda: _post_json(
                csrf_client,
                "/api/v1/admin/content/article",
                {"title": "T", "slug": "t", "locale": "en", "status": "draft"},
            ),
        ),
        (
            "approval-queue",
            lambda: csrf_client.get("/api/v1/admin/approval-queue"),
        ),
        (
            "site-get",
            lambda: csrf_client.get("/api/v1/admin/site"),
        ),
        (
            "preview-link",
            lambda: csrf_client.post(
                f"/api/v1/admin/content/landing/{landing.pk}/preview-link"
            ),
        ),
    ]
    for label, call in cases:
        response = call()
        assert response.status_code == 403, label
        assert response.json()["code"] == "OTP_REQUIRED", label
