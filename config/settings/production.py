"""Production settings — require env vars; never fall back to defaults.

Secrets are read from the environment only. The CMS runs behind Caddy on the
same host (HTTPS terminator); gunicorn speaks HTTP on the loopback publish.
"""

import os

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403
from .base import BASE_DIR  # noqa: F401
from .base import MIDDLEWARE as BASE_MIDDLEWARE

DEBUG = False

# Serve Django static files from gunicorn (Caddy proxies /static*).
_middleware = list(BASE_MIDDLEWARE)
if "whitenoise.middleware.WhiteNoiseMiddleware" not in _middleware:
    _middleware.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")
MIDDLEWARE = _middleware

ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get("ALLOWED_HOSTS", "").split(",")
    if host.strip()
]
if not ALLOWED_HOSTS:
    raise ImproperlyConfigured("ALLOWED_HOSTS is required in production")

# Container HEALTHCHECK and host-local smoke use Host: 127.0.0.1 — keep probes
# working without widening the public hostname set beyond loopback.
for _loopback in ("127.0.0.1", "localhost"):
    if _loopback not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(_loopback)

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    raise ImproperlyConfigured("DJANGO_SECRET_KEY environment variable is required")

_rebuild_enabled = os.environ.get("REBUILD_TRIGGER_ENABLED", "false").strip().lower()
REBUILD_TRIGGER_ENABLED = _rebuild_enabled in {"1", "true", "yes"}
REBUILD_TRIGGER_SECRET = os.environ.get("REBUILD_TRIGGER_SECRET", "")
REBUILD_SCRIPT_PATH = os.environ.get("REBUILD_SCRIPT_PATH", "").strip()
PREVIEW_SHARE_SECRET = os.environ.get("PREVIEW_SHARE_SECRET", "").strip()
_bulk_archive = os.environ.get("FEATURE_ADMIN_BULK_ARCHIVE", "false").strip().lower()
FEATURE_ADMIN_BULK_ARCHIVE = _bulk_archive in {"1", "true", "yes", "on"}

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "taha_cms"),
        "USER": os.environ.get("POSTGRES_USER"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD"),
        "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
    }
}
if not DATABASES["default"]["USER"] or not DATABASES["default"]["PASSWORD"]:
    raise ImproperlyConfigured("POSTGRES_USER and POSTGRES_PASSWORD are required in production")

# Caddy terminates TLS and proxies HTTP to gunicorn. Trust forwarded proto/host
# so secure cookies and CSRF origins stay correct for https://tahamohamadi.ir.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True
SECURE_SSL_REDIRECT = True
# In-container / host-loopback health probes and CMS_API_BASE builds are plain
# HTTP without Caddy. gunicorn binds 127.0.0.1 only; Caddy still sends
# X-Forwarded-Proto: https for public traffic so these paths stay secure.
SECURE_REDIRECT_EXEMPT = [r"^health/", r"^api/"]
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_BROWSER_XSS_FILTER = True
X_FRAME_OPTIONS = "DENY"
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_TRUSTED_ORIGINS = [
    f"https://{host}"
    for host in ALLOWED_HOSTS
    if host and host not in ("127.0.0.1", "localhost")
]

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {
            "()": "pythonjsonlogger.json.JsonFormatter",
            "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
            "rename_fields": {"asctime": "timestamp"},
        },
    },
    "handlers": {
        "console": {
            "level": "INFO",
            "class": "logging.StreamHandler",
            "formatter": "json",
        },
    },
    "loggers": {
        "django": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "django.security": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "django.request": {"handlers": ["console"], "level": "ERROR", "propagate": False},
    },
}
