#!/usr/bin/env bash
# SUPERSEDED — static-symlink era (ADR-0017). Public HTML is now served by the
# `web` nginx container behind Compose Caddy (LOG-0216); web rollback is a
# previous image via cd.yml. Kept for reference only.
#
# Static P1 rollback — restore a previous release (ADR-0017).
# Candidate script: review against the P0A-01 inventory before first use.
#
# Usage:
#   SITE_ROOT=/opt/taha/site ./rollback.sh /opt/taha/site/releases/release-<previous-version>-<checksum8>

set -euo pipefail

SITE_ROOT="${SITE_ROOT:?set SITE_ROOT or export it}"
RELEASE_DIR="${1:?usage: rollback.sh <absolute-previous-release-path>}"

if [[ ! -d "$RELEASE_DIR" ]]; then
  echo "error: artifact directory missing: $RELEASE_DIR" >&2
  exit 1
fi

ln -sfn "$RELEASE_DIR" "$SITE_ROOT/current.tmp"
mv -Tf "$SITE_ROOT/current.tmp" "$SITE_ROOT/current"

printf '%s rollback %s\n' "$(date -u +%FT%TZ)" "$(basename "$RELEASE_DIR")" \
  >> "$SITE_ROOT/deploy.log"

echo "rolled back to $(basename "$RELEASE_DIR"); re-validate and reload Caddy"
