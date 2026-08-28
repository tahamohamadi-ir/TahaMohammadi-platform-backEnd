"""Stateless HMAC preview share tokens (DEFER-0016 / ADM-4).

Signed message: ``preview:{kind}:{pk}:{exp}``. Tokens expire after
``PREVIEW_SHARE_TTL_SECONDS`` (default 15 minutes). No server-side store.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass
from enum import Enum

from django.conf import settings

MESSAGE_PREFIX = "preview"
DEFAULT_TTL_SECONDS = 900


class PreviewTokenStatus(str, Enum):
    VALID = "valid"
    EXPIRED = "expired"
    INVALID = "invalid"


@dataclass(frozen=True)
class PreviewTokenPayload:
    kind: str
    pk: int
    exp: int


def _preview_secret() -> str:
    configured = getattr(settings, "PREVIEW_SHARE_SECRET", "") or ""
    if configured:
        return configured
    return getattr(settings, "SECRET_KEY", "") or ""


def preview_ttl_seconds() -> int:
    return int(getattr(settings, "PREVIEW_SHARE_TTL_SECONDS", DEFAULT_TTL_SECONDS))


def _sign(secret: str, kind: str, pk: int, exp: int) -> str:
    message = f"{MESSAGE_PREFIX}:{kind}:{pk}:{exp}"
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def build_preview_token(kind: str, pk: int, *, ttl_seconds: int | None = None) -> str:
    """Return a URL-safe token string ``kind.pk.exp.signature``."""
    secret = _preview_secret()
    if not secret:
        raise ValueError("PREVIEW_SHARE_SECRET or SECRET_KEY is required")
    ttl = preview_ttl_seconds() if ttl_seconds is None else ttl_seconds
    exp = int(time.time()) + ttl
    signature = _sign(secret, kind, pk, exp)
    return f"{kind}.{pk}.{exp}.{signature}"


def build_preview_share_path(kind: str, pk: int, *, ttl_seconds: int | None = None) -> str:
    token = build_preview_token(kind, pk, ttl_seconds=ttl_seconds)
    return f"/preview/share/{token}/"


def parse_preview_token(token: str) -> tuple[PreviewTokenStatus, PreviewTokenPayload | None]:
    """Validate ``token`` and return status + payload when valid."""
    secret = _preview_secret()
    if not secret or not token:
        return PreviewTokenStatus.INVALID, None
    parts = token.strip("/").split(".")
    if len(parts) != 4:
        return PreviewTokenStatus.INVALID, None
    kind, pk_raw, exp_raw, signature = parts
    if not kind or not pk_raw.isdigit() or not exp_raw.isdigit() or not signature:
        return PreviewTokenStatus.INVALID, None
    pk = int(pk_raw)
    exp = int(exp_raw)
    if not hmac.compare_digest(signature, _sign(secret, kind, pk, exp)):
        return PreviewTokenStatus.INVALID, None
    if exp < int(time.time()):
        return PreviewTokenStatus.EXPIRED, PreviewTokenPayload(kind=kind, pk=pk, exp=exp)
    return PreviewTokenStatus.VALID, PreviewTokenPayload(kind=kind, pk=pk, exp=exp)
