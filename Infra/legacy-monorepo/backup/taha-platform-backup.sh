#!/usr/bin/env bash
# Back up the live CMS PostgreSQL + media, Caddy config, and Compose files.
# Optionally also dump the legacy pre-CMS stack if those containers/paths still exist.
# Secrets are supplied only by /etc/taha-backup.env and never belong in this file.
#
# Usage:
#   taha-platform-backup           # real restic backup (systemd)
#   taha-platform-backup --dry-run # inventory + readiness only (no restic)

set -Eeuo pipefail

PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

readonly CMS_POSTGRES_CONTAINER='taha-cms-db-1'
readonly CMS_MEDIA_PATH='/var/lib/docker/volumes/taha-cms_cms_media/_data'
readonly CMS_COMPOSE_CANDIDATES=(
  '/home/deploy/cms-repo/infra/cms/docker-compose.cms.yml'
  '/opt/taha/cms-repo/infra/cms/docker-compose.cms.yml'
)

readonly LEGACY_POSTGRES_CONTAINER='taha-prod-postgres-1'
readonly LEGACY_MEDIA_PATH='/var/lib/docker/volumes/taha_prod_media_data/_data'
readonly CADDYFILE_PATH='/etc/caddy/Caddyfile'
readonly LEGACY_COMPOSE_PATH='/opt/taha/repository/compose.yaml'
readonly LEGACY_PRODUCTION_COMPOSE_PATH='/opt/taha/repository/compose.production.yaml'
readonly LOCK_PATH='/run/lock/taha-platform-backup.lock'

DRY_RUN=0
if (( $# == 1 )) && [[ "$1" == '--dry-run' ]]; then
  DRY_RUN=1
elif (( $# != 0 )); then
  echo 'Usage: taha-platform-backup [--dry-run]' >&2
  exit 64
fi

container_running() {
  local name="$1"
  [[ "$(docker inspect --format '{{.State.Running}}' "$name" 2>/dev/null || echo false)" == 'true' ]]
}

resolve_cms_compose() {
  local candidate
  for candidate in "${CMS_COMPOSE_CANDIDATES[@]}"; do
    if [[ -r "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

if [[ "$DRY_RUN" -eq 0 ]]; then
  : "${RCLONE_CONFIG:?RCLONE_CONFIG must be set by the systemd environment file}"
  : "${RESTIC_PASSWORD_FILE:?RESTIC_PASSWORD_FILE must be set by the systemd environment file}"
  : "${RESTIC_REPOSITORY:?RESTIC_REPOSITORY must be set by the systemd environment file}"

  for required_file in "$RCLONE_CONFIG" "$RESTIC_PASSWORD_FILE" "$CADDYFILE_PATH"; do
    if [[ ! -r "$required_file" ]]; then
      echo "Required backup input is not readable: $required_file" >&2
      exit 1
    fi
  done
else
  if [[ ! -r "$CADDYFILE_PATH" ]]; then
    echo "Required backup input is not readable: $CADDYFILE_PATH" >&2
    exit 1
  fi
fi

if ! container_running "$CMS_POSTGRES_CONTAINER"; then
  echo "CMS PostgreSQL container is not running: $CMS_POSTGRES_CONTAINER" >&2
  echo "RISK-0003 requires the live CMS database (taha-cms) to be dumpable." >&2
  exit 1
fi

CMS_COMPOSE_PATH=''
if CMS_COMPOSE_PATH="$(resolve_cms_compose)"; then
  :
else
  echo "Warning: CMS docker-compose.cms.yml not found in known paths; continuing without it." >&2
  CMS_COMPOSE_PATH=''
fi

CONFIG_PATHS=("$CADDYFILE_PATH")
if [[ -n "$CMS_COMPOSE_PATH" ]]; then
  CONFIG_PATHS+=("$CMS_COMPOSE_PATH")
fi
if [[ -r "$LEGACY_COMPOSE_PATH" ]]; then
  CONFIG_PATHS+=("$LEGACY_COMPOSE_PATH")
fi
if [[ -r "$LEGACY_PRODUCTION_COMPOSE_PATH" ]]; then
  CONFIG_PATHS+=("$LEGACY_PRODUCTION_COMPOSE_PATH")
fi

MEDIA_PATHS=()
if [[ -d "$CMS_MEDIA_PATH" ]]; then
  MEDIA_PATHS+=("$CMS_MEDIA_PATH")
else
  echo "Warning: CMS media volume path absent: $CMS_MEDIA_PATH (ok if unused)." >&2
fi
if [[ -d "$LEGACY_MEDIA_PATH" ]]; then
  MEDIA_PATHS+=("$LEGACY_MEDIA_PATH")
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "dry-run: CMS postgres dump source = $CMS_POSTGRES_CONTAINER"
  if container_running "$LEGACY_POSTGRES_CONTAINER"; then
    echo "dry-run: legacy postgres dump source = $LEGACY_POSTGRES_CONTAINER"
  else
    echo "dry-run: legacy postgres not running (skipped)"
  fi
  echo "dry-run: media paths:"
  if ((${#MEDIA_PATHS[@]} == 0)); then
    echo "  (none)"
  else
    printf '  %s\n' "${MEDIA_PATHS[@]}"
  fi
  echo "dry-run: config paths:"
  printf '  %s\n' "${CONFIG_PATHS[@]}"
  echo "dry-run: OK (no restic invoked)"
  exit 0
fi

exec 9>"$LOCK_PATH"
if ! flock -n 9; then
  echo 'Another Taha platform backup is already running; no overlapping backup was started.' >&2
  exit 75
fi

# Primary: live CMS database (RISK-0003)
restic backup \
  --stdin-filename cms-postgres-all.sql \
  --stdin-from-command \
  --tag production \
  --tag cms \
  --tag postgres \
  -- \
  docker exec "$CMS_POSTGRES_CONTAINER" sh -ceu 'exec pg_dumpall -U "$POSTGRES_USER"'

# Optional legacy stack dump while the old container still exists
if container_running "$LEGACY_POSTGRES_CONTAINER"; then
  restic backup \
    --stdin-filename legacy-postgres-all.sql \
    --stdin-from-command \
    --tag production \
    --tag legacy \
    --tag postgres \
    -- \
    docker exec "$LEGACY_POSTGRES_CONTAINER" sh -ceu 'exec pg_dumpall -U "$POSTGRES_USER"'
fi

FILE_BACKUP_ARGS=(
  restic backup
  --tag production
  --tag media
  --tag config
)
if ((${#MEDIA_PATHS[@]} > 0)); then
  FILE_BACKUP_ARGS+=("${MEDIA_PATHS[@]}")
fi
FILE_BACKUP_ARGS+=("${CONFIG_PATHS[@]}")
"${FILE_BACKUP_ARGS[@]}"

restic forget --keep-daily 7 --keep-weekly 4 --keep-monthly 12 --prune
