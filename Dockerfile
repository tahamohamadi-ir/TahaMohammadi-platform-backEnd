# syntax=docker/dockerfile:1
#
# Standalone backend image (BACKEND-100). Logic rewritten from the legacy
# Infra/legacy-monorepo/cms/Dockerfile.cms for this repository layout: the app
# code and pyproject.toml/uv.lock live at the repo root — no apps/* paths, no
# monorepo build context.
#
# Secrets are never baked in: DJANGO_SECRET_KEY, POSTGRES_*, etc. come from the
# runtime environment (.env via compose). No media is copied into the image
# (excluded via .dockerignore).

FROM python:3.12-slim AS builder
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
# Same uv release the migrated lock file was built with in the legacy image.
COPY --from=ghcr.io/astral-sh/uv:0.8.5 /uv /uvx /bin/
WORKDIR /build
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev \
    && .venv/bin/python -c "import argon2, whitenoise, gunicorn, django, qrcode"

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    VIRTUAL_ENV=/app/.venv \
    DJANGO_SETTINGS_MODULE=config.settings.production
WORKDIR /app
COPY --from=builder /build/.venv ./.venv
COPY . .
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin app \
    && mkdir -p staticfiles media \
    && chown -R app:app /app
# Build-time-only dummy values for collectstatic; not secrets and never used at
# runtime. WhiteNoise (production middleware) needs a populated STATIC_ROOT.
RUN USER=app GROUP=app HOME=/home/app \
    DJANGO_SETTINGS_MODULE=config.settings.test \
    DJANGO_SECRET_KEY=build-time-only-no-secret \
    ALLOWED_HOSTS=localhost \
    python manage.py collectstatic --noinput

USER app
EXPOSE 8000
CMD ["python", "-m", "gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "config.wsgi:application"]
