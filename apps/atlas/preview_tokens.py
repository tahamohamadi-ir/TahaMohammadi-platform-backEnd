"""Atlas draft-preview capabilities — one mint/verify primitive (spec §10.10.1).

An Atlas preview capability is a short-lived, read-only, purpose-bound,
version- and locale-scoped HMAC token: ``atlas-preview.<version_id>.<exp>.<sig>``
over the message ``preview:atlas-preview:<version_id>:<locale>:<exp>``, signed
with the existing content-preview secret handling
(``PREVIEW_SHARE_SECRET`` falling back to ``SECRET_KEY``, re-imported from
:mod:`apps.content.preview_token` — one secret, one place).

Rules this module exists to enforce once, in one place:

* **One primitive.** Plan B task 6 mints through :func:`build_atlas_preview_token`
  and Plan A's endpoint verifies through :func:`parse_atlas_preview_token`; no
  other module mints or parses an Atlas capability.
* **Purpose-bound.** ``atlas-preview`` is inside the signed message, so a token
  minted for a content-entity preview (kind ``hero``/``landing``/…, a different
  message shape) can never verify as an Atlas capability, and vice versa — the
  recalculation crosses message shapes and the comparison fails.
* **Locale-scoped.** The locale is inside the signed message: swapping a
  token's locale segment produces a different message and a bad signature.
* **Secret stays backend-only.** The secret is read from Django settings at
  minting/verification time, exactly like the existing preview share; nothing
  here exports it and nothing here renders it into any payload.
* **No TTL from the request.** The TTL is chosen at minting time (default 600 s
  — spec §10.10's ten minutes) and the expiry *inside the signed message* is
  authoritative afterwards; verification takes no TTL argument.
* **Verify-only here.** No ORM access: the endpoint checks that the referenced
  version exists (a draft is the normal case) and that the requested
  ``?locale=`` equals the token's scope.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass

from apps.content.preview_token import _preview_secret

__all__ = [
    "ATLAS_PREVIEW_PURPOSE",
    "AtlasPreviewCapability",
    "build_atlas_preview_token",
    "parse_atlas_preview_token",
]

#: The purpose segment — the wire name both the mint (Plan B) and the serve
#: (Plan A) paths carry inside the signed message.
ATLAS_PREVIEW_PURPOSE = "atlas-preview"

#: The message prefix, shared with the existing content preview tokens' scheme
#: (``preview:<kind>:<pk>:<exp>``). The Atlas capability extends that scheme
#: with a purpose **and** a locale segment, so the two token families share the
#: secret but never share a message shape — a content token cannot be replayed
#: as an Atlas capability and vice versa.
MESSAGE_PREFIX = "preview"

#: Default TTL (spec §10.10: short-lived, ten minutes). Overridable only at
#: minting time.
DEFAULT_TTL_SECONDS = 600


@dataclass(frozen=True)
class AtlasPreviewCapability:
    """What a verified Atlas preview token asserts about its reader's access."""

    version_id: int
    locale: str
    purpose: str
    exp: int


def _sign(secret: str, version_id: int, locale: str, exp: int) -> str:
    """The one HMAC over the Atlas message shape (purpose + locale included)."""
    message = f"{MESSAGE_PREFIX}:{ATLAS_PREVIEW_PURPOSE}:{version_id}:{locale}:{exp}"
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def build_atlas_preview_token(
    version_id: int, locale: str, *, ttl_seconds: int = DEFAULT_TTL_SECONDS
) -> str:
    """Mint one ``atlas-preview.<version_id>.<exp>.<signature>`` capability.

    The signature covers ``preview:atlas-preview:<version_id>:<locale>:<exp>``:
    neither the version, the locale nor the expiry can be swapped without
    breaking it. Verification takes *no* TTL argument — the expiry inside the
    signed message is authoritative.
    """
    secret = _preview_secret()
    if not secret:
        raise ValueError("PREVIEW_SHARE_SECRET or SECRET_KEY is required")
    exp = int(time.time()) + int(ttl_seconds)
    signature = _sign(secret, version_id, locale, exp)
    return f"{ATLAS_PREVIEW_PURPOSE}.{version_id}.{locale}.{exp}.{signature}"


def parse_atlas_preview_token(
    token: str, *, purpose: str = ATLAS_PREVIEW_PURPOSE
) -> AtlasPreviewCapability | None:
    """Verify one Atlas preview token (spec §10.10.1's validation order).

    ``None`` means "nothing verified": absent, structurally wrong, failed
    signature verification, or not our HMAC at all. Forging routes land here,
    because each segment value is replayed verbatim into the signed message and
    an altered one fails the signature comparison.

    A token **signed by us** — this secret, this five-segment shape — parses to a
    capability even when its ``purpose`` is not ``atlas-preview`` (e.g. the
    existing ``article.<pk>.<exp>.<sig>`` share token), because the endpoint
    needs to tell "verified but out of scope" (spec §10.10.1 → ``403``) apart
    from "garbage" (``401``). The caller checks ``purpose``, locale and version
    existence and stays inside the matrix; this primitive only decides what is
    cryptographically ours.

    An **expired** token is also *not* ``None``: the capability has verified, so
    the caller answers ``403`` for expired / out-of-scope and ``401`` for
    "nothing verified at all".
    """
    if not token:
        return None
    segments = token.strip("/").split(".")
    if len(segments) != 5:
        return None
    purpose, version_raw, locale, exp_raw, signature = segments
    if not version_raw.isdigit() or not exp_raw.isdigit() or not locale:
        return None
    secret = _preview_secret()
    if not secret:
        return None
    version_id = int(version_raw)
    exp = int(exp_raw)
    message = f"{MESSAGE_PREFIX}:{purpose}:{version_raw}:{locale}:{exp_raw}"
    expected = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    return AtlasPreviewCapability(version_id=version_id, locale=locale, purpose=purpose, exp=exp)
