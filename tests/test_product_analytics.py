"""Tests for PU-20-events: First-party aggregate analytics ingest/reporting and 13-month retention.

Contract: Docs/03-contracts/PRODUCT-INTERFACES-V2.md §I07
Packet: Docs/05-delivery/concept-alignment-v2/product-packets/PU-20-events.md
"""

from __future__ import annotations

import datetime
from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client
from django.utils import timezone
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.analytics.api import analytics_admin_router, analytics_public_router
from apps.analytics.models import AggregateEvent


@pytest.fixture(autouse=True)
def db_access(db):
    """Ensure database access for all tests."""
    pass


@pytest.fixture
def admin_user():
    """Create superuser with verified OTP device."""
    user_model = get_user_model()
    user = user_model.objects.create_superuser(
        username="admin-analytics",
        email="admin-analytics@example.com",
        password="ValidPassword123!",
    )
    TOTPDevice.objects.create(user=user, name="default", confirmed=True)
    return user


@pytest.fixture
def admin_client(admin_user):
    """Client with staff session and verified OTP device."""
    device = TOTPDevice.objects.get(user=admin_user)
    client = Client()
    client.force_login(admin_user)
    session = client.session
    session["otp_device_id"] = device.persistent_id
    session["django_otp_device_id"] = device.persistent_id
    session.save()
    csrf_token = client.get("/api/v1/admin/auth/csrf").json()["csrfToken"]
    client.defaults["HTTP_X_CSRFTOKEN"] = csrf_token
    return client


def test_analytics_app_installed_and_routes_exist():
    """Verify analytics models and routers can be imported and resolved."""
    assert AggregateEvent is not None
    assert analytics_public_router is not None
    assert analytics_admin_router is not None


def test_public_event_ingest_success_and_daily_aggregation():
    """Public ingest accepts valid event and aggregates count atomically per calendar day."""
    AggregateEvent.objects.all().delete()
    client = Client()

    payload = {
        "event": "page_view",
        "pagePath": "/en/blog/intro/",
        "locale": "en",
    }
    res = client.post(
        "/api/v1/analytics/events",
        data=payload,
        content_type="application/json",
    )
    assert res.status_code in (200, 202)
    assert res.json() == {"status": "accepted"}

    today = timezone.now().date()
    record = AggregateEvent.objects.get(
        date=today,
        locale="en",
        page_path="/en/blog/intro/",
        event="page_view",
        target="",
    )
    assert record.count == 1

    # Ingest a second event on the same day -> count increments to 2
    res2 = client.post(
        "/api/v1/analytics/events",
        data=payload,
        content_type="application/json",
    )
    assert res2.status_code in (200, 202)
    record.refresh_from_db()
    assert record.count == 2

    # Ingest action event with target ID
    action_payload = {
        "event": "cv_download",
        "pagePath": "/fa/about/",
        "locale": "fa",
        "target": "academic_cv",
    }
    res3 = client.post(
        "/api/v1/analytics/events",
        data=action_payload,
        content_type="application/json",
    )
    assert res3.status_code in (200, 202)
    action_rec = AggregateEvent.objects.get(
        date=today,
        locale="fa",
        page_path="/fa/about/",
        event="cv_download",
        target="academic_cv",
    )
    assert action_rec.count == 1


def test_public_event_ingest_validation_rules():
    """Public ingest strictly validates canonical path, event enum, locale, and target ID."""
    client = Client()

    # 1. Invalid event enum
    res = client.post(
        "/api/v1/analytics/events",
        data={"event": "user_login", "pagePath": "/en/", "locale": "en"},
        content_type="application/json",
    )
    assert res.status_code == 400

    # 2. Invalid locale
    res = client.post(
        "/api/v1/analytics/events",
        data={"event": "page_view", "pagePath": "/fr/", "locale": "fr"},
        content_type="application/json",
    )
    assert res.status_code == 400

    # 3. Path with query parameters rejected
    res = client.post(
        "/api/v1/analytics/events",
        data={"event": "page_view", "pagePath": "/en/blog/?ref=twitter", "locale": "en"},
        content_type="application/json",
    )
    assert res.status_code == 400

    # 4. Path with hash fragment rejected
    res = client.post(
        "/api/v1/analytics/events",
        data={"event": "page_view", "pagePath": "/en/blog/#heading", "locale": "en"},
        content_type="application/json",
    )
    assert res.status_code == 400

    # 5. Non-slash starting path rejected
    res = client.post(
        "/api/v1/analytics/events",
        data={"event": "page_view", "pagePath": "en/blog/", "locale": "en"},
        content_type="application/json",
    )
    assert res.status_code == 400

    # 6. Arbitrary URL in target rejected
    res = client.post(
        "/api/v1/analytics/events",
        data={"event": "demo_click", "pagePath": "/en/", "locale": "en", "target": "https://malicious.com"},
        content_type="application/json",
    )
    assert res.status_code == 400

    # 7. Arbitrary text with spaces in target rejected
    res = client.post(
        "/api/v1/analytics/events",
        data={
            "event": "demo_click",
            "pagePath": "/en/",
            "locale": "en",
            "target": "action with spaces",
        },
        content_type="application/json",
    )
    assert res.status_code == 400


def test_public_event_ingest_privacy_extra_fields_rejected():
    """Privacy boundary: extra fields (emails, cookies, visitor IDs, referrers) are rejected."""
    client = Client()

    # Reject email
    res = client.post(
        "/api/v1/analytics/events",
        data={
            "event": "page_view",
            "pagePath": "/en/",
            "locale": "en",
            "email": "user@example.com",
        },
        content_type="application/json",
    )
    assert res.status_code == 422

    # Reject visitor_id / user_id
    res = client.post(
        "/api/v1/analytics/events",
        data={
            "event": "page_view",
            "pagePath": "/en/",
            "locale": "en",
            "visitor_id": "vid-123456",
        },
        content_type="application/json",
    )
    assert res.status_code == 422

    # Reject cookie
    res = client.post(
        "/api/v1/analytics/events",
        data={
            "event": "page_view",
            "pagePath": "/en/",
            "locale": "en",
            "cookie": "session=xyz",
        },
        content_type="application/json",
    )
    assert res.status_code == 422


def test_public_event_ingest_cross_origin_rejected():
    """Foreign cross-origin requests are rejected (§I07 same-origin enforcement)."""
    client = Client()
    res = client.post(
        "/api/v1/analytics/events",
        data={"event": "page_view", "pagePath": "/en/", "locale": "en"},
        content_type="application/json",
        HTTP_ORIGIN="https://foreign-tracker.evil.com",
    )
    assert res.status_code == 403


def _post_event(client, payload, **extra):
    import json as _json

    return client.post(
        "/api/v1/analytics/events",
        data=_json.dumps(payload),
        content_type="application/json",
        **extra,
    )


def test_a09_unregistered_target_rejected_with_envelope():
    """A09: target must be a registered action ID; anything else gets the envelope."""
    AggregateEvent.objects.all().delete()
    client = Client()
    res = _post_event(
        client,
        {
            "event": "demo_click",
            "pagePath": "/en/",
            "locale": "en",
            "target": "homepage-hero",
        },
    )
    assert res.status_code == 400
    body = res.json()
    assert body["code"] == "INVALID_INPUT"
    assert body["message"]
    assert body["request_id"]
    assert AggregateEvent.objects.count() == 0


def test_a09_page_view_rejects_nonempty_target():
    """A09: page_view carries no action target."""
    client = Client()
    res = _post_event(
        client,
        {
            "event": "page_view",
            "pagePath": "/en/",
            "locale": "en",
            "target": "academic_cv",
        },
    )
    assert res.status_code == 400
    assert res.json()["code"] == "INVALID_INPUT"


def test_a09_unknown_route_and_locale_mismatch_rejected():
    """A09: pagePath must be a canonical route consistent with the locale."""
    client = Client()
    unknown = _post_event(
        client,
        {"event": "page_view", "pagePath": "/en/tags/something/", "locale": "en"},
    )
    assert unknown.status_code == 400
    assert unknown.json()["code"] == "INVALID_INPUT"

    mismatch = _post_event(
        client,
        {"event": "page_view", "pagePath": "/fa/about/", "locale": "en"},
    )
    assert mismatch.status_code == 400
    assert mismatch.json()["code"] == "INVALID_INPUT"


def test_a09_gateway_root_accepted_for_either_locale():
    """A09: the locale-neutral language gateway '/' is a canonical path."""
    client = Client()
    for locale in ("en", "fa"):
        res = _post_event(
            client, {"event": "page_view", "pagePath": "/", "locale": locale}
        )
        assert res.status_code in (200, 202)


def test_a09_oversized_payload_rejected():
    """A09: oversized bodies get a controlled 413 and store nothing."""
    AggregateEvent.objects.all().delete()
    client = Client()
    big_target = "x" * 5000
    res = _post_event(
        client,
        {"event": "page_view", "pagePath": "/en/", "locale": "en", "target": big_target},
    )
    assert res.status_code == 413
    assert res.json()["code"] == "PAYLOAD_TOO_LARGE"
    assert AggregateEvent.objects.count() == 0


def test_a09_extra_fields_envelope_422():
    """A09: schema-shape rejections keep 422 but follow the error envelope."""
    client = Client()
    res = _post_event(
        client,
        {
            "event": "page_view",
            "pagePath": "/en/",
            "locale": "en",
            "email": "user@example.com",
        },
    )
    assert res.status_code == 422
    body = res.json()
    assert body["code"] == "INVALID_INPUT"
    assert body["request_id"]


def test_a09_registered_cv_targets_accepted():
    """A09: the two real document slots are the cv_download registry."""
    AggregateEvent.objects.all().delete()
    client = Client()
    for target in ("academic_cv", "industry_resume"):
        res = _post_event(
            client,
            {
                "event": "cv_download",
                "pagePath": "/en/cv/",
                "locale": "en",
                "target": target,
            },
        )
        assert res.status_code in (200, 202), res.content[:300]
    assert AggregateEvent.objects.filter(event="cv_download").count() == 2


def test_admin_analytics_requires_staff_and_otp():
    """Unauthenticated or non-OTP clients cannot read analytics."""
    client = Client()

    # Anonymous access
    res = client.get("/api/v1/admin/analytics")
    assert res.status_code in (401, 403, 404)

    # Staff session without OTP
    user_model = get_user_model()
    staff = user_model.objects.create_user(
        username="staff-no-otp",
        email="staff-no-otp@example.com",
        password="ValidPassword123!",
        is_staff=True,
    )
    client.force_login(staff)
    res2 = client.get("/api/v1/admin/analytics")
    assert res2.status_code in (401, 403, 404)


def test_admin_analytics_report_success_and_wire_shape(admin_client):
    """Admin report returns received_events wire shape with date and locale filtering."""
    AggregateEvent.objects.all().delete()

    d1 = datetime.date(2026, 8, 10)
    d2 = datetime.date(2026, 8, 11)

    AggregateEvent.objects.create(
        date=d1,
        locale="en",
        page_path="/en/blog/",
        event="page_view",
        target="",
        count=15,
    )
    AggregateEvent.objects.create(
        date=d1,
        locale="fa",
        page_path="/fa/blog/",
        event="page_view",
        target="",
        count=8,
    )
    AggregateEvent.objects.create(
        date=d2,
        locale="en",
        page_path="/en/resources/paper/",
        event="cv_download",
        target="pdf",
        count=3,
    )

    res = admin_client.get("/api/v1/admin/analytics?from=2026-08-01&to=2026-08-31")
    assert res.status_code == 200
    data = res.json()

    assert data["from"] == "2026-08-01"
    assert data["to"] == "2026-08-31"
    assert data["timezone"] == "UTC"
    assert data["metric"] == "received_events"
    assert "updatedAt" in data
    assert len(data["rows"]) == 3

    # Test locale filter: locale=fa
    fa_res = admin_client.get("/api/v1/admin/analytics?from=2026-08-01&to=2026-08-31&locale=fa")
    assert fa_res.status_code == 200
    fa_data = fa_res.json()
    assert len(fa_data["rows"]) == 1
    assert fa_data["rows"][0]["locale"] == "fa"
    assert fa_data["rows"][0]["count"] == 8


def test_admin_analytics_validation_limits(admin_client):
    """Admin report rejects range > 366 days, inverted dates, and invalid formats."""
    # 1. Date range > 366 days
    res = admin_client.get("/api/v1/admin/analytics?from=2024-01-01&to=2025-02-01")
    assert res.status_code == 400
    assert "366" in res.json()["message"]

    # 2. Inverted dates: from > to
    res2 = admin_client.get("/api/v1/admin/analytics?from=2026-08-20&to=2026-08-10")
    assert res2.status_code == 400

    # 3. Invalid date format
    res3 = admin_client.get("/api/v1/admin/analytics?from=not-a-date&to=2026-08-10")
    assert res3.status_code == 400

    # 4. Invalid locale
    res4 = admin_client.get("/api/v1/admin/analytics?locale=es")
    assert res4.status_code == 400


def test_prune_analytics_command():
    """prune_analytics removes records older than 13 months (~396 days)."""
    AggregateEvent.objects.all().delete()
    today = timezone.now().date()

    stale_date = today - datetime.timedelta(days=420)
    recent_date = today - datetime.timedelta(days=30)

    stale = AggregateEvent.objects.create(
        date=stale_date,
        locale="en",
        page_path="/en/old/",
        event="page_view",
        target="",
        count=5,
    )
    recent = AggregateEvent.objects.create(
        date=recent_date,
        locale="en",
        page_path="/en/recent/",
        event="page_view",
        target="",
        count=10,
    )

    # Dry-run does not delete
    out_dry = StringIO()
    call_command("prune_analytics", dry_run=True, stdout=out_dry)
    assert AggregateEvent.objects.filter(pk=stale.pk).exists()
    assert "[DRY-RUN]" in out_dry.getvalue()

    # Actual prune deletes stale, keeps recent
    out_run = StringIO()
    call_command("prune_analytics", stdout=out_run)
    assert not AggregateEvent.objects.filter(pk=stale.pk).exists()
    assert AggregateEvent.objects.filter(pk=recent.pk).exists()
    assert "Successfully pruned 1" in out_run.getvalue()
