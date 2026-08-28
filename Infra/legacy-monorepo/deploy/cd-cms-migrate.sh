#!/usr/bin/env bash
# CD / owner-attended CMS image update with backup + migrate + smoke (ADR-0027 Slice 2).
#
# Runs as deploy (docker group). Does NOT require root.
#
# Usage (VPS or via GitHub Actions SSH):
#   export CMS_IMAGE=ghcr.io/tahamohamadi-ir/taha-cms:<sha>
#   bash infra/deploy/cd-cms-migrate.sh
#
# Env:
#   CMS_REPO_DIR   default /home/deploy/cms-repo (or repo root when script lives there)
#   BACKUP_ROOT    default: writable dir under deploy home (see resolve_backup_root)
#   SKIP_GIT_PULL  set to 1 to skip ff-only pull of main (CD may have already synced)
#   SKIP_SMOKE     set to 1 to skip public smoke-cms.sh
#   SITE_URL       default https://tahamohamadi.ir
#
# RISK-0012: first production CD migrate must be owner-attended (workflow_dispatch).
# Unattended CD only when repository variable CMS_CD_AUTO_MIGRATE=true.
#
# Note: /home/deploy/backups is often root-owned (prod-cms-update-migrate.sh). This
# script prefers a deploy-writable path so CD SSH does not need sudo for mkdir.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_FROM_SCRIPT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
CMS_REPO_DIR="${CMS_REPO_DIR:-${REPO_FROM_SCRIPT}}"
SKIP_GIT_PULL="${SKIP_GIT_PULL:-0}"
SKIP_SMOKE="${SKIP_SMOKE:-0}"
SITE_URL="${SITE_URL:-https://tahamohamadi.ir}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
COMPOSE_FILE="infra/cms/docker-compose.cms.yml"

: "${CMS_IMAGE:?CMS_IMAGE is required - pin a GHCR sha tag (e.g. ghcr.io/tahamohamadi-ir/taha-cms:2e200fe)}"

resolve_backup_root() {
  local candidate
  if [[ -n "${BACKUP_ROOT:-}" ]]; then
    candidate="$BACKUP_ROOT"
    if mkdir -p "$candidate" 2>/dev/null; then
      echo "$candidate"
      return 0
    fi
    echo "error: BACKUP_ROOT=${candidate} is not writable by $(id -un)" >&2
    return 1
  fi
  for candidate in \
    "${HOME}/cms-migrate-backups" \
    /home/deploy/cms-migrate-backups \
    /home/deploy/backups
  do
    if mkdir -p "$candidate" 2>/dev/null; then
      echo "$candidate"
      return 0
    fi
  done
  echo "error: no writable backup root (tried \$HOME/cms-migrate-backups and /home/deploy/backups)" >&2
  return 1
}

cd "$CMS_REPO_DIR"

if [[ ! -f "$COMPOSE_FILE" ]]; then
  echo "error: missing ${COMPOSE_FILE} under ${CMS_REPO_DIR}" >&2
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "error: docker not found - deploy user needs docker group access" >&2
  exit 1
fi

BACKUP_ROOT="$(resolve_backup_root)"
BACKUP_DIR="${BACKUP_ROOT}/pre-migrate-${TIMESTAMP}"

PREVIOUS_IMAGE="$(docker inspect taha-cms-cms-1 --format '{{.Config.Image}}' 2>/dev/null || echo unknown)"
echo "==> [cd-cms-migrate] previous_image=${PREVIOUS_IMAGE}"
echo "==> [cd-cms-migrate] target_image=${CMS_IMAGE}"

echo "==> [cd-cms-migrate] pg_dumpall backup -> ${BACKUP_DIR}"
mkdir -p "$BACKUP_DIR"
docker exec taha-cms-db-1 sh -ceu 'exec pg_dumpall -U "$POSTGRES_USER"' \
  > "${BACKUP_DIR}/cms-postgres-all.sql"
dump_size="$(wc -c < "${BACKUP_DIR}/cms-postgres-all.sql" | tr -d ' ')"
if [[ "$dump_size" -lt 1024 ]]; then
  echo "error: backup too small (${dump_size} bytes): ${BACKUP_DIR}/cms-postgres-all.sql" >&2
  exit 1
fi
echo "backup_ok path=${BACKUP_DIR}/cms-postgres-all.sql size=${dump_size}"

if [[ "$SKIP_GIT_PULL" != "1" ]]; then
  echo "==> [cd-cms-migrate] git pull --ff-only origin main"
  git fetch origin main
  git checkout main
  git pull --ff-only origin main
  echo "repo_head=$(git rev-parse --short HEAD)"
fi

echo "==> [cd-cms-migrate] update-cms.sh"
export CMS_IMAGE CMS_BUILD="${CMS_BUILD:-0}"
bash "${SCRIPT_DIR}/update-cms.sh"

if [[ "$SKIP_SMOKE" != "1" ]]; then
  echo "==> [cd-cms-migrate] smoke-cms.sh ${SITE_URL}"
  bash "${SCRIPT_DIR}/smoke-cms.sh" "$SITE_URL"
fi

echo "==> [cd-cms-migrate] summary"
echo "previous_image=${PREVIOUS_IMAGE}"
echo "new_image=${CMS_IMAGE}"
echo "backup_dir=${BACKUP_DIR}"
echo "repo_head=$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
echo "cd-cms-migrate PASS"
echo "Rollback: export CMS_IMAGE=${PREVIOUS_IMAGE} && bash infra/deploy/update-cms.sh"
