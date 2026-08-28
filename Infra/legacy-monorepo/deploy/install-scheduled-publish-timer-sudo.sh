#!/usr/bin/env bash
# One-time owner setup: NOPASSWD wrapper for scheduled-publish timer install.
# Mirrors install-update-cms-sudo.sh / update-release.sh pattern.
#
# Run as root on the VPS after cms-repo is synced:
#   cd /home/deploy/cms-repo
#   git pull --ff-only origin main
#   sudo bash infra/deploy/install-scheduled-publish-timer-sudo.sh

set -euo pipefail

REPO_DIR="${1:-/home/deploy/cms-repo}"
SUDOERS_LINE='deploy ALL=(root) NOPASSWD: /opt/taha/bin/update-release.sh *, /opt/taha/bin/caddy-apply.sh, /opt/taha/bin/update-cms.sh *, /opt/taha/bin/caddy-sync.sh *, /opt/taha/bin/install-scheduled-publish-timer.sh *'

install -d -m 0755 /opt/taha/bin
install -m 0755 \
  "${REPO_DIR}/infra/deploy/opt-taha-bin-install-scheduled-publish-timer.sh" \
  /opt/taha/bin/install-scheduled-publish-timer.sh

SUDOERS_FILE="/etc/sudoers.d/taha-deploy"
printf '%s\n' "$SUDOERS_LINE" > "$SUDOERS_FILE"
chmod 0440 "$SUDOERS_FILE"
visudo -c

echo "Installed /opt/taha/bin/install-scheduled-publish-timer.sh and updated ${SUDOERS_FILE}"
echo "Deploy may run: sudo -n /opt/taha/bin/install-scheduled-publish-timer.sh /home/deploy/cms-repo"
