"""Custom admin API auth tests (ADR-0026, ADM-0/ADM-1 foundation).

Covers the /api/v1/admin/auth/* + /dashboard/summary contract: CSRF, login
(no-OTP, OTP required, valid OTP, bad credentials, non-staff), logout, me,
dashboard guard, CSRF enforcement and login rate limiting.
"""

import time
from datetime import timedelta

import pytest
from django.core.cache import cache
from django.test import Client
from django.utils import timezone

from apps.content.models import Article, LifecycleStatus, Locale
from apps.security.models import AuditLog


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


def _login_payload(email="admin@example.com", password="test-pass-123", otp_token=None):
    payload = {"email": email, "password": password}
    if otp_token is not None:
        payload["otpToken"] = otp_token
    return payload


def _totp_token(device) -> str:
    """Current OTP token for a TOTPDevice (mirrors django_otp verify logic)."""
    from django_otp.oath import TOTP

    totp = TOTP(device.bin_key, device.step, device.t0, device.digits, device.drift)
    totp.time = time.time()
    return str(totp.token()).zfill(device.digits)


def _login_client():
    """A client primed with a real CSRF token (exercises the full contract)."""
    client = Client(enforce_csrf_checks=True)
    token = client.get("/api/v1/admin/auth/csrf").json()["csrfToken"]
    client.defaults["HTTP_X_CSRFTOKEN"] = token
    return client


def test_csrf_endpoint_returns_token(csrf_client):
    response = csrf_client.get("/api/v1/admin/auth/csrf")
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body["csrfToken"], str)
    assert len(body["csrfToken"]) >= 32


def test_login_success_without_otp(admin_user):
    client = _login_client()
    response = client.post(
        "/api/v1/admin/auth/login",
        data=_login_payload(),
        content_type="application/json",
    )
    assert response.status_code == 200
    body = response.json()
    assert body["email"] == "admin@example.com"
    assert body["isStaff"] is True
    assert body["mfaEnrolled"] is False
    assert body["otpVerified"] is False


def test_login_bad_password(admin_user):
    client = _login_client()
    response = client.post(
        "/api/v1/admin/auth/login",
        data=_login_payload(password="wrong-password-123"),
        content_type="application/json",
    )
    assert response.status_code == 401
    assert response.json()["code"] == "AUTH_FAILED"


def test_login_non_staff_rejected(user):
    client = _login_client()
    response = client.post(
        "/api/v1/admin/auth/login",
        data=_login_payload(email="tester@example.com"),
        content_type="application/json",
    )
    assert response.status_code == 401
    assert response.json()["code"] == "AUTH_FAILED"


def test_login_requires_otp_when_enrolled(admin_user, totp_device):
    client = _login_client()
    response = client.post(
        "/api/v1/admin/auth/login",
        data=_login_payload(),
        content_type="application/json",
    )
    assert response.status_code == 401
    assert response.json()["code"] == "AUTH_FAILED"


def test_login_with_valid_otp_then_me(admin_user, totp_device):
    client = _login_client()
    login_response = client.post(
        "/api/v1/admin/auth/login",
        data=_login_payload(otp_token=_totp_token(totp_device)),
        content_type="application/json",
    )
    assert login_response.status_code == 200
    body = login_response.json()
    assert body["mfaEnrolled"] is True
    assert body["otpVerified"] is True

    me_response = client.get("/api/v1/admin/auth/me")
    assert me_response.status_code == 200
    assert me_response.json()["email"] == "admin@example.com"


def test_login_with_wrong_otp(admin_user, totp_device):
    client = _login_client()
    response = client.post(
        "/api/v1/admin/auth/login",
        data=_login_payload(otp_token="000000"),
        content_type="application/json",
    )
    assert response.status_code == 401
    assert response.json()["code"] == "AUTH_FAILED"


def test_login_with_recovery_code_once_only(admin_user, totp_device):
    from apps.security.recovery import issue_recovery_codes

    codes = issue_recovery_codes(admin_user, ip="")
    code = codes[0]

    client = _login_client()
    first = client.post(
        "/api/v1/admin/auth/login",
        data=_login_payload(otp_token=code),
        content_type="application/json",
    )
    assert first.status_code == 200
    assert first.json()["otpVerified"] is True

    client.post("/api/v1/admin/auth/logout")

    second = _login_client().post(
        "/api/v1/admin/auth/login",
        data=_login_payload(otp_token=code),
        content_type="application/json",
    )
    assert second.status_code == 401
    assert second.json()["code"] == "AUTH_FAILED"


def test_me_unauthenticated(csrf_client):
    response = csrf_client.get("/api/v1/admin/auth/me")
    assert response.status_code == 401
    assert response.json()["code"] == "AUTH_REQUIRED"


def test_logout_ends_session(admin_api_client):
    response = admin_api_client.post("/api/v1/admin/auth/logout")
    assert response.status_code == 200
    assert response.json()["ok"] is True
    me = admin_api_client.get("/api/v1/admin/auth/me")
    assert me.status_code == 401
    assert AuditLog.objects.filter(action="admin.logout").exists()


def test_dashboard_summary_requires_otp(csrf_client, admin_user):
    csrf_client.force_login(admin_user)  # staff but NO otp session
    response = csrf_client.get("/api/v1/admin/dashboard/summary")
    assert response.status_code == 403
    assert response.json()["code"] == "OTP_REQUIRED"


def test_dashboard_summary_counts(admin_api_client):
    Article.objects.create(
        locale=Locale.EN,
        slug="draft-article",
        title="Draft article",
        body="",
        status=LifecycleStatus.DRAFT,
    )
    Article.objects.create(
        locale=Locale.FA,
        slug="published-article",
        title="Published article",
        body="",
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now() - timedelta(days=1),
    )
    response = admin_api_client.get("/api/v1/admin/dashboard/summary")
    assert response.status_code == 200
    body = response.json()
    assert body["contentCounts"]["article"] == 2
    assert body["drafts"] >= 1
    assert body["published"] >= 1


def test_csrf_required_for_login(csrf_client, admin_user):
    # CSRF-enforcing client with no X-CSRFToken header -> 403 CSRF_FAILED
    response = csrf_client.post(
        "/api/v1/admin/auth/login",
        data=_login_payload(),
        content_type="application/json",
    )
    assert response.status_code == 403
    assert response.json()["code"] == "CSRF_FAILED"


def test_login_rate_limited_and_audited(admin_user):
    client = _login_client()
    for _ in range(5):
        response = client.post(
            "/api/v1/admin/auth/login",
            data=_login_payload(password="wrong-password-123"),
            content_type="application/json",
        )
        assert response.status_code == 401
    blocked = client.post(
        "/api/v1/admin/auth/login",
        data=_login_payload(password="wrong-password-123"),
        content_type="application/json",
    )
    assert blocked.status_code == 429
    assert blocked.json()["code"] == "RATE_LIMITED"
    assert AuditLog.objects.filter(action="login.blocked").exists()
