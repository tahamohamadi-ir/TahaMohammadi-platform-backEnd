"""Staff content preview access boundary + headers (P3-07)."""

import pytest
from django.test import Client
from django.urls import reverse
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.content.models import Landing, LifecycleStatus, Locale
from apps.content.views_preview import sanitize_preview_body


@pytest.fixture
def admin_with_otp_client(admin_user):
    TOTPDevice.objects.create(user=admin_user, name="default", confirmed=True)
    client = Client()
    client.force_login(admin_user)
    device = admin_user.totpdevice_set.first()
    session = client.session
    session["otp_device_id"] = device.persistent_id
    session.save()
    return client


@pytest.fixture
def draft_landing(db):
    return Landing.objects.create(
        locale=Locale.EN,
        slug="preview-draft",
        title="Draft Home",
        body='Hello <script>alert(1)</script><p>ok</p>',
        status=LifecycleStatus.DRAFT,
        published_at=None,
    )


@pytest.mark.django_db
class TestStaffContentPreview:
    def test_anonymous_redirects_to_login(self, draft_landing):
        url = reverse(
            "content_staff_preview",
            kwargs={"kind": "landing", "pk": draft_landing.pk},
        )
        response = Client().get(url)
        assert response.status_code in (301, 302)
        assert "/staff/login/" in response.url
        assert b"Draft Home" not in response.content

    def test_non_staff_cannot_read_draft(self, draft_landing, user):
        client = Client()
        client.force_login(user)
        url = reverse(
            "content_staff_preview",
            kwargs={"kind": "landing", "pk": draft_landing.pk},
        )
        response = client.get(url)
        assert response.status_code in (302, 403)
        assert b"Draft Home" not in response.content
        assert b"<script>" not in response.content

    def test_staff_with_device_no_otp_cannot_read_draft(self, draft_landing, admin_user):
        TOTPDevice.objects.create(user=admin_user, name="default", confirmed=True)
        client = Client()
        client.force_login(admin_user)
        url = reverse(
            "content_staff_preview",
            kwargs={"kind": "landing", "pk": draft_landing.pk},
        )
        response = client.get(url)
        assert response.status_code == 302
        assert "/staff/login/" in response.url
        assert b"Draft Home" not in response.content

    def test_staff_otp_sees_draft_with_headers(self, admin_with_otp_client, draft_landing):
        url = reverse(
            "content_staff_preview",
            kwargs={"kind": "landing", "pk": draft_landing.pk},
        )
        response = admin_with_otp_client.get(url)
        assert response.status_code == 200
        assert b"Draft Home" in response.content
        assert b"<script>" not in response.content
        assert b"ok" in response.content
        assert response.headers["X-Robots-Tag"] == "noindex, nofollow, noarchive"
        assert response.headers["Cache-Control"] == "no-store"

    def test_unknown_kind_404(self, admin_with_otp_client, draft_landing):
        url = reverse(
            "content_staff_preview",
            kwargs={"kind": "missing", "pk": draft_landing.pk},
        )
        response = admin_with_otp_client.get(url)
        assert response.status_code == 404

    def test_missing_pk_404(self, admin_with_otp_client):
        url = reverse(
            "content_staff_preview",
            kwargs={"kind": "landing", "pk": 999999},
        )
        response = admin_with_otp_client.get(url)
        assert response.status_code == 404

    def test_sanitize_strips_script(self):
        cleaned = sanitize_preview_body(
            '<p>hi</p><script>alert(1)</script><img src=x onerror=alert(1)>'
        )
        assert "<script" not in cleaned
        assert "onerror" not in cleaned
