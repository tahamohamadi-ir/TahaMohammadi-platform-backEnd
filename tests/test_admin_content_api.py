"""Custom admin content read API tests (ADR-0026, ADM-1).

Covers GET /api/v1/admin/content/{entity} list (auth, OTP guard, filters,
pagination, validation) and GET /api/v1/admin/content/{entity}/{id} detail
(entity-specific fields, 404).
"""

import pytest
from django.core.cache import cache
from django.test import Client
from django.utils import timezone

from apps.content.models import (
    Article,
    LifecycleStatus,
    Locale,
    Project,
)


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

