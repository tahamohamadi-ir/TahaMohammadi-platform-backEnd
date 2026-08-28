"""Rebuild trigger signing helpers (P3-08) — HMAC-SHA256 signed trigger.

The signed message is ``taha-rebuild:<timestamp>``. The token travels with its
timestamp so a caller can prove freshness without server-side state; validation
compares with ``hmac.compare_digest`` to stay constant-time.

When ``REBUILD_TRIGGER_ENABLED`` is true, ``invoke_static_rebuild`` starts
``infra/deploy/rebuild-web.sh`` in the background (Compose ``web`` image rebuild
after Caddy cutover to ``127.0.0.1:13080``). The CMS image does not ship that
host script; production enablement stays owner-gated (DEFER-0027).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import subprocess
import time
from pathlib import Path
from urllib.parse import urlencode

from django.conf import settings

MESSAGE_PREFIX = "taha-rebuild"
MAX_TRIGGER_AGE_SECONDS = 300
logger = logging.getLogger(__name__)


def _sign(secret: str, timestamp: int) -> str:
    message = f"{MESSAGE_PREFIX}:{timestamp}"
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def validate_rebuild_token(token: str, secret: str, timestamp: int) -> bool:
    """Return True when ``token`` matches the HMAC of ``taha-rebuild:<timestamp>``.

    Uses constant-time comparison. Callers must also enforce timestamp
    freshness; an empty secret always fails closed.
    """
    if not secret:
        return False
    return hmac.compare_digest(token, _sign(secret, timestamp))


def build_signed_rebuild_url(secret: str, base_url: str) -> str:
    """Return a rebuild-trigger URL carrying a fresh token and its timestamp."""
    timestamp = int(time.time())
    query = urlencode({"token": _sign(secret, timestamp), "timestamp": timestamp})
    return f"{base_url.rstrip('/')}/rebuild-trigger/?{query}"


def rebuild_script_path() -> Path:
    configured = getattr(settings, "REBUILD_SCRIPT_PATH", "") or ""
    if str(configured).strip():
        return Path(str(configured))
    here = Path(__file__).resolve()
    # Repo checkout: apps/cms/apps/rebuild/services.py → parents[4] is repo root.
    # CMS image copies apps/cms to /app, so parents[4] does not exist; fail closed.
    if len(here.parents) > 4:
        return here.parents[4] / "infra" / "deploy" / "rebuild-web.sh"
    return Path("/nonexistent-rebuild-web.sh")


def invoke_static_rebuild(*, enabled: bool | None = None) -> bool:
    """Start the loopback web rebuild script in the background.

    Returns True only when a process was started. Never raises to the caller
    (publish must succeed even if the hook cannot run). Default-disabled.
    """
    if enabled is None:
        enabled = bool(getattr(settings, "REBUILD_TRIGGER_ENABLED", False))
    if not enabled:
        return False
    script = rebuild_script_path()
    if not script.is_file():
        logger.warning("rebuild script missing: %s", script)
        return False
    try:
        subprocess.Popen(  # noqa: S603
            ["bash", str(script)],
            cwd=str(script.parent),
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        logger.exception("failed to start rebuild script")
        return False
    logger.info("rebuild script started: %s", script)
    return True
