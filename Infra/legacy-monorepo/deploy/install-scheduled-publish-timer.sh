#!/usr/bin/env bash
# One-time owner setup: systemd timer for publish_scheduled_content (DEBT-0005).
#
# Run as root on the VPS from repo root:
#   cd /home/deploy/cms-repo
#   git pull --ff-only origin main
#   sudo bash infra/deploy/install-scheduled-publish-timer.sh
#
# CD path (passwordless): owner installs wrapper first via
# install-scheduled-publish-timer-sudo.sh, then dispatches install_scheduled_timer=true.

set -euo pipefail

REPO_DIR="${1:-/home/deploy/cms-repo}"
SCRIPT_SRC="${REPO_DIR}/infra/cms/publish-scheduled-content.sh"
SERVICE_SRC="${REPO_DIR}/infra/cms/taha-publish-scheduled-content.service"
TIMER_SRC="${REPO_DIR}/infra/cms/taha-publish-scheduled-content.timer"

for f in "$SCRIPT_SRC" "$SERVICE_SRC" "$TIMER_SRC"; do
  if [[ ! -f "$f" ]]; then
    echo "error: missing ${f} — run from synced cms-repo" >&2
    exit 1
  fi
done

install -m 755 "$SCRIPT_SRC" /usr/local/sbin/taha-publish-scheduled-content
install -m 644 "$SERVICE_SRC" /etc/systemd/system/taha-publish-scheduled-content.service
install -m 644 "$TIMER_SRC" /etc/systemd/system/taha-publish-scheduled-content.timer

systemctl daemon-reload
systemctl enable --now taha-publish-scheduled-content.timer

echo "==> scheduled-publish timer status"
systemctl list-timers 'taha-publish-scheduled-content*' --no-pager || true
echo "install-scheduled-publish-timer PASS"
