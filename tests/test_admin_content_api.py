"""Custom admin content read API tests (ADR-0026, ADM-1).

Covers GET /api/v1/admin/content/{entity} list (auth, OTP guard, filters,
pagination, validation) and GET /api/v1/admin/content/{entity}/{id} detail
(entity-specific fields, 404).
"""

import json

import pytest
from django.core.cache import cache
from django.test import Client
from django.utils import timezone

from apps.content.models import (
    Article,
    LifecycleStatus,
    Locale,
    Profile,
    ProfileSkill,
    Project,
)
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


def _make_article(*, locale, slug, title, status, body=""):
    return Article.objects.create(
        locale=locale,
        slug=slug,
        title=title,
        body=body,
        status=status,
        published_at=timezone.now() if status == LifecycleStatus.PUBLISHED else None,
    )


def test_list_requires_auth(csrf_client):
    response = csrf_client.get("/api/v1/admin/content/article")
    assert response.status_code == 401
    assert response.json()["code"] == "AUTH_REQUIRED"


def test_list_requires_otp(csrf_client, admin_user):
    csrf_client.force_login(admin_user)  # staff but NO otp session
    response = csrf_client.get("/api/v1/admin/content/article")
    assert response.status_code == 403
    assert response.json()["code"] == "OTP_REQUIRED"


def test_list_article_filter_and_pagination(admin_api_client):
    _make_article(
        locale=Locale.EN,
        slug="alpha",
        title="Alpha post",
        status=LifecycleStatus.PUBLISHED,
    )
    _make_article(
        locale=Locale.EN,
        slug="beta",
        title="Beta post",
        status=LifecycleStatus.PUBLISHED,
    )
    _make_article(
        locale=Locale.FA,
        slug="gamma",
        title="Gamma draft",
        status=LifecycleStatus.DRAFT,
    )

    all_response = admin_api_client.get("/api/v1/admin/content/article")
    assert all_response.status_code == 200
    body = all_response.json()
    assert body["total"] == 3
    assert body["page"] == 1
    assert body["pageSize"] == 20
    assert len(body["items"]) == 3

    en_response = admin_api_client.get(
        "/api/v1/admin/content/article", {"locale": "en"}
    )
    assert en_response.json()["total"] == 2

    published_response = admin_api_client.get(
        "/api/v1/admin/content/article", {"status": "published"}
    )
    assert published_response.json()["total"] == 2

    search_response = admin_api_client.get(
        "/api/v1/admin/content/article", {"q": "Beta"}
    )
    search_body = search_response.json()
    assert search_body["total"] == 1
    assert search_body["items"][0]["slug"] == "beta"

    page_one = admin_api_client.get(
        "/api/v1/admin/content/article", {"status": "published", "page": 1, "pageSize": 1}
    )
    page_one_body = page_one.json()
    assert page_one_body["total"] == 2
    assert len(page_one_body["items"]) == 1

    page_two = admin_api_client.get(
        "/api/v1/admin/content/article", {"status": "published", "page": 2, "pageSize": 1}
    )
    page_two_body = page_two.json()
    assert page_two_body["total"] == 2
    assert len(page_two_body["items"]) == 1
    assert page_two_body["items"][0]["id"] != page_one_body["items"][0]["id"]


def test_list_unknown_entity_404(admin_api_client):
    response = admin_api_client.get("/api/v1/admin/content/nope")
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


def test_list_invalid_locale_400(admin_api_client):
    response = admin_api_client.get("/api/v1/admin/content/article", {"locale": "xx"})
    assert response.status_code == 400
    assert response.json()["code"] == "VALIDATION"


def test_detail_fields(admin_api_client):
    project = Project.objects.create(
        locale=Locale.EN,
        slug="case-study",
        title="Case study",
        status=LifecycleStatus.PUBLISHED,
        published_at=timezone.now(),
        project_type="ai",
        objective="Ship a retrieval system.",
        code_url="https://github.com/example/repo",
    )
    article = Article.objects.create(
        locale=Locale.FA,
        slug="post",
        title="Post",
        status=LifecycleStatus.DRAFT,
        excerpt="A short excerpt.",
        body="<p>Some rich text body</p>",
    )
    Article.objects.filter(pk=article.pk).update(reading_time_minutes=3)

    project_response = admin_api_client.get(f"/api/v1/admin/content/project/{project.pk}")
    assert project_response.status_code == 200
    project_body = project_response.json()
    assert project_body["id"] == project.pk
    assert project_body["status"] == "published"
    assert project_body["fields"]["projectType"] == "ai"
    assert project_body["fields"]["objective"] == "Ship a retrieval system."
    assert project_body["fields"]["codeUrl"] == "https://github.com/example/repo"

    article_response = admin_api_client.get(f"/api/v1/admin/content/article/{article.pk}")
    assert article_response.status_code == 200
    article_body = article_response.json()
    assert article_body["fields"]["excerpt"] == "A short excerpt."
    assert article_body["fields"]["body"] == "<p>Some rich text body</p>"
    assert article_body["fields"]["readingTimeMinutes"] == 3


def test_detail_not_found_404(admin_api_client):
    response = admin_api_client.get("/api/v1/admin/content/article/999999")
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"

def test_list_invalid_pagination_400(admin_api_client):
    bad_page = admin_api_client.get("/api/v1/admin/content/article?page=abc")
    assert bad_page.status_code == 400
    assert bad_page.json()["code"] == "VALIDATION"
    bad_size = admin_api_client.get("/api/v1/admin/content/article?pageSize=0")
    assert bad_size.status_code == 400
    assert bad_size.json()["code"] == "VALIDATION"
    too_large = admin_api_client.get("/api/v1/admin/content/article?pageSize=101")
    assert too_large.status_code == 400
    assert too_large.json()["code"] == "VALIDATION"


def test_admin_spa_traversal_blocked(rf):
    from django.http import Http404

    from apps.api.admin_spa import serve_admin_ui

    request = rf.get("/admin/")
    for evil in ("../../config/settings/base.py", "index.html/../../../secrets.txt"):
        with pytest.raises(Http404):
            serve_admin_ui(request, spa_path=evil)


# --- G-G: profile sibling-locale creation on the accepted admin API -----------


def _make_profile(*, locale, slug, title, status=LifecycleStatus.DRAFT):
    return Profile.objects.create(
        locale=locale,
        slug=slug,
        title=title,
        status=status,
    )


def test_sibling_locale_requires_auth(csrf_client, db):
    profile = _make_profile(locale=Locale.EN, slug="about", title="About")
    response = csrf_client.post(
        f"/api/v1/admin/content/profile/{profile.pk}/sibling-locale",
        data=json.dumps({"targetLocale": "fa"}),
        content_type="application/json",
    )
    assert response.status_code == 401
    assert response.json()["code"] == "AUTH_REQUIRED"
    assert not Profile.objects.filter(locale=Locale.FA, slug="about").exists()


def test_sibling_locale_requires_otp(csrf_client, admin_user, db):
    csrf_client.force_login(admin_user)  # staff but NO otp session
    profile = _make_profile(locale=Locale.EN, slug="about", title="About")
    response = csrf_client.post(
        f"/api/v1/admin/content/profile/{profile.pk}/sibling-locale",
        data=json.dumps({"targetLocale": "fa"}),
        content_type="application/json",
    )
    assert response.status_code == 403
    assert response.json()["code"] == "OTP_REQUIRED"
    assert not Profile.objects.filter(locale=Locale.FA, slug="about").exists()


def test_sibling_locale_requires_csrf(admin_user, totp_device, db):
    client = Client(enforce_csrf_checks=True)
    client.force_login(admin_user)
    session = client.session
    session["otp_device_id"] = totp_device.persistent_id
    session.save()
    profile = _make_profile(locale=Locale.EN, slug="about", title="About")
    response = client.post(
        f"/api/v1/admin/content/profile/{profile.pk}/sibling-locale",
        data=json.dumps({"targetLocale": "fa"}),
        content_type="application/json",
    )  # no X-CSRFToken header
    assert response.status_code == 403
    assert response.json()["code"] == "CSRF_FAILED"
    assert not Profile.objects.filter(locale=Locale.FA, slug="about").exists()


def test_sibling_locale_unknown_profile_404(admin_api_client):
    response = admin_api_client.post(
        "/api/v1/admin/content/profile/999999/sibling-locale",
        data=json.dumps({"targetLocale": "fa"}),
        content_type="application/json",
    )
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


def test_sibling_locale_invalid_target_locale_400(admin_api_client):
    profile = _make_profile(locale=Locale.EN, slug="about", title="About")
    response = admin_api_client.post(
        f"/api/v1/admin/content/profile/{profile.pk}/sibling-locale",
        data=json.dumps({"targetLocale": "xx"}),
        content_type="application/json",
    )
    assert response.status_code == 400
    assert response.json()["code"] == "VALIDATION"


def test_sibling_locale_same_locale_400(admin_api_client):
    profile = _make_profile(locale=Locale.EN, slug="about", title="About")
    response = admin_api_client.post(
        f"/api/v1/admin/content/profile/{profile.pk}/sibling-locale",
        data=json.dumps({"targetLocale": "en"}),
        content_type="application/json",
    )
    assert response.status_code == 400
    assert response.json()["code"] == "VALIDATION"


def test_sibling_locale_creates_draft_sibling_201(
    admin_api_client, admin_user, db
):
    source = _make_profile(locale=Locale.EN, slug="about", title="About")
    Profile.objects.filter(pk=source.pk).update(
        short_bio="Bio", availability="Open", updated_at=source.updated_at
    )
    ProfileSkill.objects.create(
        profile=source, category="Design", name="Figma", source="Work"
    )
    source.refresh_from_db()

    response = admin_api_client.post(
        f"/api/v1/admin/content/profile/{source.pk}/sibling-locale",
        data=json.dumps({"targetLocale": "fa"}),
        content_type="application/json",
        REMOTE_ADDR="203.0.113.12",
    )
    assert response.status_code == 201
    body = response.json()

    created = Profile.objects.get(locale=Locale.FA, slug="about")
    assert body["editorUrl"] == f"/admin/content/profile/{created.pk}"
    assert body["profile"]["locale"] == "fa"
    assert body["profile"]["slug"] == "about"
    assert body["profile"]["title"] == ""
    assert body["profile"]["status"] == "draft"
    assert body["profile"]["revision"] == 1
    assert body["profile"]["translationStatus"]["status"] == "COMPLETE"

    created.refresh_from_db()
    assert created.translation_key == source.translation_key
    assert created.status == LifecycleStatus.DRAFT
    assert created.title == ""
    assert created.body == ""
    assert created.seo_title == ""
    assert created.seo_description == ""
    assert created.short_bio == ""
    assert created.long_bio == ""
    assert created.availability == ""
    assert created.published_at is None
    # Legacy behavior: the sibling inherits the source row's updated_at.
    assert created.updated_at == source.updated_at

    row = AuditLog.objects.get(action="admin.profile.sibling_created")
    assert row.user == admin_user
    assert row.model_name == "profile"
    assert row.object_id == str(created.pk)
    assert row.ip == "203.0.113.12"


def test_sibling_locale_existing_sibling_409(admin_api_client, db):
    source = _make_profile(locale=Locale.EN, slug="about", title="About")
    Profile.objects.create(
        locale=Locale.FA,
        slug="about",
        title="درباره",
        status=LifecycleStatus.DRAFT,
        translation_key=source.translation_key,
    )
    response = admin_api_client.post(
        f"/api/v1/admin/content/profile/{source.pk}/sibling-locale",
        data=json.dumps({"targetLocale": "fa"}),
        content_type="application/json",
    )
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "DUPLICATE"
    assert body["field_errors"] == {}
    assert Profile.objects.filter(locale=Locale.FA, slug="about").count() == 1


def test_sibling_locale_slug_conflict_409(admin_api_client, db):
    source = _make_profile(locale=Locale.EN, slug="about", title="About")
    Profile.objects.create(
        locale=Locale.FA,
        slug="about",
        title="Unrelated fa profile",
        status=LifecycleStatus.DRAFT,
    )
    response = admin_api_client.post(
        f"/api/v1/admin/content/profile/{source.pk}/sibling-locale",
        data=json.dumps({"targetLocale": "fa"}),
        content_type="application/json",
    )
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "DUPLICATE"
    assert body["field_errors"] == {}
    assert Profile.objects.filter(locale=Locale.FA, slug="about").count() == 1
    assert not AuditLog.objects.filter(action="admin.profile.sibling_created").exists()

