#!/usr/bin/env bash
# Reload Compose-edge Caddy after Caddyfile.compose changes (DEFER-0031 / Slice 4).
#
# Usage (from repo root on the VPS, as deploy with docker rights):
#   bash infra/deploy/caddy-compose-reload.sh
#
# Prerequisites:
#   - Host systemd Caddy is stopped (ports 80/443 free)
#   - Compose profile `edge` caddy service is (or will be) running
#   - This script does NOT stop/start host Caddy
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
COMPOSE_FILE="${COMPOSE_FILE:-$ROOT/infra/cms/docker-compose.cms.yml}"
CADDYFILE="${CADDYFILE:-$ROOT/infra/caddy/Caddyfile.compose}"

if [ ! -f "$COMPOSE_FILE" ]; then
  echo "error: compose file not found: $COMPOSE_FILE" >&2
  exit 1
fi

if [ ! -f "$CADDYFILE" ]; then
  echo "error: Caddyfile.compose not found: $CADDYFILE" >&2
  exit 1
fi

# Guard for legacy /var/www/html bind (LOG-0216): host path may be absent after
# old taha-prod-* removal; Docker would create empty dir -> Caddy 404 for /fonts.
# With `create_host_path: false` in compose, missing path fails fast; ensure it exists.
if [ ! -d /var/www/html ]; then
  echo "warning: /var/www/html missing — creating guard dir for legacy /fonts" >&2
  sudo mkdir -p /var/www/html 2>/dev/null || mkdir -p /var/www/html 2>/dev/null || true
fi

# Compose is fail-closed (LOG-0238: ${CMS_IMAGE:?}/${WEB_IMAGE:?}). A caddy reload
# does not change app images — derive current ones from the running containers so
# `up -d caddy` validates without requiring deploy-time env.
if [ -z "${CMS_IMAGE:-}" ]; then
  CMS_IMAGE="$(docker inspect taha-cms-cms-1 --format '{{.Config.Image}}' 2>/dev/null || true)"
  [ -n "$CMS_IMAGE" ] && export CMS_IMAGE || unset CMS_IMAGE
fi
if [ -z "${WEB_IMAGE:-}" ]; then
  WEB_IMAGE="$(docker inspect taha-cms-web-1 --format '{{.Config.Image}}' 2>/dev/null || true)"
  [ -n "$WEB_IMAGE" ] && export WEB_IMAGE || unset WEB_IMAGE
fi

cd "$(dirname "$COMPOSE_FILE")"

# Recreate so bind-mounted Caddyfile is re-read; validate inside the container.
docker compose -f "$COMPOSE_FILE" --profile edge up -d caddy

# Best-effort config check (container must be up).
if docker compose -f "$COMPOSE_FILE" --profile edge exec -T caddy \
  caddy validate --config /etc/caddy/Caddyfile 2>&1; then
  docker compose -f "$COMPOSE_FILE" --profile edge exec -T caddy \
    caddy reload --config /etc/caddy/Caddyfile 2>&1 || true
  echo "caddy-compose: up -d + validate OK"
else
  echo "error: caddy validate failed inside container" >&2
  exit 1
fi
