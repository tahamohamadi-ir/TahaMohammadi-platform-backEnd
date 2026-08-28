"""Custom admin workflow + overview API tests (ADR-0026, ADM-4).

Covers POST /api/v1/admin/content/{entity}/{id}/transition (lifecycle
transitions, validation, guards, audit log) and the read-only
/api/v1/admin/overview/* endpoints (translation queue + content health).
"""

import json
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.utils import timezone

from apps.content.models import (
    Article,
    Landing,
    LifecycleStatus,
)
from apps.media.models import Media
from apps.security.models import AuditLog

PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
    b"\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xcf\xc0\x00\x00\x00\x03"
    b"\x00\x01\xff\xff\xff\xff\xff\xff\xff\xff\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def media_root(tmp_path, settings):
    settings.MEDIA_ROOT = str(tmp_path / "media")


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


def _make_landing(*, locale, slug, title, status="draft", body=""):
    return Landing.objects.create(
        locale=locale,
        slug=slug,
        title=title,
        status=status,
        body=body,
        published_at=timezone.now() if status == LifecycleStatus.PUBLISHED else None,
    )


# --- lifecycle transitions -------------------------------------------------


def test_transition_draft_to_review_then_published(admin_api_client):
    landing = _make_landing(locale="en", slug="flow", title="Flow")

    review = _post_json(
        admin_api_client,
        f"/api/v1/admin/content/landing/{landing.pk}/transition",
        {"to": "review"},
    )
    assert review.status_code == 200
    assert review.json()["status"] == "review"
    assert review.json()["publishedAt"] is None

    published = _post_json(
        admin_api_client,
        f"/api/v1/admin/content/landing/{landing.pk}/transition",
        {"to": "published", "reason": "approved by owner"},
    )
    assert published.status_code == 200
    assert published.json()["status"] == "published"
    assert published.json()["publishedAt"] is not None

    landing.refresh_from_db()
    assert landing.status == "published"
    assert landing.published_at is not None


@override_settings(REBUILD_TRIGGER_ENABLED=True)
def test_publish_invokes_rebuild_hook(admin_api_client):
    landing = _make_landing(locale="en", slug="rebuild-hook", title="Rebuild")
    with patch("apps.api.admin_content.invoke_static_rebuild") as mocked:
        published = _post_json(
            admin_api_client,
            f"/api/v1/admin/content/landing/{landing.pk}/transition",
            {"to": "published"},
        )
        assert published.status_code == 200
        mocked.assert_called_once()


def test_transition_published_to_archived_to_draft(admin_api_client):
    landing = _make_landing(
        locale="en", slug="archive", title="Archive", status="published"
    )

    archived = _post_json(
        admin_api_client,
        f"/api/v1/admin/content/landing/{landing.pk}/transition",
        {"to": "archived"},
    )
    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"

    draft = _post_json(
        admin_api_client,
        f"/api/v1/admin/content/landing/{landing.pk}/transition",
        {"to": "draft"},
    )
    assert draft.status_code == 200
    assert draft.json()["status"] == "draft"


def test_transition_invalid_400(admin_api_client):
    landing = _make_landing(
        locale="en", slug="stuck", title="Stuck", status="published"
    )
    response = _post_json(
        admin_api_client,
        f"/api/v1/admin/content/landing/{landing.pk}/transition",
        {"to": "published"},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "VALIDATION"
    assert body["message"] == "Invalid transition from published to published."


def test_transition_invalid_to_400(admin_api_client):
    landing = _make_landing(locale="en", slug="live", title="Live")
    response = _post_json(
        admin_api_client,
        f"/api/v1/admin/content/landing/{landing.pk}/transition",
        {"to": "live"},
    )
    assert response.status_code == 400
    assert response.json()["code"] == "VALIDATION"


def test_transition_unknown_entity_and_id_404(admin_api_client):
    unknown_entity = _post_json(
        admin_api_client,
        "/api/v1/admin/content/nope/1/transition",
        {"to": "review"},
    )
    assert unknown_entity.status_code == 404
    assert unknown_entity.json()["code"] == "NOT_FOUND"

    unknown_id = _post_json(
        admin_api_client,
        "/api/v1/admin/content/landing/999999/transition",
        {"to": "review"},
    )
    assert unknown_id.status_code == 404
    assert unknown_id.json()["code"] == "NOT_FOUND"


def test_transition_guards(csrf_client, admin_user):
    landing = _make_landing(locale="en", slug="guard", title="Guard")
    path = f"/api/v1/admin/content/landing/{landing.pk}/transition"

    anonymous = csrf_client.post(
        path, data=json.dumps({"to": "review"}), content_type="application/json"
    )
    assert anonymous.status_code == 401
    assert anonymous.json()["code"] == "AUTH_REQUIRED"

    csrf_client.force_login(admin_user)  # staff but NO otp session
    staff_no_otp = csrf_client.post(
        path, data=json.dumps({"to": "review"}), content_type="application/json"
    )
    assert staff_no_otp.status_code == 403
    assert staff_no_otp.json()["code"] == "OTP_REQUIRED"


def test_transition_audit_log(admin_api_client):
    landing = _make_landing(locale="en", slug="audit", title="Audit")
    response = _post_json(
        admin_api_client,
        f"/api/v1/admin/content/landing/{landing.pk}/transition",
        {"to": "review", "reason": "ready for review"},
    )
    assert response.status_code == 200

    log = AuditLog.objects.filter(
        action="lifecycle.draft->review",
        model_name="landing",
        object_id=str(landing.pk),
    ).first()
    assert log is not None
    assert "ready for review" in log.detail
    assert log.user is not None


def test_transition_reason_truncated(admin_api_client):
    landing = _make_landing(locale="en", slug="reason", title="Reason")
    long_reason = "x" * 1000
    response = _post_json(
        admin_api_client,
        f"/api/v1/admin/content/landing/{landing.pk}/transition",
        {"to": "review", "reason": long_reason},
    )
    assert response.status_code == 200

    log = AuditLog.objects.filter(action="lifecycle.draft->review").first()
    assert log is not None
    assert log.detail == f"reason={'x' * 500}"


# --- translation queue -----------------------------------------------------


def test_translation_queue_partial(admin_api_client):
    _make_landing(locale="en", slug="home", title="Home", body="<p>full body</p>")
    _make_landing(locale="fa", slug="home", title="خانه", body="")

    response = admin_api_client.get("/api/v1/admin/overview/translation-queue")
    assert response.status_code == 200
    body = response.json()
    assert body["truncated"] is False

    items = [i for i in body["items"] if i["entity"] == "landing" and i["slug"] == "home"]
    assert len(items) == 1
    item = items[0]
    assert item["status"] == "partial"
    assert item["fa"]["status"] == "incomplete"
    assert "body" in item["fa"]["missingFields"]
    assert "title" not in item["fa"]["missingFields"]
    assert item["en"]["status"] == "complete"
    assert item["en"]["missingFields"] == []


def test_translation_queue_missing(admin_api_client):
    Article.objects.create(
        locale="en",
        slug="only-en",
        title="Only EN",
        body="<p>body</p>",
        status="draft",
    )

    response = admin_api_client.get("/api/v1/admin/overview/translation-queue")
    assert response.status_code == 200
    items = [
        i
        for i in response.json()["items"]
        if i["entity"] == "article" and i["slug"] == "only-en"
    ]
    assert len(items) == 1
    item = items[0]
    assert item["status"] == "missing"
    assert item["fa"]["status"] == "missing"
    assert item["en"]["status"] == "complete"


def test_translation_queue_guards(csrf_client, admin_user):
    anonymous = csrf_client.get("/api/v1/admin/overview/translation-queue")
    assert anonymous.status_code == 401

    csrf_client.force_login(admin_user)
    staff_no_otp = csrf_client.get("/api/v1/admin/overview/translation-queue")
    assert staff_no_otp.status_code == 403


def test_translation_queue_bounded(admin_api_client):
    slugs = [f"bulk-{i:03d}" for i in range(101)]
    Landing.objects.bulk_create(
        [
            Landing(locale="en", slug=slug, title=f"Title {slug}", status="draft")
            for slug in slugs
        ]
    )

    response = admin_api_client.get("/api/v1/admin/overview/translation-queue")
    assert response.status_code == 200
    body = response.json()
    assert body["truncated"] is True
    assert len(body["items"]) == 100


# --- content health --------------------------------------------------------


def test_content_health(admin_api_client, media_root):
    _make_landing(locale="en", slug="pub", title="Pub", status="published")
    _make_landing(locale="en", slug="draft", title="Draft")
    Media.objects.create(
        file=SimpleUploadedFile("photo.png", PNG_1X1, content_type="image/png"),
        title="photo",
    )

    response = admin_api_client.get("/api/v1/admin/overview/content-health")
    assert response.status_code == 200
    body = response.json()
    assert body["published"] >= 1
    assert body["drafts"] >= 1
    assert body["review"] >= 0
    assert body["archived"] >= 0
    assert body["missingAltMedia"] >= 1
    assert body["orphanMedia"] >= 1


def test_content_health_guards(csrf_client, admin_user):
    anonymous = csrf_client.get("/api/v1/admin/overview/content-health")
    assert anonymous.status_code == 401

    csrf_client.force_login(admin_user)
    staff_no_otp = csrf_client.get("/api/v1/admin/overview/content-health")
    assert staff_no_otp.status_code == 403

def test_transition_preserves_published_at_through_archive_restore(admin_api_client):
    landing = _make_landing(
        locale="en", slug="keep-pub", title="Keep", status="published", body="b"
    )
    original = landing.published_at

    archived = _post_json(
        admin_api_client,
        f"/api/v1/admin/content/landing/{landing.pk}/transition",
        {"to": "archived"},
    )
    assert archived.status_code == 200
    landing.refresh_from_db()
    assert landing.published_at == original

    restored = _post_json(
        admin_api_client,
        f"/api/v1/admin/content/landing/{landing.pk}/transition",
        {"to": "draft"},
    )
    assert restored.status_code == 200
    assert restored.json()["status"] == "draft"
    landing.refresh_from_db()
    assert landing.published_at == original


def test_content_health_exact_counts(admin_api_client):
    from django.core.files.uploadedfile import SimpleUploadedFile

    from apps.media.models import Media

    PNG_1X1 = (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
        b"\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xcf\xc0\x00\x00\x00\x03"
        b"\x00\x01\xff\xff\xff\xff\xff\xff\xff\xff\x00\x00\x00\x00IEND\xaeB`\x82"
    )

    _make_landing(locale="en", slug="p1", title="P", status="published", body="b")
    _make_landing(locale="fa", slug="d1", title="D", status="draft")
    _make_landing(locale="en", slug="r1", title="R", status="review")
    _make_landing(locale="en", slug="a1", title="A", status="archived")
    Media.objects.create(
        file=SimpleUploadedFile("a.png", PNG_1X1, content_type="image/png"),
        title="no-alt",
    )
    Media.objects.create(
        file=SimpleUploadedFile("b.png", PNG_1X1, content_type="image/png"),
        title="with-alt",
        alt_text_fa="alt",
    )

    health = admin_api_client.get("/api/v1/admin/overview/content-health")
    assert health.status_code == 200
    body = health.json()
    assert body["published"] == 1
    assert body["drafts"] == 1
    assert body["review"] == 1
    assert body["archived"] == 1
    assert body["missingAltMedia"] == 1  # only the all-empty-alt media
    assert body["orphanMedia"] == 2  # both media are unused (no content FKs)
    assert body["incompleteTranslations"] >= 2

