#!/usr/bin/env bash
# Insert public /api/* and /media/* handles into /etc/caddy/Caddyfile
# inside the tahamohamadi.ir block, immediately before import taha_application_routes.
#
# Usage:
#   bash /home/deploy/cms-repo/infra/deploy/apply-caddy-api.sh

set -euo pipefail

CADDYFILE="${CADDYFILE:-/etc/caddy/Caddyfile}"

if [[ ! -f "$CADDYFILE" ]]; then
  echo "missing ${CADDYFILE}" >&2
  exit 1
fi

if grep -qE 'handle[[:space:]]+/api/\*' "$CADDYFILE"; then
  echo "handle /api/* already present — validating and reloading only"
  sudo caddy validate --config "$CADDYFILE"
  sudo systemctl reload caddy
  echo "reloaded"
  exit 0
fi

BACKUP="/etc/caddy/Caddyfile.pre-api.$(date -u +%Y%m%dT%H%M%SZ)"
sudo cp -a "$CADDYFILE" "$BACKUP"
echo "backup=${BACKUP}"

python3 - "$CADDYFILE" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
insert = """        handle /api/* {
                reverse_proxy 127.0.0.1:18000 {
                        header_up X-Forwarded-Proto {scheme}
                        header_up X-Forwarded-Host {host}
                        header_down Cache-Control "public, max-age=60"
                }
        }

        handle /media/* {
                reverse_proxy 127.0.0.1:18000 {
                        header_up X-Forwarded-Proto {scheme}
                        header_up X-Forwarded-Host {host}
                        header_down Cache-Control "public, max-age=86400"
                }
        }

"""
anchor = "tahamohamadi.ir {"
start = text.find(anchor)
if start < 0:
    raise SystemExit("tahamohamadi.ir block not found")
marker = "        import taha_application_routes"
idx = text.find(marker, start)
if idx < 0:
    raise SystemExit("import taha_application_routes not found in tahamohamadi.ir block")
Path("/tmp/Caddyfile.api-pending").write_text(text[:idx] + insert + text[idx:], encoding="utf-8")
print("wrote /tmp/Caddyfile.api-pending")
PY

sudo install -m 0644 /tmp/Caddyfile.api-pending "$CADDYFILE"
sudo caddy validate --config "$CADDYFILE"
sudo systemctl reload caddy
echo "Caddy API handles applied and reloaded"
