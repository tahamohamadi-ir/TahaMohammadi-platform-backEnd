"""Custom admin content write API tests (ADR-0026, ADM-1).

Covers GET /api/v1/admin/content/schema (writable-field metadata),
POST /api/v1/admin/content/{entity} (create) and
PUT /api/v1/admin/content/{entity}/{id} (optimistically-locked update):
validation, duplicates, coercion, publish timestamp and the OTP guard.
"""

import json

import pytest
from django.core.cache import cache
from django.test import Client

from apps.content.models import Article, Landing


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


def _post_json(client, path, payload):
    return client.post(
        path,
        data=json.dumps(payload),
        content_type="application/json",
    )


def _create_article(client, *, slug, title, fields=None, locale="en"):
    return _post_json(
        client,
        "/api/v1/admin/content/article",
        {"locale": locale, "slug": slug, "title": title, "fields": fields or {}},
    )


def _create_landing(client, *, slug, title, fields=None, locale="en"):
    return _post_json(
        client,
        "/api/v1/admin/content/landing",
        {"locale": locale, "slug": slug, "title": title, "fields": fields or {}},
    )


def test_create_article_201(admin_api_client):
    body = " ".join(["word"] * 401)  # en @ 230 wpm → ceil(401/230) = 2-minute read
    response = _create_article(
        admin_api_client,
        slug="hello",
        title="Hello world",
        fields={"excerpt": "A short excerpt.", "body": body, "readingTimeMinutes": "2"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["status"] == "draft"
    assert data["locale"] == "en"
    assert data["slug"] == "hello"
    assert data["fields"]["excerpt"] == "A short excerpt."
    assert data["fields"]["readingTimeMinutes"] == 2

    article = Article.objects.get(locale="en", slug="hello")
    assert article.status == "draft"
    assert article.reading_time_minutes == 2


def test_create_duplicate_409(admin_api_client):
    first = _create_article(admin_api_client, slug="dup", title="First")
    assert first.status_code == 201
    second = _create_article(admin_api_client, slug="dup", title="Second")
    assert second.status_code == 409
    assert second.json()["code"] == "DUPLICATE"


def test_create_invalid_locale_400(admin_api_client):
    response = _create_article(admin_api_client, slug="bad", title="Bad", locale="xx")
    assert response.status_code == 400
    assert response.json()["code"] == "VALIDATION"


def test_create_unknown_field_400(admin_api_client):
    response = _create_article(
        admin_api_client,
        slug="bogus",
        title="Bogus",
        fields={"excerpt": "Ok", "bogus": "x"},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "VALIDATION"
    assert body["fields"]["fields"] == ["bogus"]


def test_update_fields_and_title_200(admin_api_client):
    created = _create_landing(admin_api_client, slug="home", title="Home")
    assert created.status_code == 201
    landing_id = created.json()["id"]
    updated_at = created.json()["updatedAt"]

    response = admin_api_client.put(
        f"/api/v1/admin/content/landing/{landing_id}",
        data=json.dumps({"title": "Updated Home", "fields": {"seoTitle": "New SEO"}}),
        content_type="application/json",
        HTTP_IF_MATCH=f'"{updated_at}"',
    )
    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "Updated Home"
    assert data["fields"]["seoTitle"] == "New SEO"

    landing = Landing.objects.get(pk=landing_id)
    assert landing.title == "Updated Home"
    assert landing.seo_title == "New SEO"


def test_update_conflict_409(admin_api_client):
    created = _create_landing(admin_api_client, slug="conflict", title="Conflict")
    assert created.status_code == 201
    landing_id = created.json()["id"]
    current = created.json()["updatedAt"]

    response = admin_api_client.put(
        f"/api/v1/admin/content/landing/{landing_id}",
        data=json.dumps({"title": "Stale update"}),
        content_type="application/json",
        HTTP_IF_MATCH='"2000-01-01T00:00:00+00:00"',
    )
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "CONFLICT"
    assert body["currentUpdatedAt"] == current


def test_update_slug_duplicate_409(admin_api_client):
    first = _create_landing(admin_api_client, slug="alpha", title="Alpha")
    assert first.status_code == 201
    second = _create_landing(admin_api_client, slug="beta", title="Beta")
    assert second.status_code == 201
    second_id = second.json()["id"]
    second_updated_at = second.json()["updatedAt"]

    response = admin_api_client.put(
        f"/api/v1/admin/content/landing/{second_id}",
        data=json.dumps({"slug": "alpha"}),
        content_type="application/json",
        HTTP_IF_MATCH=f'"{second_updated_at}"',
    )
    assert response.status_code == 409
    assert response.json()["code"] == "DUPLICATE"


def test_publish_sets_published_at(admin_api_client):
    created = _create_landing(admin_api_client, slug="publish", title="Publish")
    assert created.status_code == 201
    landing_id = created.json()["id"]
    updated_at = created.json()["updatedAt"]

    response = admin_api_client.put(
        f"/api/v1/admin/content/landing/{landing_id}",
        data=json.dumps({"status": "published"}),
        content_type="application/json",
        HTTP_IF_MATCH=f'"{updated_at}"',
    )
    assert response.status_code == 200
    assert response.json()["status"] == "published"
    assert response.json()["publishedAt"] is not None

    landing = Landing.objects.get(pk=landing_id)
    assert landing.status == "published"
    assert landing.published_at is not None


def test_schema_endpoint(admin_api_client):
    response = admin_api_client.get("/api/v1/admin/content/schema")
    assert response.status_code == 200
    entities = response.json()["entities"]
    assert set(entities) == {
        "landing",
        "profile",
        "article",
        "series",
        "research-topic",
        "research-statement",
        "project",
        "publication",
        "book",
        "talk",
        "download",
        "course",
        "creative-work",
    }

    article_specs = {spec["key"]: spec for spec in entities["article"]["fields"]}
    assert article_specs["readingTimeMinutes"]["type"] == "number"
    assert article_specs["body"]["type"] == "textarea"

    profile_keys = [spec["key"] for spec in entities["profile"]["fields"]]
    assert "revision" not in profile_keys
    assert "seoTitle" in profile_keys

    project_specs = {spec["key"]: spec for spec in entities["project"]["fields"]}
    assert project_specs["showOnProjects"]["type"] == "boolean"


def test_write_requires_otp(csrf_client, admin_user):
    csrf_client.force_login(admin_user)  # staff but NO otp session
    post_response = csrf_client.post(
        "/api/v1/admin/content/article",
        data=json.dumps({"locale": "en", "slug": "nope", "title": "Nope"}),
        content_type="application/json",
    )
    assert post_response.status_code == 403
    assert post_response.json()["code"] == "OTP_REQUIRED"

    put_response = csrf_client.put(
        "/api/v1/admin/content/article/1",
        data=json.dumps({"title": "Nope"}),
        content_type="application/json",
        HTTP_IF_MATCH='"2000-01-01T00:00:00+00:00"',
    )
    assert put_response.status_code == 403
    assert put_response.json()["code"] == "OTP_REQUIRED"

def test_publish_keeps_published_at_on_resave(admin_api_client):
    created = _create_landing(admin_api_client, slug="resave", title="Resave")
    assert created.status_code == 201
    landing_id = created.json()["id"]
    updated_at = created.json()["updatedAt"]

    first = admin_api_client.put(
        f"/api/v1/admin/content/landing/{landing_id}",
        data=json.dumps({"status": "published"}),
        content_type="application/json",
        HTTP_IF_MATCH=f'"{updated_at}"',
    )
    assert first.status_code == 200
    published_at_first = first.json()["publishedAt"]
    assert published_at_first is not None
    updated_at2 = first.json()["updatedAt"]

    second = admin_api_client.put(
        f"/api/v1/admin/content/landing/{landing_id}",
        data=json.dumps({"status": "published", "title": "Resave title"}),
        content_type="application/json",
        HTTP_IF_MATCH=f'"{updated_at2}"',
    )
    assert second.status_code == 200
    assert second.json()["publishedAt"] == published_at_first


def test_update_blank_numeric_field_ok(admin_api_client):
    body = " ".join(["word"] * 401)
    created = _create_article(
        admin_api_client,
        slug="blanknum",
        title="Blank num",
        fields={"excerpt": "x", "body": body, "readingTimeMinutes": "2"},
    )
    assert created.status_code == 201
    article_id = created.json()["id"]
    updated_at = created.json()["updatedAt"]

    response = admin_api_client.put(
        f"/api/v1/admin/content/article/{article_id}",
        data=json.dumps({"fields": {"readingTimeMinutes": ""}}),
        content_type="application/json",
        HTTP_IF_MATCH=f'"{updated_at}"',
    )
    assert response.status_code == 200
    assert response.json()["fields"]["readingTimeMinutes"] == 2


def test_p8_book_and_download_admin_crud_smoke(admin_api_client, db):
    from django.core.files.uploadedfile import SimpleUploadedFile

    from apps.content.models import Book, Download
    from apps.media.models import Media

    book = _post_json(
        admin_api_client,
        "/api/v1/admin/content/book",
        {
            "locale": "en",
            "slug": "admin-book",
            "title": "Admin Book",
            "fields": {"authors": "Taha", "isbn": "9780000000000"},
        },
    )
    assert book.status_code == 201
    book_id = book.json()["id"]
    assert Book.objects.filter(pk=book_id, slug="admin-book").exists()

    media = Media.objects.create(
        file=SimpleUploadedFile("admin.pdf", b"%PDF-1.4", content_type="application/pdf"),
        title="admin.pdf",
        is_active=True,
    )
    download = _post_json(
        admin_api_client,
        "/api/v1/admin/content/download",
        {
            "locale": "en",
            "slug": "admin-download",
            "title": "Admin Download",
            "fields": {"mediaId": media.pk, "downloadType": "pdf", "language": "en"},
        },
    )
    assert download.status_code == 201, download.content
    download_id = download.json()["id"]
    assert Download.objects.filter(pk=download_id, media_id=media.pk).exists()

    series = _post_json(
        admin_api_client,
        "/api/v1/admin/content/series",
        {
            "locale": "en",
            "slug": "admin-series",
            "title": "Admin Series",
            "fields": {"description": "Series desc", "ordering": 1},
        },
    )
    assert series.status_code == 201

