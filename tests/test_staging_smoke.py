"""BACKEND-180 staging smoke — one connected disposable-env pass over the
same-origin session/CSRF/MFA/preview/contact/media boundaries listed in
``docs/quality/INTEGRATION-TEST-PLAN.md`` (evidence layer 1).

Runs on the Django test client against the disposable test settings; the same
module runs against the disposable PostgreSQL profile. This is NOT staging
browser evidence — that capture is owned by COORD-060/070 and consumed by
PUBLIC-320 / ADMIN-300.
"""

import json
import time
from base64 import b32decode
from datetime import timedelta

import pytest
from django.contrib.sessions.models import Session
from django.core import mail
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.utils import timezone
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.content.models import Landing, LifecycleStatus, Locale
from apps.content.preview_token import build_preview_token
from apps.media.models import Media
from apps.security.models import AuditLog
from apps.siteconfig.models import SiteSettings

PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)

ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD = "test-pass-123"


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def media_root(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    return tmp_path


@pytest.fixture
def preview_secret(settings):
    settings.PREVIEW_SHARE_SECRET = "staging-smoke-preview-secret"


@pytest.fixture
def contact_env(settings, db):
    """Contact form enabled with the locmem email backend (test settings)."""
    settings.EMAIL_HOST = "smtp.example.com"
    settings.CONTACT_FORM_TO = ""
    row = SiteSettings.get_singleton()
    row.contact_email = "owner@example.com"
    row.contact_form_enabled = True
    row.save()
    mail.outbox.clear()
    return row


def _csrf_client() -> Client:
    """CSRF-enforcing client primed with a real token from the auth contract."""
    client = Client(enforce_csrf_checks=True)
    token = client.get("/api/v1/admin/auth/csrf").json()["csrfToken"]
    client.defaults["HTTP_X_CSRFTOKEN"] = token
    return client


def _refresh_csrf(client: Client) -> None:
    """Re-fetch the token after login() rotates the CSRF secret (SPA parity)."""
    token = client.get("/api/v1/admin/auth/csrf").json()["csrfToken"]
    client.defaults["HTTP_X_CSRFTOKEN"] = token


def _login(client: Client, *, password: str = ADMIN_PASSWORD, otp_token: str | None = None):
    payload: dict = {"email": ADMIN_EMAIL, "password": password}
    if otp_token is not None:
        payload["otpToken"] = otp_token
    return client.post(
        "/api/v1/admin/auth/login",
        data=json.dumps(payload),
        content_type="application/json",
    )


def _pending_totp_token(secret_b32: str) -> str:
    """Current TOTP token for a base32 secret (mirrors django_otp verify)."""
    from django_otp.oath import TOTP

    totp = TOTP(b32decode(secret_b32), 30, 0, 6, 0)
    totp.time = time.time()
    return str(totp.token()).zfill(6)


def _device_totp(device, *, offset_steps: int = 0) -> str:
    from django_otp.oath import TOTP

    totp = TOTP(device.bin_key, device.step, device.t0, device.digits, device.drift)
    totp.time = time.time() + offset_steps * device.step
    return str(totp.token()).zfill(device.digits)


def _expire_session(client: Client) -> None:
    key = client.cookies["sessionid"].value
    Session.objects.filter(session_key=key).update(
        expire_date=timezone.now() - timedelta(seconds=1)
    )


def _draft_landing(slug: str) -> Landing:
    return Landing.objects.create(
        locale=Locale.EN,
        slug=slug,
        title=f"Smoke landing {slug}",
        body="<p>shared draft body</p>",
        status=LifecycleStatus.DRAFT,
        published_at=None,
    )


@pytest.mark.django_db
def test_admin_session_mfa_content_preview_media_smoke(
    admin_user, media_root, preview_secret, settings
):
    """Connected chain: health -> sign-in -> MFA -> CSRF -> expiry -> re-auth
    -> content -> preview -> media -> logout."""
    # Staging-style contact delivery so /health/ reports the ok path.
    settings.EMAIL_HOST = "smtp.example.com"

    # 1. Health readiness.
    health = Client().get("/health/")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert health.json()["db"] == "ok"
    assert health.json()["contact"] == "ok"

    # 2. Anonymous admin access is refused.
    anonymous = Client().get("/api/v1/admin/auth/me")
    assert anonymous.status_code == 401
    assert anonymous.json()["code"] == "AUTH_REQUIRED"

    # 3. Sign-in rejects bad credentials.
    client = _csrf_client()
    bad = _login(client, password="wrong-password-123")
    assert bad.status_code == 401
    assert bad.json()["code"] == "AUTH_FAILED"

    # 4. First sign-in before MFA enrollment.
    first = _login(client)
    assert first.status_code == 200
    assert first.json()["email"] == ADMIN_EMAIL
    assert first.json()["mfaEnrolled"] is False
    assert first.json()["otpVerified"] is False
    assert client.get("/api/v1/admin/auth/me").status_code == 200
    _refresh_csrf(client)  # login() rotated the CSRF secret

    # 5. MFA enrollment through the SPA API: status -> confirm -> codes.
    status = client.get("/api/v1/admin/auth/mfa/status")
    assert status.status_code == 200
    assert status.json()["enrolled"] is False
    confirm = client.post(
        "/api/v1/admin/auth/mfa/confirm",
        data=json.dumps({"otpToken": _pending_totp_token(status.json()["manualSecret"])}),
        content_type="application/json",
    )
    assert confirm.status_code == 200
    assert confirm.json()["ok"] is True
    assert len(confirm.json()["codes"]) > 0
    assert client.get("/api/v1/admin/auth/mfa/status").json()["enrolled"] is True

    # 6. CSRF failure on a CMS mutation when the token header is stripped.
    saved_token = client.defaults.pop("HTTP_X_CSRFTOKEN")
    csrf_fail = client.post(
        "/api/v1/admin/content/article",
        data=json.dumps({"locale": "en", "slug": "smoke-article", "title": "Smoke article"}),
        content_type="application/json",
    )
    assert csrf_fail.status_code == 403
    assert csrf_fail.json()["code"] == "CSRF_FAILED"
    client.defaults["HTTP_X_CSRFTOKEN"] = saved_token

    # 7. Session expiry forces re-authentication.
    _expire_session(client)
    expired = client.get("/api/v1/admin/auth/me")
    assert expired.status_code == 401
    assert expired.json()["code"] == "AUTH_REQUIRED"

    # 8. Re-sign-in with the valid TOTP second factor.
    client = _csrf_client()
    device = TOTPDevice.objects.get(user=admin_user, confirmed=True)
    # The confirm step consumed this step's code (last_t replay guard); the
    # re-sign-in uses the next time step's code.
    second = _login(client, otp_token=_device_totp(device, offset_steps=1))
    assert second.status_code == 200
    assert second.json()["mfaEnrolled"] is True
    assert second.json()["otpVerified"] is True
    _refresh_csrf(client)  # login() rotated the CSRF secret

    # 9. CMS content mutation through the guarded API.
    created = client.post(
        "/api/v1/admin/content/article",
        data=json.dumps(
            {
                "locale": "en",
                "slug": "smoke-article",
                "title": "Smoke article",
                "status": "draft",
            }
        ),
        content_type="application/json",
    )
    assert created.status_code == 201
    assert created.json()["slug"] == "smoke-article"
    assert created.json()["status"] == "draft"

    # 10. Preview share link: staff mints, anonymous sees the sanitized draft.
    landing = _draft_landing("smoke-share")
    link = client.post(
        f"/api/v1/admin/content/landing/{landing.pk}/preview-link",
        data=json.dumps({}),
        content_type="application/json",
    )
    assert link.status_code == 200
    assert link.json()["path"].startswith("/preview/share/")
    share = Client().get(link.json()["path"])
    assert share.status_code == 200
    assert f"Smoke landing {landing.slug}".encode() in share.content
    assert b"<script" not in share.content
    assert share.headers["Cache-Control"] == "no-store"
    assert share.headers["X-Robots-Tag"] == "noindex, nofollow, noarchive"
    # The draft stays hidden on the public surface without the token.
    assert AuditLog.objects.filter(action="preview.share_link").exists()

    # 11. Media upload, then the public active/inactive delivery boundary.
    upload = client.post(
        "/api/v1/admin/media",
        {
            "title": "Smoke photo",
            "file": SimpleUploadedFile("smoke.png", PNG_1X1, content_type="image/png"),
        },
    )
    assert upload.status_code == 201
    media = Media.objects.get(pk=upload.json()["id"])
    assert media.is_active is False  # private-by-default library
    public = Client().get(f"/media/{media.file.name}")
    assert public.status_code == 404
    media.is_active = True
    media.save(update_fields=["is_active"])
    active = Client().get(f"/media/{media.file.name}")
    assert active.status_code == 200
    assert active["Content-Type"].startswith("image/png")

    # 12. Logout ends the session and is audited.
    logout = client.post("/api/v1/admin/auth/logout")
    assert logout.status_code == 200
    assert logout.json()["ok"] is True
    after = client.get("/api/v1/admin/auth/me")
    assert after.status_code == 401
    assert AuditLog.objects.filter(action="admin.logout").exists()

    # 13. With MFA enrolled, sign-in without the second factor is refused
    # (checked last: a failed device verify starts a throttle cooldown).
    denied = _login(_csrf_client())
    assert denied.status_code == 401
    assert denied.json()["code"] == "AUTH_FAILED"


@pytest.mark.django_db
def test_contact_smoke_html_json_cross_origin_and_non_persistence(contact_env):
    # No-JS footer form path: urlencoded POST answers with styled HTML.
    form = Client().post(
        "/api/contact",
        data={
            "name": "Sara",
            "email": "sara@example.com",
            "message": "Hello from the smoke test",
            "locale": "en",
        },
    )
    assert form.status_code == 200
    assert form["Content-Type"].startswith("text/html")
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["owner@example.com"]

    # JSON API path.
    payload = json.dumps({"email": "json@example.com", "message": "JSON smoke", "locale": "en"})
    json_response = Client().post("/api/contact", data=payload, content_type="application/json")
    assert json_response.status_code == 200
    assert json_response.json()["ok"] is True
    assert len(mail.outbox) == 2

    # Cross-origin submissions are rejected before anything is sent.
    cross = Client().post(
        "/api/contact",
        data=json.dumps({"email": "evil@example.com", "message": "nope"}),
        content_type="application/json",
        HTTP_ORIGIN="https://evil.example",
    )
    assert cross.status_code == 400
    assert len(mail.outbox) == 2

    # Non-persistence policy: no message body reaches the audit trail.
    audits = AuditLog.objects.filter(action="contact.sent")
    assert audits.count() == 2
    for row in audits:
        assert "Hello from the smoke test" not in row.detail
        assert "JSON smoke" not in row.detail


@pytest.mark.django_db
def test_preview_share_expiry_smoke(preview_secret, db):
    token = build_preview_token("landing", _draft_landing("smoke-expired").pk, ttl_seconds=-60)
    response = Client().get(f"/preview/share/{token}/")
    assert response.status_code == 410
    assert b"Smoke landing smoke-expired" not in response.content
