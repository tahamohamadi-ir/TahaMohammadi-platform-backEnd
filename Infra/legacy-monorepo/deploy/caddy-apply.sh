#!/usr/bin/env bash
# SUPERSEDED — replaced by caddy-sync.sh which deploys the full repo-managed
# Caddyfile (infra/caddy/Caddyfile). Kept for reference only.
#
# Root-only (installed at /opt/taha/bin/caddy-apply.sh, root:root 0755):
# applies the documented 404 handle_errors fix to /etc/caddy/Caddyfile with
# backup -> validate -> reload; restores the backup if validation fails.
#
# This is intentionally a FIXED transformation (no user-supplied patch files),
# so the sudoers grant for this script cannot be used to run arbitrary code.
set -euo pipefail

CADDY_FILE="/etc/caddy/Caddyfile"
BACKUP="$CADDY_FILE.auto-$(date +%Y%m%d%H%M%S)"

cp -a "$CADDY_FILE" "$BACKUP"

python3 - "$CADDY_FILE" <<'PYEOF'
import sys

path = sys.argv[1]
with open(path, encoding="utf-8") as f:
    text = f.read()

start = text.find("(taha_application_routes) {")
if start == -1:
    print("error: taha_application_routes snippet not found", file=sys.stderr)
    sys.exit(1)

close = text.find("\n}", start)
if close == -1:
    print("error: snippet closing brace not found", file=sys.stderr)
    sys.exit(1)

region = text[start:close]
if "handle_errors" in region:
    print("handle_errors already present in snippet; no change")
    sys.exit(0)

# canonical snippet rewrite: balanced braces, closing } on its own line
new_snippet = (
    "(taha_application_routes) {\n"
    "\troot * /opt/taha/site/current\n"
    "\tfile_server\n"
    "\thandle_errors {\n"
    "\t\trewrite * /404.html\n"
    "\t\tfile_server\n"
    "\t}\n"
    "}\n"
)
new_text = text[:start] + new_snippet + text[close + 2 :]

with open(path, "w", encoding="utf-8") as f:
    f.write(new_text)
print("handle_errors block inserted into taha_application_routes")
PYEOF

if ! caddy validate --config "$CADDY_FILE"; then
  echo "validation failed; restoring $BACKUP" >&2
  cp -a "$BACKUP" "$CADDY_FILE"
  exit 1
fi

systemctl reload caddy
echo "caddy: 404 handle_errors applied and reloaded; backup at $BACKUP"
