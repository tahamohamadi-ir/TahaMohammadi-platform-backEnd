"""ADM-4 / DEBT-0005: revisions + scheduled publish."""

from __future__ import annotations

import json
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.core.management import call_command
from django.test import Client
from django.utils import timezone

from apps.content.models import ContentRevision, Landing, LifecycleStatus
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


def _post_json(client, path, payload):
    return client.post(
        path,
        data=json.dumps(payload),
        content_type="application/json",
    )


def _make_landing(*, locale="en", slug="home", title="Home", status="draft", body="v1"):
    return Landing.objects.create(
        locale=locale,
        slug=slug,
        title=title,
        status=status,
        body=body,
        published_at=timezone.now() if status == LifecycleStatus.PUBLISHED else None,
    )


def test_transition_to_scheduled_requires_future_datetime(admin_api_client):
    landing = _make_landing()
    past = timezone.now() - timedelta(hours=1)
    bad = _post_json(
        admin_api_client,
        f"/api/v1/admin/content/landing/{landing.pk}/transition",
        {"to": "scheduled", "scheduledFor": past.isoformat()},
    )
    assert bad.status_code == 400
    assert bad.json()["code"] == "VALIDATION"

    missing = _post_json(
        admin_api_client,
        f"/api/v1/admin/content/landing/{landing.pk}/transition",
        {"to": "scheduled"},
    )
    assert missing.status_code == 400

    future = timezone.now() + timedelta(hours=2)
    ok = _post_json(
        admin_api_client,
        f"/api/v1/admin/content/landing/{landing.pk}/transition",
        {"to": "scheduled", "scheduledFor": future.isoformat(), "reason": "launch"},
    )
    assert ok.status_code == 200
    body = ok.json()
    assert body["status"] == "scheduled"
    assert body["scheduledFor"] is not None
    landing.refresh_from_db()
    assert landing.status == LifecycleStatus.SCHEDULED
    assert landing.scheduled_for is not None


def test_transition_scheduled_to_draft_clears_scheduled_for(admin_api_client):
    landing = _make_landing()
    future = timezone.now() + timedelta(days=1)
    scheduled = _post_json(
        admin_api_client,
        f"/api/v1/admin/content/landing/{landing.pk}/transition",
        {"to": "scheduled", "scheduledFor": future.isoformat()},
    )
    assert scheduled.status_code == 200

    draft = _post_json(
        admin_api_client,
        f"/api/v1/admin/content/landing/{landing.pk}/transition",
        {"to": "draft"},
    )
    assert draft.status_code == 200
    assert draft.json()["status"] == "draft"
    assert draft.json()["scheduledFor"] is None
    landing.refresh_from_db()
    assert landing.scheduled_for is None


def test_publish_scheduled_command_publishes_due_rows(db):
    due = _make_landing(slug="due", title="Due")
    due.status = LifecycleStatus.SCHEDULED
    due.scheduled_for = timezone.now() - timedelta(minutes=1)
    due.save()

    future = _make_landing(slug="later", title="Later")
    future.status = LifecycleStatus.SCHEDULED
    future.scheduled_for = timezone.now() + timedelta(hours=3)
    future.save()

    with patch(
        "apps.content.management.commands.publish_scheduled_content.invoke_static_rebuild"
    ) as mocked:
        call_command("publish_scheduled_content")
        mocked.assert_called_once()

    due.refresh_from_db()
    future.refresh_from_db()
    assert due.status == LifecycleStatus.PUBLISHED
    assert due.published_at is not None
    assert due.scheduled_for is None
    assert future.status == LifecycleStatus.SCHEDULED
    assert future.scheduled_for is not None

    log = AuditLog.objects.filter(
        action="lifecycle.scheduled->published",
        model_name="landing",
        object_id=str(due.pk),
    ).first()
    assert log is not None


def test_publish_scheduled_command_idempotent(db):
    landing = _make_landing(slug="once")
    landing.status = LifecycleStatus.SCHEDULED
    landing.scheduled_for = timezone.now() - timedelta(seconds=5)
    landing.save()

    with patch(
        "apps.content.management.commands.publish_scheduled_content.invoke_static_rebuild"
    ):
        call_command("publish_scheduled_content")
        call_command("publish_scheduled_content")

    landing.refresh_from_db()
    assert landing.status == LifecycleStatus.PUBLISHED
    assert (
        AuditLog.objects.filter(
            action="lifecycle.scheduled->published", object_id=str(landing.pk)
        ).count()
        == 1
    )


def test_revision_create_and_restore_as_draft(admin_api_client):
    landing = _make_landing(body="original")
    # Publish so restore must force draft (never leave published with old body).
    published = _post_json(
        admin_api_client,
        f"/api/v1/admin/content/landing/{landing.pk}/transition",
        {"to": "published"},
    )
    assert published.status_code == 200

    snap = _post_json(
        admin_api_client,
        f"/api/v1/admin/content/landing/{landing.pk}/revisions",
        {"note": "before edit"},
    )
    assert snap.status_code == 201
    rev_id = snap.json()["id"]
    assert ContentRevision.objects.filter(pk=rev_id).count() == 1

    landing.body = "changed live"
    landing.title = "Changed"
    landing.save()

    restore = _post_json(
        admin_api_client,
        f"/api/v1/admin/content/landing/{landing.pk}/revisions/{rev_id}/restore",
        {},
    )
    assert restore.status_code == 200
    body = restore.json()
    assert body["status"] == "draft"
    assert body["title"] == "Home"
    assert body["fields"]["body"] == "original"

    landing.refresh_from_db()
    assert landing.status == LifecycleStatus.DRAFT
    assert landing.body == "original"
    # Pre-restore snapshot of the live row must exist.
    assert ContentRevision.objects.filter(
        entity_key="landing", object_id=landing.pk, note="pre-restore snapshot"
    ).exists()

    listed = admin_api_client.get(
        f"/api/v1/admin/content/landing/{landing.pk}/revisions"
    )
    assert listed.status_code == 200
    assert len(listed.json()["items"]) >= 2


def test_create_rejects_scheduled_status(admin_api_client):
    response = _post_json(
        admin_api_client,
        "/api/v1/admin/content/landing",
        {
            "locale": "en",
            "slug": "no-direct",
            "title": "Nope",
            "status": "scheduled",
            "fields": {},
        },
    )
    assert response.status_code == 400
    assert "transition" in response.json()["message"]
