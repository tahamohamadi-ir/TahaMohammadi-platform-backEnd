"""Public contact endpoint + site-settings contact projection (board A10)."""

import json

import pytest
from django.core import mail
from django.core.cache import cache
from django.test import Client

from apps.siteconfig.models import SiteSettings


@pytest.fixture(autouse=True)
def _contact_env(settings, db):
    settings.EMAIL_HOST = "smtp.example.com"
    settings.EMAIL_PORT = 587
    settings.CONTACT_FORM_TO = ""
    cache.clear()
    row = SiteSettings.get_singleton()
    row.contact_email = "owner@example.com"
    row.contact_form_enabled = True
    row.save()
    mail.outbox.clear()
    yield
    cache.clear()


@pytest.fixture
def totp_device(db, admin_user):
    from django_otp.plugins.otp_totp.models import TOTPDevice

    return TOTPDevice.objects.create(user=admin_user, name="default", confirmed=True)


@pytest.fixture
def admin_api_client(totp_device, admin_user):
    client = Client(enforce_csrf_checks=True)
    client.force_login(admin_user)
    session = client.session
    session["otp_device_id"] = totp_device.persistent_id
    session.save()
    token = client.get("/api/v1/admin/auth/csrf").json()["csrfToken"]
    client.defaults["HTTP_X_CSRFTOKEN"] = token
    return client


def _post_json(client, payload, **headers):
    return client.post(
        "/api/contact",
        data=json.dumps(payload),
        content_type="application/json",
        **headers,
    )


@pytest.mark.django_db
def test_contact_json_submission_sends_email_once():
    response = _post_json(
        Client(),
        {"name": "Sara", "email": "sara@example.com", "message": "Hello", "locale": "en"},
    )
    assert response.status_code == 200, response.content
    assert response.json()["ok"] is True
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["owner@example.com"]
    assert "sara@example.com" in mail.outbox[0].body


@pytest.mark.django_db
def test_contact_form_submission_returns_html_no_store():
    response = Client().post(
        "/api/contact",
        data={
            "name": "",
            "email": "nojs@example.com",
            "message": "پیام تست",
            "locale": "fa",
        },
    )
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/html")
    assert "پیام شما ارسال شد" in response.content.decode("utf-8")
    assert len(mail.outbox) == 1


@pytest.mark.django_db
def test_contact_honeypot_silently_dropped():
    response = _post_json(
        Client(),
        {
            "email": "bot@example.com",
            "message": "spam",
            "website": "http://spam.example",
        },
    )
    assert response.status_code == 200
    assert len(mail.outbox) == 0


@pytest.mark.django_db
def test_contact_invalid_email_400():
    response = _post_json(Client(), {"email": "not-an-email", "message": "hi"})
    assert response.status_code == 400


@pytest.mark.django_db
def test_contact_empty_message_400():
    response = _post_json(Client(), {"email": "a@b.co", "message": "  "})
    assert response.status_code == 400


@pytest.mark.django_db
def test_contact_cross_origin_rejected():
    response = _post_json(
        Client(),
        {"email": "a@b.co", "message": "hi"},
        HTTP_ORIGIN="https://evil.example",
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_contact_rate_limited_429():
    client = Client()
    for _ in range(5):
        ok = _post_json(client, {"email": "a@b.co", "message": "hi"})
        assert ok.status_code == 200
    sixth = _post_json(client, {"email": "a@b.co", "message": "hi"})
    assert sixth.status_code == 429


@pytest.mark.django_db
def test_contact_disabled_returns_404():
    row = SiteSettings.get_singleton()
    row.contact_form_enabled = False
    row.save()
    response = _post_json(Client(), {"email": "a@b.co", "message": "hi"})
    assert response.status_code == 404


@pytest.mark.django_db
def test_contact_without_smtp_config_503(settings):
    settings.EMAIL_HOST = ""
    response = _post_json(Client(), {"email": "a@b.co", "message": "hi"})
    assert response.status_code == 503


@pytest.mark.django_db
def test_contact_audit_log_has_no_body():
    _post_json(Client(), {"name": "X", "email": "x@example.com", "message": "secret-words"})
    from apps.security.models import AuditLog

    entry = AuditLog.objects.filter(action="contact.sent").latest("id")
    assert "secret-words" not in (entry.detail or "")
    assert entry.object_id == "x@example.com"


@pytest.mark.django_db
def test_public_site_settings_project_contact_block():
    client = Client()
    response = client.get("/api/site")
    assert response.status_code == 200
    contact = response.json()["contact"]
    assert contact["email"] == "owner@example.com"
    assert contact["formEnabled"] is True


@pytest.mark.django_db
def test_public_site_settings_never_leaks_phone_numbers():
    """Phones stay private (owner decision 2026-08-23) even when stored."""
    row = SiteSettings.get_singleton()
    row.contact_phone = "+98 910 000 0000"
    row.contact_phone_intl = "+1 202 555 0100"
    row.save()

    body = Client().get("/api/site").content.decode("utf-8")
    assert "+98 910 000 0000" not in body
    assert "+1 202 555 0100" not in body
    assert "phone" not in json.loads(body)["contact"]


@pytest.mark.django_db
def test_admin_site_settings_contact_roundtrip(admin_api_client):
    detail = admin_api_client.get("/api/v1/admin/site")
    assert detail.status_code == 200
    updated_at = detail.json()["updatedAt"]

    updated = admin_api_client.put(
        "/api/v1/admin/site",
        data=json.dumps(
            {
                "contactEmail": "new@example.com",
                "contactPhone": "+98 912 000 0000",
                "contactLinkedin": "https://linkedin.com/in/new",
                "contactFormEnabled": False,
            }
        ),
        content_type="application/json",
        HTTP_IF_MATCH=updated_at,
    )
    assert updated.status_code == 200, updated.content
    data = updated.json()
    assert data["contactEmail"] == "new@example.com"
    assert data["contactFormEnabled"] is False
    assert data["contactLocation"] == "Tehran, Iran"  # untouched fields persist

    bad = admin_api_client.put(
        "/api/v1/admin/site",
        data=json.dumps({"contactEmail": "nope"}),
        content_type="application/json",
        HTTP_IF_MATCH=data["updatedAt"],
    )
    assert bad.status_code == 400
