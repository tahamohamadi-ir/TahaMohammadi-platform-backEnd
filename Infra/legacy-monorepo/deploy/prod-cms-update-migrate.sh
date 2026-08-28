#!/usr/bin/env bash
# Production CMS update + migrate (P4–P6). Run on VPS as deploy (re-execs via sudo).
# Usage:
#   export CMS_IMAGE=ghcr.io/tahamohamadi-ir/taha-cms:<sha>
#   sudo bash infra/deploy/prod-cms-update-migrate.sh
#
# CMS_IMAGE is REQUIRED. Use the latest sha tag from the "CMS image" workflow on
# main (GitHub → Actions → CMS image), NOT HEAD: docs-only merges do not build an
# image. Pinning a stale tag (e.g. b369885) skips migrations 0005/0006 and the
# `import_profile_seed` command. Known-good at 2026-08-18: 430061b (PR #31).
#
# Requires: one interactive sudo password on first run. Does not print secrets.

set -euo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "error: run as root — sudo CMS_IMAGE=${CMS_IMAGE:-...} bash $0" >&2
  exit 1
fi

CMS_REPO_DIR="${CMS_REPO_DIR:-/home/deploy/cms-repo}"
: "${CMS_IMAGE:?CMS_IMAGE is required — export it to the latest GHCR sha tag from the 'CMS image' workflow on main (e.g. ghcr.io/tahamohamadi-ir/taha-cms:430061b). A stale default previously pinned b369885 and skipped migrations 0005/0006.}"
BACKUP_ROOT="${BACKUP_ROOT:-/home/deploy/backups}"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="${BACKUP_ROOT}/pre-migrate-${TIMESTAMP}"
COMPOSE_FILE="infra/cms/docker-compose.cms.yml"

cd "$CMS_REPO_DIR"

echo "=== preflight: record current image ==="
PREVIOUS_IMAGE="$(docker inspect taha-cms-cms-1 --format '{{.Config.Image}}' 2>/dev/null || echo unknown)"
echo "previous_image=${PREVIOUS_IMAGE}"
echo "target_image=${CMS_IMAGE}"

echo "=== step 1: manual pg_dumpall backup ==="
mkdir -p "$BACKUP_DIR"
docker exec taha-cms-db-1 sh -ceu 'exec pg_dumpall -U "$POSTGRES_USER"' \
  > "${BACKUP_DIR}/cms-postgres-all.sql"
dump_size="$(wc -c < "${BACKUP_DIR}/cms-postgres-all.sql")"
if [[ "$dump_size" -lt 1024 ]]; then
  echo "error: backup too small (${dump_size} bytes): ${BACKUP_DIR}/cms-postgres-all.sql" >&2
  exit 1
fi
echo "backup_ok path=${BACKUP_DIR}/cms-postgres-all.sql size=${dump_size}"

echo "=== step 2: align repo ==="
chown -R deploy:deploy "$CMS_REPO_DIR" 2>/dev/null || true
sudo -u deploy git -C "$CMS_REPO_DIR" fetch origin main
sudo -u deploy git -C "$CMS_REPO_DIR" checkout main
sudo -u deploy git -C "$CMS_REPO_DIR" pull --ff-only origin main
echo "repo_head=$(git -C "$CMS_REPO_DIR" rev-parse --short HEAD)"

echo "=== step 3: update-cms (includes migrate) ==="
export CMS_IMAGE CMS_BUILD="${CMS_BUILD:-0}"
cd "$CMS_REPO_DIR"
bash infra/deploy/update-cms.sh

echo "=== step 4: post-deploy validation ==="
cd "$CMS_REPO_DIR"
bash infra/deploy/smoke-cms.sh https://tahamohamadi.ir
docker compose -f "$COMPOSE_FILE" exec -T cms python manage.py showmigrations content

echo "=== summary ==="
echo "previous_image=${PREVIOUS_IMAGE}"
echo "new_image=${CMS_IMAGE}"
echo "backup_dir=${BACKUP_DIR}"
echo "repo_head=$(git rev-parse --short HEAD)"
echo "prod-cms-update-migrate PASS"
