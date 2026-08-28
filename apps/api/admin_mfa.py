"""SPA TOTP enrollment API — ``/api/v1/admin/auth/mfa/*`` (ADM-0).

Primary enrollment path for staff is the React Security page (``/admin/security``)
backed by these endpoints. Django HTML fallback lives under
``/staff/account/two-factor/``.
"""

from __future__ import annotations

from base64 import b32encode

from django_otp import login as otp_login
from django_otp import user_has_device
from django_otp.plugins.otp_totp.models import TOTPDevice
from ninja import Field, Router, Schema

from apps.api.admin_common import (
    AdminError,
    _check_csrf,
    _client_ip,
    _require_admin_otp,
    _require_staff_session,
)
from apps.security.recovery import (
    consume_recovery_code,
    issue_recovery_codes,
    pop_recovery_codes,
    purge_mfa_state,
    unused_recovery_code_count,
)

DEVICE_NAME = "default"

mfa_router = Router()


class MfaStatusOut(Schema):
    enrolled: bool
    otpVerified: bool
    unusedRecoveryCodes: int
    configUrl: str | None = None
    manualSecret: str | None = None


class MfaTokenIn(Schema):
    otpToken: str = Field(min_length=6, max_length=40)


class RecoveryCodesOut(Schema):
    codes: list[str]


def _pending_device(user):
    return (
        TOTPDevice.objects.filter(user=user, confirmed=False)
        .order_by("-id")
        .first()
    )


def _ensure_pending_device(user):
    device = _pending_device(user)
    if device is None:
        TOTPDevice.objects.filter(user=user).delete()
        device = TOTPDevice.objects.create(
            user=user,
            name=DEVICE_NAME,
            confirmed=False,
        )
    return device


def _verify_second_factor(user, token: str, *, ip: str) -> bool:
    cleaned = (token or "").strip()
    device = TOTPDevice.objects.filter(user=user, confirmed=True).first()
    if device is not None and device.verify_token(cleaned):
        return True
    return consume_recovery_code(user, cleaned, ip=ip)


@mfa_router.get("/status", response=MfaStatusOut, summary="TOTP enrollment status.")
def mfa_status(request):
    _require_staff_session(request)
    enrolled = user_has_device(request.user, confirmed=True)
    otp_verified = getattr(request.user, "otp_device", None) is not None
    config_url = None
    manual_secret = None
    if not enrolled:
        device = _ensure_pending_device(request.user)
        config_url = device.config_url
        manual_secret = b32encode(device.bin_key).decode("ascii")
    return MfaStatusOut(
        enrolled=enrolled,
        otpVerified=otp_verified,
        unusedRecoveryCodes=unused_recovery_code_count(request.user),
        configUrl=config_url,
        manualSecret=manual_secret,
    )


@mfa_router.post("/confirm", summary="Confirm a pending TOTP device.")
def mfa_confirm(request, payload: MfaTokenIn):
    _require_staff_session(request)
    _check_csrf(request)
    if user_has_device(request.user, confirmed=True):
        raise AdminError(400, "VALIDATION", "Two-factor authentication is already enabled.")
    device = _pending_device(request.user)
    if device is None:
        raise AdminError(400, "VALIDATION", "No pending authenticator device.")
    if not device.verify_token(payload.otpToken.strip()):
        raise AdminError(400, "VALIDATION", "Invalid code. Wait for a new code and try again.")
    device.confirmed = True
    device.save(update_fields=["confirmed"])
    request.session.cycle_key()
    otp_login(request, device)
    codes = issue_recovery_codes(request.user, ip=_client_ip(request))
    return {"ok": True, "codes": codes}


@mfa_router.post("/regenerate", response=RecoveryCodesOut, summary="Replace recovery codes.")
def mfa_regenerate(request, payload: MfaTokenIn):
    _require_admin_otp(request)
    _check_csrf(request)
    if not _verify_second_factor(request.user, payload.otpToken, ip=_client_ip(request)):
        raise AdminError(400, "VALIDATION", "Invalid authentication or recovery code.")
    codes = issue_recovery_codes(request.user, ip=_client_ip(request))
    return RecoveryCodesOut(codes=codes)


@mfa_router.post("/disable", summary="Disable TOTP and recovery codes.")
def mfa_disable(request, payload: MfaTokenIn):
    _require_admin_otp(request)
    _check_csrf(request)
    if not _verify_second_factor(request.user, payload.otpToken, ip=_client_ip(request)):
        raise AdminError(400, "VALIDATION", "Invalid authentication or recovery code.")
    purge_mfa_state(request.user, ip=_client_ip(request))
    request.session.cycle_key()
    return {"ok": True}


@mfa_router.get("/recovery-codes", response=RecoveryCodesOut, summary="Pop one-time stashed codes.")
def mfa_recovery_peek(request):
    _require_admin_otp(request)
    codes = pop_recovery_codes(request.session)
    return RecoveryCodesOut(codes=list(codes or []))
