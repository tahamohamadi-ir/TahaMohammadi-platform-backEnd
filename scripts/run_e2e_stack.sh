#!/usr/bin/env bash
# Start a disposable CMS stack for Playwright lifecycle (admin SPA + Ninja API).
# Requires: uv, built apps/admin/dist (ADR-0032: SPA lives in its own project), bash.
set -euo pipefail

CMS_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ADMIN_DIST="$CMS_ROOT/../admin/dist"
cd "$CMS_ROOT"

# Always use e2e settings (file SQLite). Do not inherit CI's config.settings.test
# (:memory: DB) — migrate and seed are separate processes and would not share state.
export DJANGO_SETTINGS_MODULE="config.settings.e2e"

# ADR-0032: point Django's admin_spa.py at the moved SPA build.
if [[ ! -f "$ADMIN_DIST/index.html" ]]; then
  echo "Admin SPA missing — run: (cd apps/admin && npm ci && npm run build)" >&2
  exit 1
fi
export ADMIN_SPA_ROOT="$ADMIN_DIST"

rm -f e2e.sqlite3
uv run python manage.py migrate --noinput
uv run python scripts/seed_e2e_fixtures.py
exec uv run python manage.py runserver 127.0.0.1:8000 --noreload
