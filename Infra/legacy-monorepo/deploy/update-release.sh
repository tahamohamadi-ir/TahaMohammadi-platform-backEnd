#!/usr/bin/env bash
# Update the production release served by /opt/taha/site/current.
# Run with root/sudo. The deployed Caddy snippet already serves
# $SITE_ROOT/current, so no Caddyfile change or reload is needed.
#
# Usage:
#   sudo ./update-release.sh /home/deploy/taha-prod/release-<version>-<hash>
#
# Safety:
#   - switches /opt/taha/site/current atomically; the previous release stays on
#     disk for rollback.

set -euo pipefail

RELEASE_DIR="${1:?usage: sudo update-release.sh <absolute-release-path>}"
SITE_ROOT="${SITE_ROOT:-/opt/taha/site}"
BASENAME="$(basename "$RELEASE_DIR")"

if [[ ! -d "$RELEASE_DIR" ]]; then
  echo "error: release directory missing: $RELEASE_DIR" >&2
  exit 1
fi

if [[ ! -f "$RELEASE_DIR/health.json" ]]; then
  echo "error: artifact has no health.json" >&2
  exit 1
fi

# 1. layout
install -d -o root -g root -m 0755 "$SITE_ROOT/releases"
touch "$SITE_ROOT/deploy.log"

# 2. install artifact (idempotent copy) and normalize ownership/permissions so
#    the caddy user can traverse the tree (scp artifacts arrive mode 0700)
install -d -o root -g root -m 0755 "$SITE_ROOT/releases/$BASENAME"
cp -a "$RELEASE_DIR/." "$SITE_ROOT/releases/$BASENAME/"
chown -R root:root "$SITE_ROOT/releases/$BASENAME"
chmod -R a+rX "$SITE_ROOT/releases/$BASENAME"

# 3. atomic switch of the current pointer
ln -sfn "$SITE_ROOT/releases/$BASENAME" "$SITE_ROOT/current.tmp"
mv -Tf "$SITE_ROOT/current.tmp" "$SITE_ROOT/current"

CHECKSUM="$(find "$SITE_ROOT/releases/$BASENAME" -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum | cut -c1-8)"
printf '%s updated %s %s\n' "$(date -u +%FT%TZ)" "$BASENAME" "$CHECKSUM" \
  >> "$SITE_ROOT/deploy.log"

echo "current -> $BASENAME (checksum $CHECKSUM)"
