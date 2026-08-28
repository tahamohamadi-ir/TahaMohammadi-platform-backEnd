"""Public preview share token access boundary (DEFER-0016)."""

import json

import pytest
from django.core.cache import cache
from django.test import Client
from django.urls import reverse

from apps.content.models import Article, Landing, LifecycleStatus, Locale
from apps.content.preview_token import build_preview_token
from apps.content.views_preview import sanitize_preview_body
from apps.security.models import AuditLog

PREVIEW_SECRET = "test-preview-share-secret"


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
    csrf_client.force_login(admin_user)
    session = csrf_client.session
    session["otp_device_id"] = totp_device.persistent_id
    session.save()
    token = csrf_client.get("/api/v1/admin/auth/csrf").json()["csrfToken"]
    csrf_client.defaults["HTTP_X_CSRFTOKEN"] = token
    return csrf_client


@pytest.fixture
def draft_landing(db):
    return Landing.objects.create(
        locale=Locale.EN,
        slug="share-draft",
        title="Shared Draft Home",
        body='Hello <script>alert(1)</script><p>shared ok</p>',
        status=LifecycleStatus.DRAFT,
        published_at=None,
    )


@pytest.fixture
def preview_client():
    return Client()


@pytest.fixture(autouse=True)
def _preview_secret(settings):
    settings.PREVIEW_SHARE_SECRET = PREVIEW_SECRET


@pytest.mark.django_db
class TestPublicPreviewShare:
    def test_valid_token_shows_draft(self, preview_client, draft_landing):
        token = build_preview_token("landing", draft_landing.pk)
        url = reverse("content_public_share_preview", kwargs={"token": token})
        response = preview_client.get(url)
        assert response.status_code == 200
        assert b"Shared Draft Home" in response.content
        assert b"<script>" not in response.content
        assert b"shared ok" in response.content
        assert response.headers["X-Robots-Tag"] == "noindex, nofollow, noarchive"
        assert response.headers["Cache-Control"] == "no-store"

    def test_expired_token_returns_410(self, preview_client, draft_landing):
        token = build_preview_token("landing", draft_landing.pk, ttl_seconds=-60)
        url = reverse("content_public_share_preview", kwargs={"token": token})
        response = preview_client.get(url)
        assert response.status_code == 410
        assert b"Shared Draft Home" not in response.content

    def test_tampered_token_returns_404(self, preview_client, draft_landing):
        token = build_preview_token("landing", draft_landing.pk)
        tampered = token[:-4] + "dead"
        url = reverse("content_public_share_preview", kwargs={"token": tampered})
        response = preview_client.get(url)
        assert response.status_code == 404
        assert b"Shared Draft Home" not in response.content

    def test_unknown_kind_returns_404(self, preview_client, draft_landing):
        token = build_preview_token("missing", draft_landing.pk)
        url = reverse("content_public_share_preview", kwargs={"token": token})
        response = preview_client.get(url)
        assert response.status_code == 404

    def test_missing_object_returns_404(self, preview_client, draft_landing):
        token = build_preview_token("landing", 999999)
        url = reverse("content_public_share_preview", kwargs={"token": token})
        response = preview_client.get(url)
        assert response.status_code == 404

    def test_profile_and_article_kinds(self, preview_client, db):
        from apps.content.models import Profile

        profile = Profile.objects.create(
            locale=Locale.FA,
            slug="share-profile",
            title="Profile Draft",
            body="<p>profile body</p>",
            status=LifecycleStatus.DRAFT,
        )
        article = Article.objects.create(
            locale=Locale.EN,
            slug="share-article",
            title="Article Draft",
            body="<p>article body</p>",
            status=LifecycleStatus.DRAFT,
        )
        for kind, obj, needle in (
            ("profile", profile, b"Profile Draft"),
            ("article", article, b"Article Draft"),
        ):
            token = build_preview_token(kind, obj.pk)
            url = reverse("content_public_share_preview", kwargs={"token": token})
            response = preview_client.get(url)
            assert response.status_code == 200
            assert needle in response.content

    def test_sanitize_strips_script(self):
        cleaned = sanitize_preview_body(
            '<p>hi</p><script>alert(1)</script><img src=x onerror=alert(1)>'
        )
        assert "<script" not in cleaned
        assert "onerror" not in cleaned


@pytest.mark.django_db
class TestPreviewLinkAdminApi:
    def test_anonymous_cannot_create_preview_link(self, draft_landing):
        response = Client().post(
            f"/api/v1/admin/content/landing/{draft_landing.pk}/preview-link"
        )
        assert response.status_code in (401, 403)

    def test_staff_otp_creates_preview_link(self, admin_api_client, draft_landing):
        response = admin_api_client.post(
            f"/api/v1/admin/content/landing/{draft_landing.pk}/preview-link",
            data=json.dumps({}),
            content_type="application/json",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["path"].startswith("/preview/share/")
        assert data["ttlSeconds"] == 900
        assert "expiresAt" in data
        audit = AuditLog.objects.filter(action="preview.share_link").first()
        assert audit is not None
        assert audit.object_id == str(draft_landing.pk)

    def test_unsupported_entity_returns_404(self, admin_api_client, db):
        from apps.content.models import Project

        project = Project.objects.create(
            locale=Locale.EN,
            slug="no-preview",
            title="Project",
            status=LifecycleStatus.DRAFT,
        )
        response = admin_api_client.post(
            f"/api/v1/admin/content/project/{project.pk}/preview-link",
            data=json.dumps({}),
            content_type="application/json",
        )
        assert response.status_code == 404
