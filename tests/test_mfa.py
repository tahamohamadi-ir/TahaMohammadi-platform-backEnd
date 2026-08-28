"""MFA enforcement + TOTP enrollment + OTP login form tests."""

import pytest
from django.test import Client
from django.urls import reverse
from django_otp.oath import totp
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.security.models import AuditLog, RecoveryCode
from apps.security.recovery import (
    issue_recovery_codes,
    normalize_recovery_code,
    unused_recovery_code_count,
)


@pytest.fixture
def admin_with_device(admin_user):
    """Superuser with a confirmed TOTP device."""
    TOTPDevice.objects.create(user=admin_user, name="default", confirmed=True)
    return admin_user


@pytest.fixture
def admin_no_otp_client(admin_with_device):
    """Client logged in as admin_with_device but NO OTP verification in session."""
    client = Client()
    client.force_login(admin_with_device)
    return client


@pytest.fixture
def admin_with_otp_client(admin_with_device):
    """Client logged in as admin_with_device WITH OTP verified in session."""
    client = Client()
    client.force_login(admin_with_device)
    device = admin_with_device.totpdevice_set.first()
    session = client.session
    session["otp_device_id"] = device.persistent_id
    session.save()
    return client


def _current_token(device: TOTPDevice) -> str:
    return f"{totp(device.bin_key):06d}"


class TestMFAEnforcement:
    def test_unauthenticated_admin_redirects_to_login(self, db):
        response = Client().get("/staff/profiles/")
        assert response.status_code in (301, 302)
        assert "/staff/login/" in response.url

    def test_admin_without_device_redirected_to_setup(self, db, admin_client):
        response = admin_client.get("/staff/profiles/")
        assert response.status_code == 302
        assert "/staff/account/two-factor/" in response.url

    def test_admin_without_device_can_open_setup(self, db, admin_client):
        response = admin_client.get("/staff/account/two-factor/")
        assert response.status_code == 200
        assert b"Confirm and enable" in response.content or b"otp_token" in response.content

    def test_admin_with_device_no_otp_blocked(self, db, admin_no_otp_client):
        response = admin_no_otp_client.get("/staff/profiles/")
        assert response.status_code == 302
        assert "/staff/login/" in response.url

    def test_admin_with_verified_otp_can_access_admin(self, db, admin_with_otp_client):
        response = admin_with_otp_client.get("/staff/profiles/")
        assert response.status_code == 200

    def test_non_admin_path_not_affected_by_mfa_guard(self, db, admin_no_otp_client):
        response = admin_no_otp_client.get("/health/")
        assert response.status_code == 200


class TestTOTPEnrollment:
    def test_setup_creates_unconfirmed_device(self, db, admin_client, admin_user):
        assert TOTPDevice.objects.filter(user=admin_user).count() == 0
        response = admin_client.get(reverse("security_totp_setup"))
        assert response.status_code == 200
        device = TOTPDevice.objects.get(user=admin_user)
        assert device.confirmed is False

    def test_confirm_enables_device_and_sets_session(self, db, admin_client, admin_user):
        admin_client.get(reverse("security_totp_setup"))
        device = TOTPDevice.objects.get(user=admin_user, confirmed=False)
        token = _current_token(device)
        response = admin_client.post(
            reverse("security_totp_setup"),
            {"otp_token": token},
        )
        assert response.status_code == 302
        assert reverse("security_recovery_codes_reveal") in response.url
        device.refresh_from_db()
        assert device.confirmed is True
        assert admin_client.session.get("otp_device_id") == device.persistent_id
        assert unused_recovery_code_count(admin_user) == 10

        reveal = admin_client.get(reverse("security_recovery_codes_reveal"))
        assert reveal.status_code == 200
        assert b"Save your recovery codes" in reveal.content
        # Second visit must not re-show plaintext.
        again = admin_client.get(reverse("security_recovery_codes_reveal"))
        assert again.status_code == 302

    def test_qrcode_svg(self, db, admin_client, admin_user):
        admin_client.get(reverse("security_totp_setup"))
        response = admin_client.get(reverse("security_totp_qrcode"))
        assert response.status_code == 200
        assert response["Content-Type"] == "image/svg+xml"
        assert b"<svg" in response.content.lower() or b"svg" in response.content.lower()

    def test_enrolled_stale_session_cannot_fetch_qr_secret(
        self, db, admin_no_otp_client
    ):
        """Confirmed device + password-only session must not read QR secret."""
        response = admin_no_otp_client.get(reverse("security_totp_qrcode"))
        assert response.status_code == 302
        assert "/staff/login/" in response.url
        response = admin_no_otp_client.get(reverse("security_totp_setup"))
        assert response.status_code == 302
        assert "/staff/login/" in response.url


class TestOTPLoginForm:
    def test_login_without_device_password_only(self, db, admin_user):
        admin_user.set_password("CorrectHorseBattery!")
        admin_user.save()
        client = Client()
        response = client.post(
            "/staff/login/",
            {
                "username": admin_user.get_username(),
                "password": "CorrectHorseBattery!",
                "next": "/staff/profiles/",
            },
        )
        assert response.status_code == 302
        # Land on MFA setup, not full staff HTML, until enrolled.
        follow = client.get("/staff/profiles/")
        assert follow.status_code == 302
        assert "/staff/account/two-factor/" in follow.url

    def test_login_with_device_requires_otp(self, db, admin_with_device):
        admin_with_device.set_password("CorrectHorseBattery!")
        admin_with_device.save()
        device = admin_with_device.totpdevice_set.get()
        client = Client()
        # Password only — must fail OTP clean
        response = client.post(
            "/staff/login/",
            {
                "username": admin_with_device.get_username(),
                "password": "CorrectHorseBattery!",
                "next": "/staff/profiles/",
            },
        )
        assert response.status_code == 200
        assert response.context["form"].errors

        response = client.post(
            "/staff/login/",
            {
                "username": admin_with_device.get_username(),
                "password": "CorrectHorseBattery!",
                "otp_token": _current_token(device),
                "next": "/staff/profiles/",
            },
        )
        assert response.status_code == 302
        assert client.session.get("otp_device_id") == device.persistent_id


class TestRecoveryCodes:
    def test_login_with_recovery_code(self, db, admin_with_device):
        admin_with_device.set_password("CorrectHorseBattery!")
        admin_with_device.save()
        plains = issue_recovery_codes(admin_with_device)
        code = plains[0]
        client = Client()
        response = client.post(
            "/staff/login/",
            {
                "username": admin_with_device.get_username(),
                "password": "CorrectHorseBattery!",
                "otp_token": code,
                "next": "/staff/profiles/",
            },
        )
        assert response.status_code == 302
        device = admin_with_device.totpdevice_set.get()
        assert client.session.get("otp_device_id") == device.persistent_id
        assert unused_recovery_code_count(admin_with_device) == 9
        assert AuditLog.objects.filter(action="mfa.recovery_used").exists()

        # Reuse must fail
        client2 = Client()
        response = client2.post(
            "/staff/login/",
            {
                "username": admin_with_device.get_username(),
                "password": "CorrectHorseBattery!",
                "otp_token": code,
                "next": "/staff/profiles/",
            },
        )
        assert response.status_code == 200
        assert response.context["form"].errors

    def test_login_accepts_normalized_recovery_code(self, db, admin_with_device):
        admin_with_device.set_password("CorrectHorseBattery!")
        admin_with_device.save()
        plains = issue_recovery_codes(admin_with_device)
        raw = normalize_recovery_code(plains[0]).lower()
        client = Client()
        response = client.post(
            "/staff/login/",
            {
                "username": admin_with_device.get_username(),
                "password": "CorrectHorseBattery!",
                "otp_token": raw,
                "next": "/staff/profiles/",
            },
        )
        assert response.status_code == 302

    def test_regenerate_invalidates_old_codes(self, db, admin_with_otp_client, admin_with_device):
        old = issue_recovery_codes(admin_with_device)
        device = admin_with_device.totpdevice_set.get()
        response = admin_with_otp_client.post(
            reverse("security_totp_regenerate"),
            {"otp_token": _current_token(device)},
        )
        assert response.status_code == 302
        assert reverse("security_recovery_codes_reveal") in response.url
        assert unused_recovery_code_count(admin_with_device) == 10
        # Old plaintext must not verify
        from apps.security.recovery import consume_recovery_code

        assert consume_recovery_code(admin_with_device, old[0]) is False

    def test_disable_clears_totp_and_codes(self, db, admin_with_otp_client, admin_with_device):
        issue_recovery_codes(admin_with_device)
        device = admin_with_device.totpdevice_set.get()
        response = admin_with_otp_client.post(
            reverse("security_totp_disable"),
            {"otp_token": _current_token(device)},
        )
        assert response.status_code == 302
        assert reverse("security_totp_setup") in response.url
        assert TOTPDevice.objects.filter(user=admin_with_device).count() == 0
        assert RecoveryCode.objects.filter(user=admin_with_device).count() == 0
        assert AuditLog.objects.filter(action="mfa.disabled").exists()
