#!/usr/bin/env bash
# Run Django publish_scheduled_content inside the Compose cms service.
# Intended for systemd timer / cron (no Celery). Owner-attended install.
set -euo pipefail

CMS_REPO_DIR="${CMS_REPO_DIR:-/home/deploy/cms-repo}"
COMPOSE_FILE="${COMPOSE_FILE:-infra/cms/docker-compose.cms.yml}"

cd "$CMS_REPO_DIR"
docker compose -f "$COMPOSE_FILE" exec -T cms \
  python manage.py publish_scheduled_content
