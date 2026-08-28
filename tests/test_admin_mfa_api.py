"""SPA TOTP enrollment API tests (ADM-0)."""

import json
import time

import pytest
from django.test import Client
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.security.recovery import unused_recovery_code_count


@pytest.fixture
def staff_client(db, admin_user):
    client = Client(enforce_csrf_checks=True)
    client.force_login(admin_user)
    token = client.get("/api/v1/admin/auth/csrf").json()["csrfToken"]
    client.defaults["HTTP_X_CSRFTOKEN"] = token
    return client


def _totp_token(device) -> str:
    from django_otp.oath import TOTP

    totp = TOTP(device.bin_key, device.step, device.t0, device.digits, device.drift)
    totp.time = time.time()
    return str(totp.token()).zfill(device.digits)


def test_mfa_status_creates_pending_device(staff_client, admin_user):
    assert TOTPDevice.objects.filter(user=admin_user).count() == 0
    response = staff_client.get("/api/v1/admin/auth/mfa/status")
    assert response.status_code == 200
    body = response.json()
    assert body["enrolled"] is False
    assert body["manualSecret"]
    assert TOTPDevice.objects.get(user=admin_user).confirmed is False


def test_mfa_confirm_enrolls_and_returns_recovery_codes(staff_client, admin_user):
    staff_client.get("/api/v1/admin/auth/mfa/status")
    device = TOTPDevice.objects.get(user=admin_user, confirmed=False)
    response = staff_client.post(
        "/api/v1/admin/auth/mfa/confirm",
        data=json.dumps({"otpToken": _totp_token(device)}),
        content_type="application/json",
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert len(body["codes"]) == 10
    device.refresh_from_db()
    assert device.confirmed is True
    assert unused_recovery_code_count(admin_user) == 10


def test_mfa_disable_clears_device(staff_client, admin_user):
    staff_client.get("/api/v1/admin/auth/mfa/status")
    device = TOTPDevice.objects.get(user=admin_user, confirmed=False)
    confirm = staff_client.post(
        "/api/v1/admin/auth/mfa/confirm",
        data=json.dumps({"otpToken": _totp_token(device)}),
        content_type="application/json",
    )
    assert confirm.status_code == 200
    recovery = confirm.json()["codes"][0]
    disable = staff_client.post(
        "/api/v1/admin/auth/mfa/disable",
        data=json.dumps({"otpToken": recovery}),
        content_type="application/json",
    )
    assert disable.status_code == 200
    assert TOTPDevice.objects.filter(user=admin_user).count() == 0
    status = staff_client.get("/api/v1/admin/auth/mfa/status")
    assert status.json()["enrolled"] is False
