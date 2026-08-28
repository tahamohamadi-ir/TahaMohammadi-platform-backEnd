"""Admin-security slice tests — audit log, read-only admin, login rate limit, rich text (P3)."""

import pytest
from django.contrib import admin
from django.core.cache import cache
from django.test import Client, RequestFactory

from apps.security.admin import AuditLogAdmin
from apps.security.middleware import LOGIN_RATE_LIMIT
from apps.security.models import AuditLog

SECRET_PASSWORD = "super-secret-password-xyz"


@pytest.fixture
def admin_client(admin_user):
    client = Client()
    client.force_login(admin_user)
    return client


def _request_for(user):
    request = RequestFactory().get("/staff/security/auditlog/")
    request.user = user
    return request


def _admin():
    return AuditLogAdmin(AuditLog, admin.site)


class TestAuditLogModel:
    def test_str_includes_action(self, db, user):
        log = AuditLog.objects.create(user=user, action="login.success")
        assert str(log).startswith("login.success")

    def test_created_at_set_on_create(self, db):
        log = AuditLog.objects.create(action="login.failed")
        assert log.created_at is not None


class TestAuditMiddleware:
    def test_login_success_recorded(self, db, admin_user):
        client = Client(REMOTE_ADDR="203.0.113.10")
        response = client.post(
            "/staff/login/",
            {"username": admin_user.email, "password": "test-pass-123"},
        )
        assert response.status_code == 302
        row = AuditLog.objects.get(action="login.success")
        assert row.user == admin_user
        assert row.object_id == str(admin_user.pk)
        assert row.ip == "203.0.113.10"

    def test_login_failure_recorded(self, db, user):
        client = Client(REMOTE_ADDR="203.0.113.11")
        response = client.post(
            "/staff/login/",
            {"username": user.email, "password": "wrong-password"},
        )
        assert response.status_code == 200
        row = AuditLog.objects.get(action="login.failed")
        assert row.user is None
        assert row.object_id == ""
        assert row.ip == "203.0.113.11"

    def test_admin_mutation_recorded(self, db, admin_client, admin_user):
        from django_otp.plugins.otp_totp.models import TOTPDevice

        device = TOTPDevice.objects.create(
            user=admin_user, name="default", confirmed=True
        )
        session = admin_client.session
        session["otp_device_id"] = device.persistent_id
        session.save()
        response = admin_client.post("/staff/pages/1234/edit/")
        assert response.status_code == 404
        row = AuditLog.objects.get(action="admin.mutation")
        assert row.user == admin_user
        assert row.model_name == "pages"
        assert row.object_id == "1234"
        assert row.detail.startswith("POST /staff/pages/1234/edit/")

    def test_health_and_static_never_audited(self, db, client):
        client.get("/health/")
        client.post("/health/")
        client.get("/static/x.css")
        assert AuditLog.objects.count() == 0

    def test_anonymous_admin_post_not_audited(self, db):
        client = Client()
        client.post("/staff/pages/1234/edit/")
        assert AuditLog.objects.count() == 0

    def test_password_never_stored_in_audit(self, db, user):
        client = Client(REMOTE_ADDR="203.0.113.12")
        client.post(
            "/staff/login/",
            {"username": user.email, "password": SECRET_PASSWORD},
        )
        assert AuditLog.objects.count() == 1
        assert all(SECRET_PASSWORD not in row.detail for row in AuditLog.objects.all())


class TestLoginRateLimit:
    def test_fifth_post_allowed_sixth_blocked(self, db, user):
        cache.clear()
        client = Client(REMOTE_ADDR="198.51.100.20")
        for _ in range(LOGIN_RATE_LIMIT):
            response = client.post(
                "/staff/login/",
                {"username": user.email, "password": "wrong-password"},
            )
            assert response.status_code != 429
        blocked = client.post(
            "/staff/login/",
            {"username": user.email, "password": "wrong-password"},
        )
        assert blocked.status_code == 429
        assert AuditLog.objects.filter(action="login.blocked").count() == 1

    def test_successful_login_resets_counter(self, db, user):
        cache.clear()
        client = Client(REMOTE_ADDR="198.51.100.21")
        for _ in range(LOGIN_RATE_LIMIT - 1):
            client.post(
                "/staff/login/",
                {"username": user.email, "password": "wrong-password"},
            )
        ok = client.post(
            "/staff/login/",
            {"username": user.email, "password": "test-pass-123"},
        )
        assert ok.status_code == 302
        fresh = Client(REMOTE_ADDR="198.51.100.21")
        for _ in range(LOGIN_RATE_LIMIT):
            response = fresh.post(
                "/staff/login/",
                {"username": user.email, "password": "wrong-password"},
            )
            assert response.status_code != 429
        assert (
            fresh.post(
                "/staff/login/",
                {"username": user.email, "password": "wrong-password"},
            ).status_code
            == 429
        )


class TestAuditLogAdminReadOnly:
    def test_non_superuser_cannot_add(self, user):
        assert _admin().has_add_permission(_request_for(user)) is False

    def test_non_superuser_cannot_change_or_delete(self, user):
        request = _request_for(user)
        obj = AuditLog()
        assert _admin().has_change_permission(request) is False
        assert _admin().has_change_permission(request, obj) is False
        assert _admin().has_delete_permission(request) is False
        assert _admin().has_delete_permission(request, obj) is False

    def test_superuser_cannot_add_but_can_change_and_delete(self, admin_user):
        request = _request_for(admin_user)
        obj = AuditLog()
        assert _admin().has_add_permission(request) is False
        assert _admin().has_change_permission(request, obj) is True
        assert _admin().has_delete_permission(request, obj) is True

    def test_all_fields_are_readonly(self):
        assert _admin().readonly_fields == tuple(field.name for field in AuditLog._meta.fields)


class TestStaffHtmlAccess:
    def test_anonymous_redirected_to_login(self, db):
        response = Client().get("/staff/profiles/")
        assert response.status_code in (301, 302)
        assert response.url.startswith("/staff/login/")

    def test_non_staff_user_denied(self, db, user):
        client = Client()
        client.force_login(user)
        response = client.get("/staff/profiles/")
        assert response.status_code in (301, 302, 403)

    def test_superuser_reaches_staff_html(self, db, admin_client, admin_user):
        from django_otp.plugins.otp_totp.models import TOTPDevice

        # Without TOTP, MFA middleware sends staff to enrollment.
        response = admin_client.get("/staff/profiles/")
        assert response.status_code == 302
        assert "/staff/account/two-factor/" in response.url

        device = TOTPDevice.objects.create(
            user=admin_user, name="default", confirmed=True
        )
        session = admin_client.session
        session["otp_device_id"] = device.persistent_id
        session.save()
        response = admin_client.get("/staff/profiles/")
        assert response.status_code == 200


class TestNoIndexMiddleware:
    def test_admin_login_page_is_noindexed(self, db):
        response = Client().get("/staff/login/")
        assert response.status_code == 200
        assert response.headers["X-Robots-Tag"] == "noindex, nofollow"

    def test_admin_paths_do_not_get_preview_cache_control(self, db):
        response = Client().get("/staff/login/")
        assert response.headers.get("Cache-Control") != "no-store"

    def test_api_is_noindexed(self, db):
        response = Client().get("/api/landings/fa")
        assert response.status_code == 200
        assert response.headers["X-Robots-Tag"] == "noindex, nofollow"

    def test_rebuild_trigger_is_noindexed(self, db):
        response = Client().get("/rebuild-trigger/")
        assert response.status_code == 405
        assert response.headers["X-Robots-Tag"] == "noindex, nofollow"

    def test_public_health_path_is_not_noindexed(self, db):
        response = Client().get("/health/")
        assert response.status_code == 200
        assert "X-Robots-Tag" not in response.headers

    def test_preview_path_adds_noarchive_and_no_store(self, db, admin_user):
        from django_otp.plugins.otp_totp.models import TOTPDevice

        from apps.content.models import Landing, LifecycleStatus, Locale

        landing = Landing.objects.create(
            locale=Locale.EN,
            slug="preview-headers",
            title="Preview headers",
            body="body",
            status=LifecycleStatus.DRAFT,
        )
        client = Client()
        client.force_login(admin_user)
        device = TOTPDevice.objects.create(
            user=admin_user, name="default", confirmed=True
        )
        session = client.session
        session["otp_device_id"] = device.persistent_id
        session.save()
        response = client.get(f"/staff/preview/landing/{landing.pk}/")
        assert response.status_code == 200
        assert response.headers["X-Robots-Tag"] == "noindex, nofollow, noarchive"
        assert response.headers["Cache-Control"] == "no-store"

    def test_public_share_path_adds_noarchive_and_no_store(self, db, admin_user):
        from apps.content.models import Landing, LifecycleStatus, Locale
        from apps.content.preview_token import build_preview_token

        landing = Landing.objects.create(
            locale=Locale.EN,
            slug="share-headers",
            title="Share headers",
            body="body",
            status=LifecycleStatus.DRAFT,
        )
        token = build_preview_token("landing", landing.pk)
        response = Client().get(f"/preview/share/{token}/")
        assert response.status_code == 200
        assert response.headers["X-Robots-Tag"] == "noindex, nofollow, noarchive"
        assert response.headers["Cache-Control"] == "no-store"


class TestRichTextAllowlist:
    def test_features_equal_exact_settings_allowlist(self, settings):
        expected = [
            "h2",
            "h3",
            "h4",
            "bold",
            "italic",
            "ol",
            "ul",
            "link",
            "document-link",
            "hr",
            "blockquote",
            "code",
        ]
        assert settings.RICHTEXT_ALLOWED_FEATURES == expected

    def test_risky_features_excluded(self, settings):
        assert "embed" not in settings.RICHTEXT_ALLOWED_FEATURES
        assert "image" not in settings.RICHTEXT_ALLOWED_FEATURES

    def test_script_and_img_not_allowed(self, settings):
        assert "script" not in settings.RICHTEXT_ALLOWED_FEATURES
        assert "img" not in settings.RICHTEXT_ALLOWED_FEATURES

    def test_xss_payload_stripped_by_local_sanitizer(self):
        from apps.content.html_sanitize import sanitize_html

        cleaned = sanitize_html(
            "<p>hi <script>alert(1)</script>"
            '<img src="https://example.com/x.png" onerror="alert(1)">'
            '<a href="javascript:alert(1)">link</a></p>'
        )
        assert "<script" not in cleaned
        assert "onerror" not in cleaned
        assert 'href="javascript:' not in cleaned
        assert "<img" not in cleaned
        assert cleaned.startswith("<p>")
