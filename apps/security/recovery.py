"""Hashed one-time MFA recovery codes (DEFER-0015)."""

from __future__ import annotations

import secrets

from django.contrib.auth.hashers import check_password, make_password
from django.db import transaction
from django.utils import timezone

from apps.security.models import AuditLog, RecoveryCode

RECOVERY_CODE_COUNT = 10
SESSION_RECOVERY_CODES_KEY = "mfa_recovery_codes_once"


def normalize_recovery_code(raw: str) -> str:
    """Strip separators/spaces; compare case-insensitively as A-Z0-9 only."""
    return "".join(ch for ch in (raw or "").strip().upper() if ch.isalnum())


def _format_display(raw16: str) -> str:
    return f"{raw16[:4]}-{raw16[4:8]}-{raw16[8:12]}-{raw16[12:]}"


def generate_plain_recovery_codes(count: int = RECOVERY_CODE_COUNT) -> list[str]:
    codes: list[str] = []
    seen: set[str] = set()
    while len(codes) < count:
        raw = secrets.token_hex(8).upper()
        if raw in seen:
            continue
        seen.add(raw)
        codes.append(_format_display(raw))
    return codes


def unused_recovery_code_count(user) -> int:
    return RecoveryCode.objects.filter(user=user, used_at__isnull=True).count()


def issue_recovery_codes(
    user, *, count: int = RECOVERY_CODE_COUNT, ip: str = ""
) -> list[str]:
    """Replace unused codes; return plaintext once. Never log plaintext."""
    plains = generate_plain_recovery_codes(count)
    with transaction.atomic():
        RecoveryCode.objects.filter(user=user, used_at__isnull=True).delete()
        RecoveryCode.objects.bulk_create(
            [
                RecoveryCode(
                    user=user,
                    code_hash=make_password(normalize_recovery_code(plain)),
                )
                for plain in plains
            ]
        )
        AuditLog.objects.create(
            user=user,
            action="mfa.recovery_issued",
            model_name="recoverycode",
            object_id=str(user.pk),
            ip=(ip or "")[:45],
            detail=f"issued={count}",
        )
    return plains


def consume_recovery_code(user, raw: str, *, ip: str = "") -> bool:
    """Mark a matching unused code used. Work scales with unused code count."""
    norm = normalize_recovery_code(raw)
    if len(norm) < 16:
        return False
    with transaction.atomic():
        candidates = list(
            RecoveryCode.objects.select_for_update().filter(
                user=user, used_at__isnull=True
            )
        )
        for row in candidates:
            if check_password(norm, row.code_hash):
                row.used_at = timezone.now()
                row.save(update_fields=["used_at"])
                AuditLog.objects.create(
                    user=user,
                    action="mfa.recovery_used",
                    model_name="recoverycode",
                    object_id=str(row.pk),
                    ip=(ip or "")[:45],
                    detail="consumed",
                )
                return True
    return False


def purge_mfa_state(user, *, ip: str = "") -> None:
    """Delete TOTP devices and recovery codes; audit disable."""
    from django_otp.plugins.otp_totp.models import TOTPDevice

    with transaction.atomic():
        TOTPDevice.objects.filter(user=user).delete()
        RecoveryCode.objects.filter(user=user).delete()
        AuditLog.objects.create(
            user=user,
            action="mfa.disabled",
            model_name="totpdevice",
            object_id=str(user.pk),
            ip=(ip or "")[:45],
            detail="devices_and_recovery_purged",
        )


def stash_recovery_codes(session, codes: list[str]) -> None:
    session[SESSION_RECOVERY_CODES_KEY] = list(codes)
    session.modified = True


def pop_recovery_codes(session) -> list[str] | None:
    codes = session.pop(SESSION_RECOVERY_CODES_KEY, None)
    session.modified = True
    if not codes:
        return None
    return list(codes)
