#!/usr/bin/env bash
# SUPERSEDED — static-symlink era (ADR-0017). Deploys now go through cd.yml
# (web image + Compose, LOG-0216). Kept for reference only.
#
# Static P1 deploy — atomic symlink switch (ADR-0017).
# Candidate script: review against the P0A-01 inventory before first use.
#
# Usage:
#   SITE_ROOT=/opt/taha/site ./deploy.sh /opt/taha/site/releases/release-<version>-<checksum8>
#
# Guards:
#   - refuses to run if the releases directory or the target artifact is missing;
#   - requires the artifact to contain dist/health.json;
#   - switches the `current` symlink atomically (never deletes the previous one);
#   - records a non-sensitive version + checksum line in deploy.log.

set -euo pipefail

SITE_ROOT="${SITE_ROOT:?set SITE_ROOT or export it}"
RELEASE_DIR="${1:?usage: deploy.sh <absolute-release-path>}"

if [[ ! -d "$SITE_ROOT/releases" ]]; then
  echo "error: releases directory missing: $SITE_ROOT/releases" >&2
  exit 1
fi

if [[ ! -d "$RELEASE_DIR" ]]; then
  echo "error: artifact directory missing: $RELEASE_DIR" >&2
  exit 1
fi

if [[ ! -f "$RELEASE_DIR/health.json" ]]; then
  echo "error: artifact has no dist/health.json: $RELEASE_DIR" >&2
  exit 1
fi

VERSION="$(basename "$RELEASE_DIR")"
CHECKSUM="$(find "$RELEASE_DIR" -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum | cut -c1-8)"
PREV="$(readlink -f "$SITE_ROOT/current" 2>/dev/null || echo none)"

ln -sfn "$RELEASE_DIR" "$SITE_ROOT/current.tmp"
mv -Tf "$SITE_ROOT/current.tmp" "$SITE_ROOT/current"

printf '%s %s %s prev=%s\n' "$(date -u +%FT%TZ)" "$VERSION" "$CHECKSUM" "$PREV" \
  >> "$SITE_ROOT/deploy.log"

echo "deployed $VERSION ($CHECKSUM); previous was $PREV"
