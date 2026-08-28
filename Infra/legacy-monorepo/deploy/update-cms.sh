#!/usr/bin/env bash
# Pull (or build) a versioned CMS image and recreate the Compose stack.
#
# Preferred (GHCR, public or authenticated):
#   export CMS_IMAGE=ghcr.io/tahamohamadi-ir/taha-cms:main
#   bash infra/deploy/update-cms.sh
#
# Pin for production:
#   export CMS_IMAGE=ghcr.io/tahamohamadi-ir/taha-cms:573db28
#   bash infra/deploy/update-cms.sh
#
# Offline / registry auth failure — build on the VPS:
#   export CMS_IMAGE=taha-cms:local CMS_BUILD=1
#   bash infra/deploy/update-cms.sh
#
# Optional env:
#   CMS_REPO_DIR     default /home/deploy/cms-repo
#   CMS_PULL_POLICY  default always (ignored when CMS_BUILD=1)
#
# Does NOT apply Caddy, create superusers, or print secrets.

set -euo pipefail

CMS_REPO_DIR="${CMS_REPO_DIR:-/home/deploy/cms-repo}"
COMPOSE_FILE="infra/cms/docker-compose.cms.yml"
CMS_BUILD="${CMS_BUILD:-0}"
# Local builds must never hit a registry for the app image.
if [[ "$CMS_BUILD" == "1" ]]; then
  CMS_PULL_POLICY="${CMS_PULL_POLICY:-never}"
else
  CMS_PULL_POLICY="${CMS_PULL_POLICY:-always}"
fi

if [[ -z "${CMS_IMAGE:-}" ]]; then
  echo "CMS_IMAGE is required." >&2
  echo "Example: export CMS_IMAGE=ghcr.io/tahamohamadi-ir/taha-cms:main" >&2
  echo "Or build locally: export CMS_IMAGE=taha-cms:local CMS_BUILD=1" >&2
  exit 1
fi

# Compose is fail-closed (LOG-0238: ${WEB_IMAGE:?}). This script updates the CMS
# service only — derive the current web image from the running container so the
# compose interpolation succeeds without touching the web artifact.
if [[ -z "${WEB_IMAGE:-}" ]]; then
  WEB_IMAGE="$(docker inspect taha-cms-web-1 --format '{{.Config.Image}}' 2>/dev/null || true)"
  if [[ -n "$WEB_IMAGE" ]]; then
    export WEB_IMAGE
    echo "Using WEB_IMAGE=${WEB_IMAGE} (derived from running taha-cms-web-1)"
  fi
fi

cd "$CMS_REPO_DIR"

if [[ ! -f infra/cms/.env ]]; then
  echo "missing infra/cms/.env — copy from infra/cms/.env.example and set secrets" >&2
  exit 1
fi

# Ensure required production keys exist (do not overwrite existing values).
ensure_env_key() {
  local key="$1"
  local value="$2"
  if ! grep -qE "^${key}=" infra/cms/.env; then
    printf '%s=%s\n' "$key" "$value" >> infra/cms/.env
    echo "appended missing ${key} to infra/cms/.env"
  fi
}
ensure_env_key DJANGO_SETTINGS_MODULE config.settings.production
ensure_env_key POSTGRES_HOST db

echo "Using CMS_IMAGE=${CMS_IMAGE} CMS_BUILD=${CMS_BUILD}"
export CMS_IMAGE CMS_PULL_POLICY

if [[ "$CMS_BUILD" == "1" ]]; then
  docker compose -f "$COMPOSE_FILE" build cms
else
  if ! docker compose -f "$COMPOSE_FILE" pull cms; then
    echo "GHCR pull failed. Re-run with CMS_BUILD=1 to build on this host, or:" >&2
    echo "  echo YOUR_GITHUB_PAT | docker login ghcr.io -u tahamohamadi-ir --password-stdin" >&2
    echo "PAT needs read:packages (GitHub password will NOT work)." >&2
    exit 1
  fi
fi

# Leftover first-bring-up project name was `cms` (cms-cms-1 / cms-db-1).
# Those must not keep an extra Postgres or a stale 18000 bind.
echo "Removing leftover cms-* containers from the previous compose project name..."
docker rm -f cms-cms-1 cms-db-1 2>/dev/null || true
if ss -lnt 2>/dev/null | grep -q ':18000 ' || netstat -lnt 2>/dev/null | grep -q ':18000 '; then
  echo "Port 127.0.0.1:18000 is in use — stopping binders..."
  docker ps --format '{{.ID}} {{.Names}} {{.Ports}}' | awk '/18000->/ {print $1}' | while read -r cid; do
    [[ -n "$cid" ]] || continue
    docker stop "$cid" >/dev/null || true
    docker rm "$cid" >/dev/null || true
  done
fi

# Force recreate so a container that failed port-bind is not reused without DNS.
docker compose -f "$COMPOSE_FILE" up -d --force-recreate --remove-orphans --pull never

echo "Waiting for cms to resolve db and report db=ok..."
ready=0
for _ in $(seq 1 45); do
  if docker compose -f "$COMPOSE_FILE" exec -T cms python -c \
    "import json,socket,urllib.request; socket.getaddrinfo('db', 5432); d=json.load(urllib.request.urlopen('http://127.0.0.1:8000/health/', timeout=3)); raise SystemExit(0 if d.get('db')=='ok' else 1)" \
    >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 2
done
if [[ "$ready" != "1" ]]; then
  echo "cms never resolved db / health db=ok. Networks and logs:" >&2
  docker inspect taha-cms-cms-1 --format 'cms networks={{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' >&2 || true
  docker inspect taha-cms-db-1 --format 'db networks={{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' >&2 || true
  docker compose -f "$COMPOSE_FILE" logs cms --tail=80 >&2 || true
  docker compose -f "$COMPOSE_FILE" logs db --tail=40 >&2 || true
  exit 1
fi

docker compose -f "$COMPOSE_FILE" exec -T cms python manage.py migrate --noinput
docker compose -f "$COMPOSE_FILE" exec -T cms python -c "import argon2, whitenoise, qrcode; print('runtime-deps-ok')"
docker compose -f "$COMPOSE_FILE" ps

echo "Loopback health:"
curl -fsS "http://127.0.0.1:18000/health/"
echo
echo "Loopback admin login (forwarded proto, as Caddy will send):"
admin_body="$(mktemp -t cms-admin-login.XXXXXX)"
trap 'rm -f "$admin_body"' EXIT
admin_code="$(curl -sS -o "$admin_body" -w "%{http_code}" \
  -H "Host: tahamohamadi.ir" \
  -H "X-Forwarded-Proto: https" \
  "http://127.0.0.1:18000/admin/login/")"
if [[ "$admin_code" != "200" ]]; then
  echo "loopback /admin/login/ expected 200 got ${admin_code}" >&2
  exit 1
fi
echo "PASS /admin/login/ ${admin_code}"
echo
echo "CMS stack updated."
echo "Merge infra/cms/Caddyfile.cms.snippet into the Caddy site block (before file_server), then:"
echo "  bash infra/deploy/smoke-cms.sh https://tahamohamadi.ir"
echo "Create superuser (owner interactive; password must be at least 12 characters):"
echo "  docker compose -f ${COMPOSE_FILE} exec cms python manage.py createsuperuser"

