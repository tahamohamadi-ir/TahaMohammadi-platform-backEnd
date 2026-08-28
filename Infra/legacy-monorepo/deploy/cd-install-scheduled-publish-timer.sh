#!/usr/bin/env bash
# CD / owner-attended systemd timer install for publish_scheduled_content.
#
# Requires root via passwordless sudo (CD) or interactive sudo (manual).
#
# Usage (VPS or via GitHub Actions SSH):
#   bash infra/deploy/cd-install-scheduled-publish-timer.sh
#
# Env:
#   CMS_REPO_DIR   default /home/deploy/cms-repo (or repo root when script lives there)
#   SKIP_GIT_PULL  set to 1 to skip ff-only pull of main (CD may have already synced)
#
# Owner-attended: Actions → CD → Run workflow → install_scheduled_timer=true.
# No repository variable enables this on ordinary pushes.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_FROM_SCRIPT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
CMS_REPO_DIR="${CMS_REPO_DIR:-${REPO_FROM_SCRIPT}}"
SKIP_GIT_PULL="${SKIP_GIT_PULL:-0}"

cd "$CMS_REPO_DIR"

if [[ ! -f infra/cms/docker-compose.cms.yml ]]; then
  echo "error: missing infra/cms/docker-compose.cms.yml under ${CMS_REPO_DIR}" >&2
  exit 1
fi

echo "==> [cd-install-scheduled-publish-timer] repo_dir=${CMS_REPO_DIR}"

if [[ "$SKIP_GIT_PULL" != "1" ]]; then
  echo "==> [cd-install-scheduled-publish-timer] git pull --ff-only origin main"
  git fetch origin main
  git checkout main
  git pull --ff-only origin main
fi

echo "repo_head=$(git rev-parse --short HEAD)"

echo "==> [cd-install-scheduled-publish-timer] install-scheduled-publish-timer.sh"
if ! sudo -n /opt/taha/bin/install-scheduled-publish-timer.sh "$CMS_REPO_DIR"; then
  echo "error: passwordless sudo required for CD path (sudo -n /opt/taha/bin/install-scheduled-publish-timer.sh)" >&2
  echo "hint: owner one-time on VPS: cd /home/deploy/cms-repo && git pull --ff-only origin main && sudo bash infra/deploy/install-scheduled-publish-timer-sudo.sh" >&2
  exit 1
fi

echo "==> [cd-install-scheduled-publish-timer] summary"
echo "repo_head=$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
echo "cd-install-scheduled-publish-timer PASS"
