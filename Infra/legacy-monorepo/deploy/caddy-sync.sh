#!/usr/bin/env bash
# caddy-sync.sh — deploy a repo-managed Caddyfile to /etc/caddy/Caddyfile.
#
# Usage:  sudo caddy-sync.sh [/path/to/new/Caddyfile]
#
# Installed at /opt/taha/bin/caddy-sync.sh on the VPS (root:root 0755).
# The CD workflow can also run it from a temporary path via SSH.
#
# Host-edge only. After DEFER-0031 live cutover (Compose profile `edge`), CD
# uses repository variable CADDY_EDGE=compose and infra/deploy/caddy-compose-reload.sh
# instead of this script. Do not sync Caddyfile.compose into /etc/caddy/.
#
# Steps:
#   1. Back up the current Caddyfile with a timestamp
#   2. Copy the new file into place
#   3. Validate with `caddy validate`
#   4. On success: `systemctl reload caddy`
#   5. On failure: restore backup and exit 1
set -euo pipefail

SRC="${1:-/tmp/Caddyfile.deploy}"
CADDY_FILE="/etc/caddy/Caddyfile"
BACKUP="${CADDY_FILE}.bak-$(date +%Y%m%d%H%M%S)"

if [ "$(id -u)" -ne 0 ]; then
  echo "error: caddy-sync.sh must run as root (use sudo)" >&2
  exit 1
fi

if [ ! -f "$SRC" ]; then
  echo "error: source Caddyfile not found: $SRC" >&2
  exit 1
fi

cp -a "$CADDY_FILE" "$BACKUP"
echo "backup: $BACKUP"

cp "$SRC" "$CADDY_FILE"

if ! caddy validate --config "$CADDY_FILE" 2>&1; then
  echo "validation failed; restoring backup" >&2
  cp -a "$BACKUP" "$CADDY_FILE"
  exit 1
fi

systemctl reload caddy
echo "caddy: config applied and reloaded"
