"""BACKEND-210: owner approval queue surface + publish gate."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from django.core.cache import cache
from django.core.management import call_command
from django.test import Client
from django.utils import timezone

from apps.content.models import ContentSeedRecord, Landing, LifecycleStatus


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


def _make_landing(*, locale="en", slug="home", title="Home", status="draft"):
    return Landing.objects.create(
        locale=locale,
        slug=slug,
        title=title,
        status=status,
        body="v1",
    )


def _seed_for(item, *, approval_state="needs-owner-input", publication_state="draft"):
    return ContentSeedRecord.objects.create(
        content_id=f"landing.{item.slug}.{item.locale}",
        content_type="landing",
        locale=item.locale,
        slug=item.slug,
        title=item.title,
        approval_state=approval_state,
        publication_state=publication_state,
        translation_state="translated",
        visibility="public",
        payload={},
        mapped_model_label="content.Landing",
        mapped_object_id=item.pk,
    )


@pytest.mark.django_db
def test_approval_queue_requires_admin_otp(db, csrf_client, admin_user, totp_device):
    csrf_client.force_login(admin_user)
    response = csrf_client.get("/api/v1/admin/approval-queue")
    assert response.status_code == 403


@pytest.mark.django_db
def test_approval_queue_counts_and_default_filter(admin_api_client, db):
    landing = _make_landing()
    _seed_for(landing, approval_state="needs-owner-input")
    approved = _make_landing(locale="fa", slug="home-fa", title="خانه")
    _seed_for(approved, approval_state="approved", publication_state="published")

    response = admin_api_client.get("/api/v1/admin/approval-queue")
    assert response.status_code == 200
    body = response.json()
    # Default filter: only rows whose approval has not cleared.
    assert [item["contentId"] for item in body["items"]] == [
        f"landing.home.{landing.locale}"
    ]
    assert body["counts"] == {"total": 2, "approved": 1, "notApproved": 1}

    all_rows = admin_api_client.get("/api/v1/admin/approval-queue?state=all")
    assert all_rows.status_code == 200
    assert len(all_rows.json()["items"]) == 2

    invalid = admin_api_client.get("/api/v1/admin/approval-queue?state=bogus")
    assert invalid.status_code == 400


@pytest.mark.django_db
def test_publish_blocked_without_owner_approval(admin_api_client, db):
    landing = _make_landing()
    _seed_for(landing, approval_state="needs-owner-input")

    response = _post_json(
        admin_api_client,
        f"/api/v1/admin/content/landing/{landing.pk}/transition",
        {"to": "published"},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "APPROVAL_REQUIRED"

    landing.refresh_from_db()
    assert landing.status == LifecycleStatus.DRAFT


@pytest.mark.django_db
def test_publish_allowed_once_owner_triple_cleared(admin_api_client, db):
    landing = _make_landing()
    _seed_for(
        landing,
        approval_state="approved",
        publication_state="published",
    )

    response = _post_json(
        admin_api_client,
        f"/api/v1/admin/content/landing/{landing.pk}/transition",
        {"to": "published"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "published"


@pytest.mark.django_db
def test_publish_allowed_without_seed_provenance(admin_api_client, db):
    """Admin-created rows carry no seed gate: the owner authors them here."""
    landing = _make_landing()
    response = _post_json(
        admin_api_client,
        f"/api/v1/admin/content/landing/{landing.pk}/transition",
        {"to": "published"},
    )
    assert response.status_code == 200


@pytest.mark.django_db
def test_approval_state_exposed_in_list_and_detail(admin_api_client, db):
    landing = _make_landing()
    _seed_for(landing, approval_state="needs-owner-input")

    listing = admin_api_client.get("/api/v1/admin/content/landing").json()
    assert listing["items"][0]["approvalState"] == "needs-owner-input"

    detail = admin_api_client.get(
        f"/api/v1/admin/content/landing/{landing.pk}"
    ).json()
    assert detail["approvalState"] == "needs-owner-input"


@pytest.mark.django_db
def test_scheduled_publish_command_honors_gate(admin_api_client, db):
    """The scheduler must not bypass the owner approval gate."""
    from io import StringIO

    landing = _make_landing(status="scheduled")
    landing.scheduled_for = timezone.now() - timedelta(minutes=1)
    landing.save(update_fields=["scheduled_for"])
    _seed_for(landing, approval_state="needs-owner-input")

    out = StringIO()
    with pytest.raises(SystemExit):
        call_command("publish_scheduled_content", stdout=out, stderr=out)
    # The blocked row is reported (nonzero exit) and never published.
    assert "APPROVAL_REQUIRED" in out.getvalue()
    landing.refresh_from_db()
    assert landing.status == LifecycleStatus.SCHEDULED

