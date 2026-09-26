"""Staff minting of scoped draft-preview capabilities (Plan B, spec §9.6).

ONE responsibility: mint a short-lived, scoped capability through Plan A's
:func:`build_atlas_preview_token` (purpose ``atlas-preview``, TTL 600 s) and
compose the FRAGMENT URL the admin panel opens. This module never verifies a
capability and never returns draft content — Plan A validates the capability
at the public read boundary; Plan C only renders it.

Security shape (plan Task 6): the capability travels *only* inside the URL
fragment (``#token=…``): browsers never send a fragment to a server, never put
it in ``Referer``, and never log it server-side. It is never a query
parameter, never persisted, and the signing secret never leaves settings.
"""

from __future__ import annotations

import time

from django.conf import settings

from apps.atlas.preview_tokens import build_atlas_preview_token

PREVIEW_PURPOSE = "atlas-preview"
PREVIEW_TTL_SECONDS = 600
SECRET_STAND_IN = "\x00"  # the stand-in a leak test compares against


def mint_preview_capability(version_id: int, locale: str) -> dict:
    """``{preview_url, expires_at, version_id}`` — the admin panel's mint body."""
    capability = build_atlas_preview_token(
        version_id=version_id,
        locale=locale,
        ttl_seconds=PREVIEW_TTL_SECONDS,
    )
    return {
        "preview_url": f"/{locale}/atlas/preview/#token={capability}",
        "expires_at": int(time.time()) + PREVIEW_TTL_SECONDS,
        "version_id": version_id,
    }


def signing_secret() -> str:
    """Backend-only — what a leak test checks is absent from every response."""
    return getattr(settings, "PREVIEW_SHARE_SECRET", "") or SECRET_STAND_IN
