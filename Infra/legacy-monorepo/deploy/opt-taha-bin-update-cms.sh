#!/usr/bin/env bash
# Root-owned wrapper for passwordless CMS deploy (sudo NOPASSWD).
# Installed to /opt/taha/bin/update-cms.sh — see install-update-cms-sudo.sh
#
# Usage (matches update-release.sh — image as argument, not env):
#   sudo -n /opt/taha/bin/update-cms.sh ghcr.io/tahamohamadi-ir/taha-cms:<sha>

set -euo pipefail

CMS_REPO_DIR="${CMS_REPO_DIR:-/home/deploy/cms-repo}"
COMPOSE_FILE="${CMS_REPO_DIR}/infra/cms/docker-compose.cms.yml"

if [[ -n "${1:-}" ]]; then
  export CMS_IMAGE="$1"
fi

if [[ -z "${CMS_IMAGE:-}" ]]; then
  echo "usage: update-cms.sh <CMS_IMAGE>" >&2
  echo "example: sudo -n /opt/taha/bin/update-cms.sh ghcr.io/tahamohamadi-ir/taha-cms:<sha>" >&2
  exit 1
fi

if [[ ! -f "$COMPOSE_FILE" ]]; then
  echo "missing compose file: $COMPOSE_FILE" >&2
  exit 1
fi

export CMS_BUILD="${CMS_BUILD:-0}" CMS_PULL_POLICY="${CMS_PULL_POLICY:-always}"
cd "$CMS_REPO_DIR"
exec bash infra/deploy/update-cms.sh
