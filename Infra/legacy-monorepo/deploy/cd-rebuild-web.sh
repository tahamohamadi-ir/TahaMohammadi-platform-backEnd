#!/usr/bin/env bash
# CD / owner-attended web container rebuild after CMS publish or schema change.
#
# Runs as deploy (docker group). Does NOT require root.
#
# Usage (VPS or via GitHub Actions SSH):
#   bash infra/deploy/cd-rebuild-web.sh
#
# Env:
#   CMS_REPO_DIR       default /home/deploy/cms-repo (or repo root when script lives there)
#   SKIP_GIT_PULL      set to 1 to skip ff-only pull of main (CD may have already synced)
#   SKIP_PUBLIC_SMOKE  passed through to rebuild-web.sh
#   CMS_API_BASE       passed through (default loopback in rebuild-web.sh)
#   WEB_BUILD_NO_CACHE set to 1 for clean Docker build
#
# Owner-attended: Actions → CD → Run workflow → rebuild_web=true.
# No repository variable enables this on ordinary pushes (unlike CMS migrate auto path).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_FROM_SCRIPT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
CMS_REPO_DIR="${CMS_REPO_DIR:-${REPO_FROM_SCRIPT}}"
SKIP_GIT_PULL="${SKIP_GIT_PULL:-0}"
COMPOSE_FILE="infra/cms/docker-compose.cms.yml"

cd "$CMS_REPO_DIR"

if [[ ! -f "$COMPOSE_FILE" ]]; then
  echo "error: missing ${COMPOSE_FILE} under ${CMS_REPO_DIR}" >&2
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "error: docker not found — deploy user needs docker group access" >&2
  exit 1
fi

PREVIOUS_WEB_IMAGE="$(docker inspect taha-cms-web-1 --format '{{.Config.Image}}' 2>/dev/null || echo unknown)"
echo "==> [cd-rebuild-web] previous_web_image=${PREVIOUS_WEB_IMAGE}"
echo "==> [cd-rebuild-web] repo_dir=${CMS_REPO_DIR}"

if [[ "$SKIP_GIT_PULL" != "1" ]]; then
  echo "==> [cd-rebuild-web] git pull --ff-only origin main"
  git fetch origin main
  git checkout main
  git pull --ff-only origin main
fi

echo "repo_head=$(git rev-parse --short HEAD)"

echo "==> [cd-rebuild-web] rebuild-web.sh"
bash "${SCRIPT_DIR}/rebuild-web.sh"

echo "==> [cd-rebuild-web] summary"
echo "previous_web_image=${PREVIOUS_WEB_IMAGE}"
echo "repo_head=$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
echo "cd-rebuild-web PASS"
