#!/usr/bin/env bash
# Manual fallback to rebuild the public Astro site after CMS publish (P3-08).
#
# Run from repo checkout on the VPS (or dev machine without deploy):
#   bash apps/cms/scripts/manual-rebuild.sh
#
# On the VPS with CMS running on loopback, prefer:
#   bash infra/deploy/rebuild-static.sh
#
# Build-only (no artifact switch):
#   SKIP_DEPLOY=1 bash infra/deploy/rebuild-static.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
DEPLOY_SCRIPT="${REPO_ROOT}/infra/deploy/rebuild-static.sh"

if [[ -f "$DEPLOY_SCRIPT" ]]; then
  echo "==> [manual-rebuild] delegating to infra/deploy/rebuild-static.sh"
  exec bash "$DEPLOY_SCRIPT" "$@"
fi

echo "==> [manual-rebuild] deploy scripts missing — build only"
cd "${REPO_ROOT}/apps/web"
npm ci
npm run build
echo "==> [manual-rebuild] build complete at apps/web/dist (no deploy)"
