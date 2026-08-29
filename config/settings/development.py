"""Development settings — local control plane only, fake/sanitized data."""

import os
from urllib.parse import urlparse

from .base import *  # noqa: F403
from .base import BASE_DIR  # noqa: F401

DEBUG = True
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "testserver"]

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# Disposable PostgreSQL when DATABASE_URL is set (see docs/operations/LOCAL-DATABASE.md).
# Bare CLI / no .env falls back to isolated SQLite for quick checks and pytest isolation.
_database_url = os.environ.get("DATABASE_URL", "").strip()
if _database_url:
    _url = urlparse(_database_url)
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": _url.path.lstrip("/"),
            "USER": _url.username,
            "PASSWORD": _url.password,
            "HOST": _url.hostname,
            "PORT": _url.port or 5432,
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "dev.sqlite3",
        }
    }
