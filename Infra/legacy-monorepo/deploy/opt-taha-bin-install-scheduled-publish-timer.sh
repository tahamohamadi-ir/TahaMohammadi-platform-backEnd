#!/usr/bin/env bash
# Root-owned wrapper for passwordless scheduled-publish timer install.
# Installed to /opt/taha/bin/install-scheduled-publish-timer.sh —
# see install-scheduled-publish-timer-sudo.sh
#
# Usage:
#   sudo -n /opt/taha/bin/install-scheduled-publish-timer.sh
#   sudo -n /opt/taha/bin/install-scheduled-publish-timer.sh /home/deploy/cms-repo

set -euo pipefail

CMS_REPO_DIR="${1:-/home/deploy/cms-repo}"
INSTALL_SCRIPT="${CMS_REPO_DIR}/infra/deploy/install-scheduled-publish-timer.sh"

if [[ ! -f "$INSTALL_SCRIPT" ]]; then
  echo "missing install script: $INSTALL_SCRIPT" >&2
  echo "hint: sync cms-repo (git pull) before install" >&2
  exit 1
fi

exec bash "$INSTALL_SCRIPT" "$CMS_REPO_DIR"
