"""E2E settings — file SQLite, fast hashing, rebuild hook off (Playwright lifecycle).

Fixture credentials live in ``scripts/seed_e2e_fixtures.py`` only. Never use
production secrets here.
"""

from .base import *  # noqa: F403
from .base import BASE_DIR  # noqa: F401

DEBUG = True
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "testserver"]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "e2e.sqlite3",
    }
}

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
REBUILD_TRIGGER_ENABLED = False
REBUILD_TRIGGER_SECRET = ""
# Enable bulk archive for Playwright ADM QA matrix (DEFER-0032).
FEATURE_ADMIN_BULK_ARCHIVE = True
