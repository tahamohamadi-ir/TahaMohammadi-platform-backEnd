#!/usr/bin/env bash
# One-time owner setup: NOPASSWD CMS update wrapper (mirrors update-release.sh).
# Run as root on the VPS:
#   sudo bash infra/deploy/install-update-cms-sudo.sh

set -euo pipefail

REPO_DIR="${1:-/home/deploy/cms-repo}"
SUDOERS_LINE='deploy ALL=(root) NOPASSWD: /opt/taha/bin/update-release.sh *, /opt/taha/bin/caddy-apply.sh, /opt/taha/bin/update-cms.sh *'

install -d -m 0755 /opt/taha/bin
install -m 0755 "${REPO_DIR}/infra/deploy/opt-taha-bin-update-cms.sh" /opt/taha/bin/update-cms.sh

SUDOERS_FILE="/etc/sudoers.d/taha-deploy"
printf '%s\n' "$SUDOERS_LINE" > "$SUDOERS_FILE"
chmod 0440 "$SUDOERS_FILE"
visudo -c

# Allow deploy to run docker-backed repo scripts without interactive sudo when using wrapper.
usermod -aG docker deploy 2>/dev/null || true

echo "Installed /opt/taha/bin/update-cms.sh and updated ${SUDOERS_FILE}"
echo "Deploy may run: sudo -n /opt/taha/bin/update-cms.sh ghcr.io/tahamohamadi-ir/taha-cms:<tag>"
