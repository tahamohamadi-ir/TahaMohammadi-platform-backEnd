"""Local laptop settings — real Postgres selected via DATABASE_URL.

Default target is the legacy monorepo parity profile 127.0.0.1:15432
(project taha-local; compose file Infra/legacy-monorepo/cms/
docker-compose.local.yml, tracked BK-L0). Throwaway credentials only;
never used in production. For the new-platform Docker profile
(Back-End/docker-compose.dev.yml, host port 5433), export the
recommended DATABASE_URL from .env.example into the process
environment — see docs/operations/LOCAL-DATABASE.md. Nothing auto-loads
.env. Fallback when Docker is unavailable: config.settings.development
(sqlite).
"""

import os
from urllib.parse import urlparse

from .base import *  # noqa: F403
from .base import BASE_DIR  # noqa: F401

DEBUG = True

ALLOWED_HOSTS = ["localhost", "127.0.0.1", "testserver"]

_default_db = "postgres://taha:taha_local_only@127.0.0.1:15432/taha"
_url = urlparse(os.environ.get("DATABASE_URL", _default_db))

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

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# Local admin SPA dev server (apps/admin vite) may talk to this origin.
CSRF_TRUSTED_ORIGINS = [
    "http://localhost:4321",
    "http://127.0.0.1:4321",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]
