"""Env-gated admin feature flags (Task-list §14 S4) — default off.

Only declare flags that gate real, shipped surfaces. Do not add unused keys.
"""

from __future__ import annotations

import os

from django.conf import settings

# Canonical flag names exposed to the admin SPA via ``auth/me``.
KNOWN_FLAGS = ("admin_bulk_archive",)


def _env_truthy(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def is_feature_enabled(flag: str) -> bool:
    """Return whether ``flag`` is enabled (unknown names are always False)."""
    if flag == "admin_bulk_archive":
        configured = getattr(settings, "FEATURE_ADMIN_BULK_ARCHIVE", None)
        if configured is not None:
            return bool(configured)
        return _env_truthy("FEATURE_ADMIN_BULK_ARCHIVE", default=False)
    return False


def enabled_feature_flags() -> dict[str, bool]:
    """Map of known flags → enabled (always includes every known key)."""
    return {name: is_feature_enabled(name) for name in KNOWN_FLAGS}
