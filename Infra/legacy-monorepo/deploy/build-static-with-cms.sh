#!/usr/bin/env bash
# Build the public Astro artifact with optional CMS content at build time.
#
# Usage (on VPS after CMS is healthy on loopback):
#   bash infra/deploy/build-static-with-cms.sh
#
# Env:
#   CMS_API_BASE   default http://127.0.0.1:18000 (loopback; no public /api/ required)
#   WEB_REPO_DIR   default repo root (parent of apps/web)
#   STAGE_DIR      default /home/deploy/taha-stage
#
# Output:
#   ${STAGE_DIR}/release-<short-sha>/  ready for update-release.sh
#
# Does NOT switch /opt/taha/site/current — call update-release.sh separately.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
WEB_DIR="${WEB_REPO_DIR:-${REPO_ROOT}}/apps/web"
STAGE_DIR="${STAGE_DIR:-/home/deploy/taha-stage}"
CMS_API_BASE="${CMS_API_BASE:-http://127.0.0.1:18000}"

if [[ ! -d "$WEB_DIR" ]]; then
  echo "error: apps/web missing at ${WEB_DIR}" >&2
  exit 1
fi

SHORT_SHA="$(git -C "$REPO_ROOT" rev-parse --short HEAD)"
RELEASE_NAME="release-${SHORT_SHA}"
RELEASE_DIR="${STAGE_DIR}/${RELEASE_NAME}"

echo "=== preflight: CMS loopback health ==="
if ! curl -fsS "${CMS_API_BASE%/}/health/" | grep -q '"db"'; then
  echo "error: CMS not healthy at ${CMS_API_BASE}/health/" >&2
  exit 1
fi
echo "CMS health OK at ${CMS_API_BASE}"

if ! command -v npm >/dev/null 2>&1; then
  echo "error: npm not found on this host." >&2
  echo "Install Node 24 (PROJECT_MANIFEST) or build on a machine with an SSH tunnel:" >&2
  echo "  ssh -L 18000:127.0.0.1:18000 … then CMS_API_BASE=http://127.0.0.1:18000 npm run build" >&2
  exit 1
fi

echo "=== build Astro with CMS_API_BASE=${CMS_API_BASE} ==="
cd "$WEB_DIR"
npm ci
export CMS_API_BASE
npm run build

if [[ ! -f dist/health.json ]]; then
  echo "error: dist/health.json missing after build" >&2
  exit 1
fi

echo "=== stage artifact ${RELEASE_DIR} ==="
install -d -m 0755 "$STAGE_DIR"
rm -rf "$RELEASE_DIR"
install -d -m 0755 "$RELEASE_DIR"
cp -a dist/. "$RELEASE_DIR/"

echo "build-static-with-cms PASS"
echo "release_dir=${RELEASE_DIR}"
echo "next: sudo -n /opt/taha/bin/update-release.sh ${RELEASE_DIR}"
