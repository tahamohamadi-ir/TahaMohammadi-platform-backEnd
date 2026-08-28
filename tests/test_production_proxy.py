"""Production reverse-proxy and health-probe settings."""

import importlib


def _load_production_settings(monkeypatch):
    monkeypatch.setenv("ALLOWED_HOSTS", "tahamohamadi.ir, www.tahamohamadi.ir")
    monkeypatch.setenv("DJANGO_SECRET_KEY", "x" * 60)
    monkeypatch.setenv("POSTGRES_USER", "cms")
    monkeypatch.setenv("POSTGRES_PASSWORD", "cms")
    monkeypatch.delenv("WAGTAILADMIN_BASE_URL", raising=False)
    import config.settings.production as production

    return importlib.reload(production)


def test_production_strips_hosts_and_adds_loopback(monkeypatch):
    production = _load_production_settings(monkeypatch)
    assert "tahamohamadi.ir" in production.ALLOWED_HOSTS
    assert "www.tahamohamadi.ir" in production.ALLOWED_HOSTS
    assert "127.0.0.1" in production.ALLOWED_HOSTS
    assert "localhost" in production.ALLOWED_HOSTS


def test_production_trusts_caddy_forwarded_proto(monkeypatch):
    production = _load_production_settings(monkeypatch)
    assert production.SECURE_PROXY_SSL_HEADER == ("HTTP_X_FORWARDED_PROTO", "https")
    assert production.USE_X_FORWARDED_HOST is True
    assert production.SECURE_REDIRECT_EXEMPT == [r"^health/", r"^api/"]


def test_production_csrf_origins_exclude_loopback(monkeypatch):
    production = _load_production_settings(monkeypatch)
    assert "https://tahamohamadi.ir" in production.CSRF_TRUSTED_ORIGINS
    assert "https://www.tahamohamadi.ir" in production.CSRF_TRUSTED_ORIGINS
    assert "https://127.0.0.1" not in production.CSRF_TRUSTED_ORIGINS


def test_production_serves_static_via_whitenoise(monkeypatch):
    production = _load_production_settings(monkeypatch)
    assert production.MIDDLEWARE[0] == "django.middleware.security.SecurityMiddleware"
    assert production.MIDDLEWARE[1] == "whitenoise.middleware.WhiteNoiseMiddleware"


def test_argon2_hasher_library_is_installed():
    from django.contrib.auth.hashers import Argon2PasswordHasher

    encoded = Argon2PasswordHasher().encode("runtime-check-password", "saltsalt")
    assert encoded.startswith("argon2")
