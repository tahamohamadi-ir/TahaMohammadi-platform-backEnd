#!/usr/bin/env bash
# Rebuild the public web nginx container after CMS publish (ADR-0027 Slice 1+).
#
# Builds apps/web inside the Docker image with live CMS content, then restarts
# the `web` service so Caddy (when cut over to 127.0.0.1:13080) serves fresh HTML.
#
# Usage (operator on VPS as deploy user, from repo root):
#   cd /home/deploy/cms-repo
#   git pull --ff-only origin main
#   bash infra/deploy/rebuild-web.sh
#
# Requires: Docker access (deploy user in `docker` group), CMS healthy on loopback.
# Does NOT apply Caddy, migrate CMS, or switch /opt/taha/site/current.
#
# Env:
#   CMS_REPO_DIR       default /home/deploy/cms-repo (or repo root when script lives there)
#   CMS_API_BASE       default http://127.0.0.1:18000 (loopback preflight on host;
#                      Docker build auto-uses https://tahamohamadi.ir when loopback)
#   WEB_IMAGE          default taha-web:local (local rebuild tag)
#   WEB_PULL_POLICY    default never (do not pull GHCR over a fresh local build)
#   SKIP_PUBLIC_SMOKE  set to 1 to skip https://tahamohamadi.ir checks
#   WEB_BUILD_NO_CACHE set to 1 to force a clean Docker build
#
# After Caddy web cutover (reverse_proxy 127.0.0.1:13080), this replaces
# rebuild-static.sh for public HTML. During transition, run both or keep using
# rebuild-static.sh until Caddy stops serving /opt/taha/site/current.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_FROM_SCRIPT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
CMS_REPO_DIR="${CMS_REPO_DIR:-${REPO_FROM_SCRIPT}}"
COMPOSE_FILE="infra/cms/docker-compose.cms.yml"
CMS_API_BASE="${CMS_API_BASE:-http://127.0.0.1:18000}"
WEB_IMAGE="${WEB_IMAGE:-taha-web:local}"
WEB_PULL_POLICY="${WEB_PULL_POLICY:-never}"
SKIP_PUBLIC_SMOKE="${SKIP_PUBLIC_SMOKE:-0}"

cd "$CMS_REPO_DIR"

if [[ ! -f "$COMPOSE_FILE" ]]; then
  echo "error: missing ${COMPOSE_FILE} under ${CMS_REPO_DIR}" >&2
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "error: docker not found — deploy user needs docker group access" >&2
  exit 1
fi

echo "==> [rebuild-web] preflight CMS at ${CMS_API_BASE}"
if ! curl -fsS "${CMS_API_BASE%/}/health/" | grep -q '"db"'; then
  echo "error: CMS not healthy at ${CMS_API_BASE}/health/" >&2
  exit 1
fi
echo "CMS health OK"

export WEB_IMAGE WEB_PULL_POLICY CMS_API_BASE

# Loopback preflight runs on the VPS host, but `docker compose build` executes
# npm run build inside an isolated build container where 127.0.0.1 is not the
# host CMS. Match CI (ci-web-image.yml) and use the public origin for Docker.
resolve_docker_build_cms_api_base() {
  case "${CMS_API_BASE}" in
    http://127.0.0.1:*|http://localhost:*)
      echo "https://tahamohamadi.ir"
      ;;
    *)
      echo "${CMS_API_BASE}"
      ;;
  esac
}

DOCKER_CMS_API_BASE="$(resolve_docker_build_cms_api_base)"
build_args=(--build-arg "CMS_API_BASE=${DOCKER_CMS_API_BASE}")
if [[ "$DOCKER_CMS_API_BASE" != "$CMS_API_BASE" ]]; then
  echo "==> [rebuild-web] docker build uses ${DOCKER_CMS_API_BASE} (host preflight ${CMS_API_BASE})"
fi
if [[ "${WEB_BUILD_NO_CACHE:-0}" == "1" ]]; then
  build_args+=(--no-cache)
fi

echo "==> [rebuild-web] building web image ${WEB_IMAGE} (docker CMS_API_BASE=${DOCKER_CMS_API_BASE})"
docker compose -f "$COMPOSE_FILE" build "${build_args[@]}" web

echo "==> [rebuild-web] restarting web service"
docker compose -f "$COMPOSE_FILE" up -d web

echo "==> [rebuild-web] waiting for loopback health"
ready=0
for _ in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:13080/health.json" 2>/dev/null | grep -q '"status":"ok"'; then
    ready=1
    break
  fi
  sleep 2
done
if [[ "$ready" != "1" ]]; then
  echo "error: http://127.0.0.1:13080/health.json did not return status ok" >&2
  docker compose -f "$COMPOSE_FILE" logs web --tail=40 >&2 || true
  exit 1
fi
echo "PASS loopback /health.json"

if [[ "$SKIP_PUBLIC_SMOKE" != "1" ]]; then
  if curl -fsS -o /dev/null -w '%{http_code}' --max-time 10 "https://tahamohamadi.ir/health.json" 2>/dev/null | grep -q '^200$'; then
    echo "==> [rebuild-web] public smoke https://tahamohamadi.ir"
    bash "${SCRIPT_DIR}/smoke.sh" "https://tahamohamadi.ir"
  else
    echo "==> [rebuild-web] skipping public smoke (https://tahamohamadi.ir unreachable from this host)"
  fi
fi

docker compose -f "$COMPOSE_FILE" ps web
echo "rebuild-web PASS"
