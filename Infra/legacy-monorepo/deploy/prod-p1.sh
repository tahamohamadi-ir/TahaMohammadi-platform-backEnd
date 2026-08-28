#!/usr/bin/env bash
# SUPERSEDED — staging-era script (P0A-09); staging decommissioned (ADR-0025)
# and public HTML now served by the `web` container (LOG-0216). References to
# staging.tahamohamadi.ir below are historical. Kept for reference only.
#
# Stage the static P1 artifact on staging.tahamohamadi.ir (P0A-09).
# Run with root/sudo. Production blocks in the Caddyfile are untouched.
#
# Usage:
#   sudo ./prod-p1.sh /home/deploy/taha-stage/release-<version>-<hash>
#
# Safety:
#   - backs up the Caddyfile before editing;
#   - validates the candidate config before reload; on validation failure the
#     backup is restored and the script exits non-zero (Caddy is not reloaded);
#   - switches /opt/taha/site/current atomically; the previous release stays on
#     disk for rollback.

set -euo pipefail

RELEASE_DIR="${1:?usage: sudo prod-p1.sh <absolute-release-path>}"
SITE_ROOT="${SITE_ROOT:-/opt/taha/site}"
CADDY_FILE="/etc/caddy/Caddyfile"
CADDY_BACKUP="$CADDY_FILE.pre-prod-p1.$(date +%Y%m%d%H%M%S)"

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
install -d -o root -g root -m 0755 "$SITE_ROOT/releases/$(basename "$RELEASE_DIR")"
cp -a "$RELEASE_DIR/." "$SITE_ROOT/releases/$(basename "$RELEASE_DIR")/"
chown -R root:root "$SITE_ROOT/releases/$(basename "$RELEASE_DIR")"
chmod -R a+rX "$SITE_ROOT/releases/$(basename "$RELEASE_DIR")"

# 3. atomic switch of the current pointer
ln -sfn "$SITE_ROOT/releases/$(basename "$RELEASE_DIR")" "$SITE_ROOT/current.tmp"
mv -Tf "$SITE_ROOT/current.tmp" "$SITE_ROOT/current"

# 4. Caddyfile: replace the staging site block with static serving
cp -a "$CADDY_FILE" "$CADDY_BACKUP"

python3 - "$CADDY_FILE" <<'PYEOF'
import sys

path = sys.argv[1]
with open(path, encoding="utf-8") as f:
    text = f.read()

marker = "tahamohamadi.ir {"
idx = text.find(marker)
if idx == -1:
    print("error: production block not found in Caddyfile", file=sys.stderr)
    sys.exit(1)

prefix = text[:idx].rstrip() + "\n"
new_block = """tahamohamadi.ir {
\timport taha_security_headers

\troot * /opt/taha/site/current

\thandle_errors {
\t\trewrite * /404.html
\t\tfile_server
\t}

\tfile_server
}
"""
with open(path, "w", encoding="utf-8") as f:
    f.write(prefix + new_block)
PYEOF

# 5. validate; restore backup on failure (no reload happens)
if ! caddy validate --config "$CADDY_FILE"; then
  echo "error: caddy validate failed; restoring previous Caddyfile" >&2
  cp -a "$CADDY_BACKUP" "$CADDY_FILE"
  exit 1
fi

systemctl reload caddy

CHECKSUM="$(find "$SITE_ROOT/releases/$(basename "$RELEASE_DIR")" -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum | cut -c1-8)"
printf '%s staged %s %s\n' "$(date -u +%FT%TZ)" "$(basename "$RELEASE_DIR")" "$CHECKSUM" \
  >> "$SITE_ROOT/deploy.log"

echo "staged $(basename "$RELEASE_DIR") on tahamohamadi.ir and reloaded Caddy"
echo "Caddy backup retained at $CADDY_BACKUP"
