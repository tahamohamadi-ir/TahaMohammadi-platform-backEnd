#!/usr/bin/env bash
# SUPERSEDED for routine use — the attended path is the GitHub Actions
# `workflow_dispatch` CMS-migrate job (infra/deploy/cd-cms-migrate.sh) with
# owner approval; SSH use is incident-only. Kept for reference.
#
# Production CMS: hard-reset repo, pin GHCR image, update + migrate.
# Run on VPS as root:
#   sudo CMS_IMAGE=ghcr.io/<owner>/taha-cms:<sha> bash \
#     /home/deploy/cms-repo/infra/deploy/prod-cms-reset-and-migrate.sh
#
# No default image pin: a stale default (b369885) previously skipped newer
# migrations. Export CMS_IMAGE explicitly to the latest GHCR sha tag from the
# 'CMS image' workflow on main.
set -euo pipefail

CMS_REPO_DIR="${CMS_REPO_DIR:-/home/deploy/cms-repo}"
CMS_IMAGE="${CMS_IMAGE:?CMS_IMAGE is required — export it to the latest GHCR sha tag from the 'CMS image' workflow on main (e.g. ghcr.io/tahamohamadi-ir/taha-cms:<sha>). A stale default previously pinned b369885 and skipped migrations.}"
CMS_BUILD="${CMS_BUILD:-0}"
COMPOSE_FILE="${CMS_REPO_DIR}/infra/cms/docker-compose.cms.yml"
ENV_FILE="${CMS_REPO_DIR}/infra/cms/.env"
ENV_BACKUP="/root/cms.env.backup.$(date +%Y%m%d-%H%M%S)"

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "error: run as root — sudo bash $0" >&2
  exit 1
fi

git_as_deploy() {
  sudo -u deploy git -C "$CMS_REPO_DIR" "$@"
}

echo "=== 0) preflight ==="
PREVIOUS_IMAGE="$(docker inspect taha-cms-cms-1 --format '{{.Config.Image}}' 2>/dev/null || echo unknown)"
echo "previous_image=${PREVIOUS_IMAGE}"
echo "target_image=${CMS_IMAGE}"
echo "repo_before=$(git_as_deploy rev-parse --short HEAD 2>/dev/null || echo missing)"

echo "=== 1) backup .env ==="
cp -a "$ENV_FILE" "$ENV_BACKUP"
echo "env_backup=${ENV_BACKUP}"

echo "=== 2) hard-align repo to origin/main ==="
git_as_deploy fetch origin main
git_as_deploy reset --hard origin/main
git_as_deploy clean -fd
cp -a "$ENV_BACKUP" "$ENV_FILE"
chown deploy:deploy "$ENV_FILE"
echo "repo_head=$(git_as_deploy rev-parse --short HEAD)"

echo "=== 3) update-cms + migrate ==="
export CMS_IMAGE CMS_BUILD
cd "$CMS_REPO_DIR"
bash infra/deploy/update-cms.sh

echo "=== 4) validation ==="
bash infra/deploy/smoke-cms.sh https://tahamohamadi.ir
docker compose -f "$COMPOSE_FILE" exec -T cms python manage.py showmigrations content

echo "=== summary ==="
echo "previous_image=${PREVIOUS_IMAGE}"
echo "new_image=${CMS_IMAGE}"
echo "running_image=$(docker inspect taha-cms-cms-1 --format '{{.Config.Image}}')"
echo "env_backup=${ENV_BACKUP}"
echo "repo_head=$(git_as_deploy rev-parse --short HEAD)"
echo "prod-cms-reset-and-migrate PASS"
