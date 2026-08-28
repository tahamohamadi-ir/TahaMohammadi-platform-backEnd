#!/usr/bin/env bash
# S5 cutover — run on VPS as deploy user. Idempotent; safe to re-run.
set -euo pipefail
cd /home/deploy/cms-repo

echo "== 0) repo fresh =="
sudo git pull --ff-only origin main

echo "== 1) admin image (rebuild if missing/stale) =="
sudo docker build -f apps/admin/Dockerfile -t taha-admin:local apps/admin

echo "== 2) start admin service WITH the env vars compose requires =="
# Interpolation needs CMS_IMAGE/WEB_IMAGE; reuse the images of the running containers.
# NOTE: `sudo -E` is unsupported here, so pass vars explicitly after sudo.
CMS_IMAGE="$(docker inspect taha-cms-cms-1 --format '{{.Config.Image}}')"
WEB_IMAGE="$(docker inspect taha-cms-web-1 --format '{{.Config.Image}}')"
export ADMIN_IMAGE=taha-admin:local
cd infra/cms
sudo env "CMS_IMAGE=$CMS_IMAGE" "WEB_IMAGE=$WEB_IMAGE" "ADMIN_IMAGE=$ADMIN_IMAGE" \
  docker compose -f docker-compose.cms.yml up -d admin
sleep 3
sudo env "CMS_IMAGE=$CMS_IMAGE" "WEB_IMAGE=$WEB_IMAGE" "ADMIN_IMAGE=$ADMIN_IMAGE" \
  docker compose -f docker-compose.cms.yml ps admin
echo "local nginx check:"
curl -s -o /dev/null -w "  127.0.0.1:13081/admin/ -> %{http_code}\n" http://127.0.0.1:13081/admin/

echo "== 3) point Caddy at the new container and reload =="
cd /home/deploy/cms-repo
grep -n "reverse_proxy admin:80" infra/caddy/Caddyfile.compose || { echo "Caddyfile not updated?!"; exit 1; }
bash infra/deploy/caddy-compose-reload.sh

echo "== 4) public verification =="
curl -s -o /dev/null -w "https://tahamohamadi.ir/admin/          -> %{http_code}\n" https://tahamohamadi.ir/admin/
curl -s -o /dev/null -w "https://tahamohamadi.ir/admin/login     -> %{http_code}\n" https://tahamohamadi.ir/admin/login
curl -s -o /dev/null -w "https://tahamohamadi.ir/api/site        -> %{http_code}\n" https://tahamohamadi.ir/api/site
echo "== done =="
