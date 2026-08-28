"""Seed deterministic admin + TOTP fixtures for Playwright lifecycle (DEFER-0026).

Values are test-only fixtures, not production credentials. The TOTP hex key is
the RFC 6238 example secret so Node and django-otp share the same code stream.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import django  # noqa: E402

# Force e2e settings so CI workflow env (config.settings.test / :memory:) cannot win.
os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings.e2e"
django.setup()

from django.contrib.auth import get_user_model  # noqa: E402
from django_otp.plugins.otp_totp.models import TOTPDevice  # noqa: E402

# Shared with apps/web/qa/e2e/fixtures/credentials.ts — test fixtures only.
E2E_EMAIL = "e2e@example.com"
E2E_PASSWORD = "e2e-pass-not-a-real-secret"
E2E_USERNAME = "e2e-admin"
# ASCII "12345678901234567890" as hex (RFC 6238 example key).
E2E_TOTP_KEY_HEX = "3132333435363738393031323334353637383930"


def main() -> None:
    User = get_user_model()
    user, created = User.objects.get_or_create(
        email=E2E_EMAIL,
        defaults={
            "username": E2E_USERNAME,
            "is_staff": True,
            "is_superuser": True,
        },
    )
    if not created:
        user.username = E2E_USERNAME
        user.is_staff = True
        user.is_superuser = True
    user.set_password(E2E_PASSWORD)
    user.save()

    device, _ = TOTPDevice.objects.get_or_create(
        user=user,
        name="e2e",
        defaults={"confirmed": True, "key": E2E_TOTP_KEY_HEX},
    )
    if device.key != E2E_TOTP_KEY_HEX or not device.confirmed:
        device.key = E2E_TOTP_KEY_HEX
        device.confirmed = True
        device.save(update_fields=["key", "confirmed"])

    print(f"e2e fixtures ready: email={E2E_EMAIL} totp_device={device.persistent_id}")


if __name__ == "__main__":
    main()
