"""Authoritative contract fixtures (BACKEND-130).

Fixtures under ``tests/fixtures/contracts/`` are observed API responses, never
invented fields. Each test rebuilds deterministic content, calls the real
endpoint, and byte-compares the (volatile-field-normalized) response against the
committed fixture. Public/admin DTO fixtures are additionally validated against
the accepted OpenAPI components.

Regenerate fixtures only after an accepted schema change:

    CONTRACT_FIXTURES_WRITE=1 uv run pytest tests/test_contract_fixtures.py
"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from django.core.cache import cache
from django.test import Client

from apps.content.models import (
    Article,
    Landing,
    LifecycleStatus,
    Locale,
    Profile,
    Project,
    Publication,
    Series,
    TopicTag,
)

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "contracts"
PUBLIC_DIR = FIXTURES_DIR / "public"
ADMIN_DIR = FIXTURES_DIR / "admin"
ERRORS_DIR = FIXTURES_DIR / "errors"

PUBLIC_OPENAPI = (
    Path(__file__).resolve().parents[1]
    / "docs/contracts/openapi/current/public-openapi.json"
)
ADMIN_OPENAPI = (
    Path(__file__).resolve().parents[1]
    / "docs/contracts/openapi/current/admin-openapi.json"
)

FIXED_INSTANT = datetime(2026, 1, 10, 12, 0, 0, tzinfo=UTC)
FIXED_INSTANT_JSON = "2026-01-10T12:00:00Z"
VOLATILE_KEYS = {"published_at", "updated_at", "publishedAt", "updatedAt"}
_ISO_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:?\d{2})?$"
)

PROFILE_DETAIL_FIELDS = {
    "locale",
    "slug",
    "title",
    "seoTitle",
    "seoDescription",
    "shortBio",
    "longBio",
    "availability",
    "publishedAt",
    "availableLocales",
    "skills",
    "experience",
    "education",
    "publications",
    "researchProjects",
    "certificates",
    "socials",
}


def _write_mode() -> bool:
    return os.environ.get("CONTRACT_FIXTURES_WRITE") == "1"


def _normalize(value, key=None):
    if isinstance(value, dict):
        return {k: _normalize(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize(v, key) for v in value]
    if (
        key in VOLATILE_KEYS
        and isinstance(value, str)
        and _ISO_RE.match(value)
    ):
        return FIXED_INSTANT_JSON
    return value


def _fixture_path(directory: Path, name: str) -> Path:
    return directory / name


def _compare_json(directory: Path, name: str, payload) -> None:
    path = _fixture_path(directory, name)
    normalized = _normalize(payload)
    if _write_mode():
        directory.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(normalized, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return
    expected = json.loads(path.read_text(encoding="utf-8"))
    assert normalized == expected, f"Contract drift against fixture: {path}"


def _compare_text(directory: Path, name: str, text: str) -> None:
    path = _fixture_path(directory, name)
    if _write_mode():
        directory.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return
    expected = path.read_text(encoding="utf-8")
    assert text == expected, f"Contract drift against fixture: {path}"


def _resolve_component(artifact: dict, name: str) -> dict:
    schema = artifact["components"]["schemas"][name]
    properties: dict = {}
    required: list = []
    parts = schema.get("allOf") or [schema]
    for part in parts:
        if "$ref" in part:
            ref = part["$ref"].split("/")[-1]
            part = artifact["components"]["schemas"][ref]
        properties.update(part.get("properties", {}))
        required.extend(part.get("required", []))
    return {"properties": set(properties), "required": set(required)}


def _validate_component(
    artifact: dict, name: str, payload: dict, *, item_component: str | None = None
) -> None:
    component = _resolve_component(artifact, name)
    assert set(payload) <= component["properties"], (
        f"{name}: unexpected fields {set(payload) - component['properties']}"
    )
    missing = component["required"] - set(payload)
    assert not missing, f"{name}: missing required fields {missing}"
    if item_component is not None:
        item = _resolve_component(artifact, item_component)
        for entry in payload.get("items", []):
            assert set(entry) <= item["properties"]
            assert item["required"] <= set(entry)


@pytest.fixture
def contract_content(db):
    Landing.objects.create(
        locale=Locale.EN,
        slug="home",
        title="Home",
        body="Home body copy",
        seo_title="Home",
        seo_description="Home description",
        status=LifecycleStatus.PUBLISHED,
        published_at=FIXED_INSTANT,
    )
    tag = TopicTag.objects.create(locale=Locale.EN, slug="systems", name="Systems")
    series = Series.objects.create(
        locale=Locale.EN,
        slug="foundations",
        title="Foundations",
        description="Series intro",
        ordering=1,
        status=LifecycleStatus.PUBLISHED,
        published_at=FIXED_INSTANT,
    )
    article = Article.objects.create(
        locale=Locale.EN,
        slug="first-post",
        title="First post",
        body="<p>Hello published</p>",
        excerpt="Hello",
        status=LifecycleStatus.PUBLISHED,
        published_at=FIXED_INSTANT,
    )
    article.topic_tags.add(tag)
    article.series.add(series)
    Profile.objects.create(
        locale=Locale.EN,
        slug="about",
        title="About",
        body="Profile body",
        short_bio="Short bio",
        long_bio="Long bio",
        status=LifecycleStatus.PUBLISHED,
        published_at=FIXED_INSTANT,
    )
    Publication.objects.create(
        locale=Locale.EN,
        slug="sample-paper",
        title="Sample paper",
        status=LifecycleStatus.PUBLISHED,
        published_at=FIXED_INSTANT,
    )
    Project.objects.create(
        locale=Locale.EN,
        slug="sample-project",
        title="Sample project",
        objective="Objective copy",
        status=LifecycleStatus.PUBLISHED,
        published_at=FIXED_INSTANT,
    )


# --- Public DTO fixtures -----------------------------------------------------


@pytest.mark.django_db
def test_fixture_landing_get(contract_content):
    response = Client().get("/api/landings/en/home")
    assert response.status_code == 200
    _compare_json(PUBLIC_DIR, "landing.get.200.json", response.json())
    if not _write_mode():
        _validate_component(
            json.loads(PUBLIC_OPENAPI.read_text(encoding="utf-8")),
            "LandingOut",
            response.json(),
        )


@pytest.mark.django_db
def test_fixture_articles_list(contract_content):
    response = Client().get("/api/articles/en")
    assert response.status_code == 200
    _compare_json(PUBLIC_DIR, "articles.get.200.json", response.json())
    if not _write_mode():
        _validate_component(
            json.loads(PUBLIC_OPENAPI.read_text(encoding="utf-8")),
            "PagedArticleListOut",
            response.json(),
            item_component="ArticleListOut",
        )


@pytest.mark.django_db
def test_fixture_articles_detail(contract_content):
    response = Client().get("/api/articles/en/first-post")
    assert response.status_code == 200
    _compare_json(PUBLIC_DIR, "articles-detail.get.200.json", response.json())
    if not _write_mode():
        _validate_component(
            json.loads(PUBLIC_OPENAPI.read_text(encoding="utf-8")),
            "ArticleDetailOut",
            response.json(),
        )


@pytest.mark.django_db
def test_fixture_profile_detail_about(contract_content):
    # Observed runtime shape (config/urls.py profile view); Gap A of
    # docs/contracts/PUBLIC-ROUTE-RECONCILIATION.md: no accepted component yet.
    response = Client().get("/api/profiles/en/about")
    assert response.status_code == 200
    payload = response.json()
    _compare_json(PUBLIC_DIR, "profile-detail.get.200.json", payload)
    if not _write_mode():
        assert set(payload) == PROFILE_DETAIL_FIELDS


@pytest.mark.django_db
def test_fixture_publication_detail(contract_content):
    response = Client().get("/api/publications/en/sample-paper")
    assert response.status_code == 200
    _compare_json(PUBLIC_DIR, "publication-detail.get.200.json", response.json())
    if not _write_mode():
        _validate_component(
            json.loads(PUBLIC_OPENAPI.read_text(encoding="utf-8")),
            "PublicationDetailOut",
            response.json(),
        )


@pytest.mark.django_db
def test_fixture_project_detail(contract_content):
    response = Client().get("/api/projects/en/sample-project")
    assert response.status_code == 200
    _compare_json(PUBLIC_DIR, "project-detail.get.200.json", response.json())
    if not _write_mode():
        _validate_component(
            json.loads(PUBLIC_OPENAPI.read_text(encoding="utf-8")),
            "ProjectDetailOut",
            response.json(),
        )


@pytest.mark.django_db
def test_fixture_site_settings(contract_content):
    response = Client().get("/api/site")
    assert response.status_code == 200
    _compare_json(PUBLIC_DIR, "site.get.200.json", response.json())
    if not _write_mode():
        _validate_component(
            json.loads(PUBLIC_OPENAPI.read_text(encoding="utf-8")),
            "PublicSiteSettingsOut",
            response.json(),
        )


# --- Admin DTO fixture --------------------------------------------------------


@pytest.fixture
def admin_otp_client(db, admin_user):
    from django.middleware.csrf import _get_new_csrf_string
    from django_otp.plugins.otp_totp.models import TOTPDevice

    client = Client()
    client.force_login(admin_user)
    device = TOTPDevice.objects.create(user=admin_user, name="default", confirmed=True)
    session = client.session
    session["otp_device_id"] = device.persistent_id
    session.save()
    token = _get_new_csrf_string()
    client.cookies["csrftoken"] = token
    client.defaults["HTTP_X_CSRFTOKEN"] = token
    return client


@pytest.mark.django_db
def test_fixture_admin_auth_me(admin_otp_client):
    response = admin_otp_client.get("/api/v1/admin/auth/me")
    assert response.status_code == 200
    payload = response.json()
    _compare_json(ADMIN_DIR, "auth-me.get.200.json", payload)
    if not _write_mode():
        _validate_component(
            json.loads(ADMIN_OPENAPI.read_text(encoding="utf-8")),
            "AdminUserOut",
            payload,
        )


# --- Error fixtures (ERROR-COMPATIBILITY-MATRIX.md rows) ----------------------


@pytest.fixture
def clean_contact_cache(db):
    cache.clear()
    yield
    cache.clear()


@pytest.mark.django_db
def test_fixture_profile_not_found_404(contract_content):
    response = Client().get("/api/profiles/en/does-not-exist")
    assert response.status_code == 404
    _compare_json(ERRORS_DIR, "profile-not-found.404.json", response.json())


@pytest.mark.django_db
def test_fixture_contact_json_error(clean_contact_cache):
    response = Client().post(
        "/api/contact",
        json.dumps({"email": "not-an-email", "message": "hi"}),
        content_type="application/json",
    )
    assert response.status_code == 400
    _compare_json(ERRORS_DIR, "contact.post.error.json", response.json())


@pytest.mark.django_db
def test_fixture_contact_html_validation(clean_contact_cache):
    response = Client().post("/api/contact", {"email": "", "message": ""})
    assert response.status_code == 422
    assert response["content-type"].startswith("text/html")
    _compare_text(ERRORS_DIR, "contact.post.validation.html", response.text)


@pytest.mark.django_db
def test_fixture_framework_validation_unhandled(contract_content):
    response = Client().get("/api/articles/en?page=not-a-number")
    assert response.status_code == 422
    _compare_json(ERRORS_DIR, "framework-validation.unhandled.json", response.json())
