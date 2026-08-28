#!/usr/bin/env bash
# SUPERSEDED — use infra/deploy/rebuild-web.sh (post Caddy web cutover,
# LOG-0173/LOG-0216). The loopback flow below is kept for reference and for
# the transition period only.
#
# End-to-end static rebuild after CMS publish (P3-08 deploy slice).
#
# Builds apps/web with loopback CMS_API_BASE, stages release-<sha>, and atomically
# switches /opt/taha/site/current via update-release.sh.
#
# After Caddy web cutover (public HTML from the `web` nginx container at
# 127.0.0.1:13080 instead of file_server on /opt/taha/site/current), use
# infra/deploy/rebuild-web.sh instead. During transition you may run both.
#
# Usage (operator on VPS):
#   bash infra/deploy/rebuild-static.sh
#
# Env:
#   CMS_API_BASE   default http://127.0.0.1:18000
#   STAGE_DIR      default /home/deploy/taha-stage
#   SKIP_DEPLOY    set to 1 to build/stage only (no symlink switch)
#
# Safety:
#   - previous release remains on disk for rollback
#   - build failure exits before update-release.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

echo "==> [rebuild-static] building with CMS content"
bash "${SCRIPT_DIR}/build-static-with-cms.sh"

SHORT_SHA="$(git -C "$REPO_ROOT" rev-parse --short HEAD)"
RELEASE_DIR="${STAGE_DIR:-/home/deploy/taha-stage}/release-${SHORT_SHA}"

if [[ "${SKIP_DEPLOY:-0}" == "1" ]]; then
  echo "==> [rebuild-static] SKIP_DEPLOY=1 — staged only at ${RELEASE_DIR}"
  exit 0
fi

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "==> [rebuild-static] re-exec with sudo for atomic switch"
  exec sudo -n bash "$0" "$@"
fi

echo "==> [rebuild-static] switching current -> ${RELEASE_DIR}"
bash "${SCRIPT_DIR}/update-release.sh" "$RELEASE_DIR"

echo "==> [rebuild-static] smoke"
bash "${SCRIPT_DIR}/smoke.sh" https://tahamohamadi.ir

echo "rebuild-static PASS"
