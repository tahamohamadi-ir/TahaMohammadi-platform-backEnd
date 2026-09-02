# Legacy Monorepo Inventory — `Infra/legacy-monorepo/`

Date: 2026-09-02
Task: BACKEND-090
Scope: every file under `Back-End/Infra/legacy-monorepo/` (46 files in `backup/`, `cms/`, `deploy/`).

## Evidence rules

- The tree is **reference-only evidence**. Every file was read for inspection only.
- **No file inside the tree was modified, moved, or deleted.**
- **No script, compose file, or Python snippet in the tree was executed.**
- Classifications are based on the actual file contents and on the owned docs:
  `docs/operations/LOCAL-DATABASE.md`, `docs/operations/STANDALONE-MIGRATION.md`,
  `docs/operations/DEPLOYMENT-READINESS.md`, `Docs/07-migration/BACKEND-MIGRATION-MANIFEST.md`,
  and `Docs/02-architecture/DEPLOYMENT-TOPOLOGY.md`.
- `delete` is a recommendation for a later, explicitly authorized cleanup. Per
  `STANDALONE-MIGRATION.md` step 6, superseded files are archived **only after
  replacement evidence exists**. This task deletes nothing.
- Rewrite targets are stated as required new parameters (paths, names, ports)
  derived from owned docs. No infrastructure decision is invented here; where a
  decision is still open (proxy implementation, deploy model), the target says so.

## Summary

| Classification | Count | Meaning |
|---|---|---|
| rewrite | 20 | Active logic the standalone platform still needs; must be re-authored for the new repo layout before use |
| reference | 18 | Evidence/history only; no action |
| delete | 8 | Self-marked superseded; candidates for later removal after replacement evidence exists |
| **Total** | **46** | |

Counts per directory: `backup/` 4 rewrite / 1 reference / 0 delete; `cms/` 6 / 3 / 1; `deploy/` 10 / 14 / 7.

## `backup/` (5 files)

| Path | Classification | Reason | Rewrite target |
|---|---|---|---|
| `backup/taha-platform-backup.sh` | rewrite | Active backup logic: `pg_dumpall` of the CMS DB via `docker exec`, optional legacy-DB dump, media volume + Caddyfile + compose config into restic, retention `--keep-daily 7 --keep-weekly 4 --keep-monthly 12`, flock, `--dry-run` mode. All targets are legacy names. | New backup script for the standalone compose: new DB container/service name, new media volume path, new compose file path; secrets still only from a root-owned env file. Feeds BACKEND-160 backup/restore validation. |
| `backup/taha-platform-backup.service` | rewrite | systemd oneshot unit for the backup script (`/usr/local/sbin/taha-platform-backup`, `/etc/taha-backup.env`). | Same unit shape; new script path if changed; `EnvironmentFile` path re-confirmed. |
| `backup/taha-platform-backup.timer` | rewrite | Daily schedule (`03:20 UTC`, persistent, randomized delay) for the backup. | Same schedule for the standalone platform unless an owner decision changes it. |
| `backup/taha-backup.env.example` | rewrite | Env template for the restic/rclone job (locations + repository URI only, no secrets). | Same mechanism; repository URI and credential paths are owner-owned and must be re-confirmed, not copied blindly. |
| `backup/README.md` | reference | Install runbook + history for the legacy backup (RISK-0003 closure, restore drill pointers into legacy `docs/` that are not in this snapshot). | None (a new backup runbook is part of the backup rewrite; do not copy this one). |

## `cms/` (10 files)

| Path | Classification | Reason | Rewrite target |
|---|---|---|---|
| `cms/docker-compose.cms.yml` | rewrite | Production compose topology: `db` (postgres:17-alpine), `cms` (gunicorn, `127.0.0.1:18000→8000`, user `10001`, healthcheck on `/health/`), plus `web` and `admin` services and a `caddy` edge profile — monorepo services the standalone backend does not own. Hardening patterns (mem/cpus limits, healthchecks, fail-closed `${CMS_IMAGE:?}`) must survive. | BACKEND-100 standalone compose: backend-only (db + app), new project/service/container names, fail-closed image vars kept; `web`/`admin`/edge-caddy services belong to the separate frontend repositories and the proxy decision, not this compose. |
| `cms/Dockerfile.cms` | rewrite | Active image build: multi-stage uv sync from `apps/cms/pyproject.toml` + `uv.lock`, non-root uid 10001, build-time collectstatic, gunicorn CMD, `/health/` HEALTHCHECK. Build context assumes monorepo root (`COPY apps/cms ...`, context `../..`). | Standalone Dockerfile at `Back-End/` root (manifest: app code is at repo root, `pyproject.toml`/`uv.lock` copied as-is). Keep runtime hardening; drop monorepo paths. |
| `cms/publish-scheduled-content.sh` | rewrite | Active scheduled-publish executor: `docker compose exec -T cms python manage.py publish_scheduled_content`, defaults `CMS_REPO_DIR=/home/deploy/cms-repo`, compose path `infra/cms/…`. | Same command against the new compose project/file; new repo dir parameter. Scheduled-publish validation is a standalone-migration requirement. |
| `cms/taha-publish-scheduled-content.service` | rewrite | systemd oneshot unit for the publish script (`CMS_REPO_DIR=/home/deploy/cms-repo`). | Same unit; new repo dir and script path. |
| `cms/taha-publish-scheduled-content.timer` | rewrite | Every-minute systemd timer for scheduled publishes. | Same schedule for the standalone platform. |
| `cms/.env.example` | rewrite | Production runtime env shape: `DJANGO_SETTINGS_MODULE=config.settings.production`, `POSTGRES_*` (service `db`), SMTP contact-form keys, `PREVIEW_SHARE_SECRET`, image selection. `REBUILD_TRIGGER_*` keys couple to the legacy web rebuild and must not be carried over as-is. | New standalone production env template, reconciled with the existing `Back-End/.env.example`; drop `REBUILD_TRIGGER_*` or re-specify it as an explicit deployment contract; keep contact-form honesty behavior (`/health/` degraded when contact enabled without email). |
| `cms/docker-compose.local.yml` | reference | Legacy `taha-local` dev stack (port 15432). `docs/operations/LOCAL-DATABASE.md` explicitly references this file for the monorepo-parity profile (`config.settings.local` → `127.0.0.1:15432`), so it is retained until that profile is retired. Do **not** delete while an owned doc references it. | None (new-platform dev DB is `Back-End/docker-compose.dev.yml` on 5433 per LOCAL-DATABASE.md). |
| `cms/Caddyfile.cms.api.snippet` | reference | Self-labeled OPTIONAL (DEFER-0017). Evidence of `/api/v1/admin/*`, `/api/admin/*` (no-store, noindex) vs generic `/api/*` (60s) vs `/media/*` (1 day) edge handling and insert-order before catch-all. Input to the future same-origin proxy config, not a deployable file. | None directly; informs the BACKEND-101 Caddy routing table. |
| `cms/Caddyfile.cms.snippet` | delete | Header says "SUPERSEDED … replaced by infra/caddy/Caddyfile … Do not use for deployment" (the full Caddyfile is not in this snapshot). Its routing evidence (`/admin/*`, `/staff/*`, `/static/*`, `/health/*`, `/health.json` not stolen) is duplicated in `cms/README.md` and the smoke scripts retained as reference. | None. |
| `cms/README.md` | reference | Canonical evidence for the legacy runtime architecture (Caddy routing table, loopback ports, image tagging, CD checklist, admin/STAFF split, rebuild chain) and for the path assumptions listed below. | None (superseded runbook content is re-authored per task, not copied). |

## `deploy/` (31 files)

| Path | Classification | Reason | Rewrite target |
|---|---|---|---|
| `deploy/update-cms.sh` | rewrite | Core deploy step: pull/build pinned image, derive `WEB_IMAGE` from running container, guard leftover `cms-*` containers and stale `:18000` binds, `up -d --force-recreate`, wait for db DNS + `db=ok`, `migrate`, runtime-deps check, loopback `/admin/login/` smoke. | Standalone update/deploy script: new compose file/project/service names, new image name, no web/admin image derivation. |
| `deploy/cd-cms-migrate.sh` | rewrite | Owner-attended CD migrate: pre-migrate `pg_dumpall` backup (size-guarded) → ff-only pull → update-cms.sh → public smoke; RISK-0012 gating notes. | Same sequence for the new layout: new container name for the dump, new backup root, new compose path. Feeds BACKEND-160. |
| `deploy/caddy-sync.sh` | rewrite | Backup → install → `caddy validate` → reload → restore-on-failure deploy of a repo-managed Caddyfile. Pattern is the safe way to apply the future proxy config. | Apply script for the new repo-managed Caddyfile once the proxy implementation is decided (DEPLOYMENT-TOPOLOGY.md requires an exact routing table before staging). |
| `deploy/install-update-cms-sudo.sh` | rewrite | One-time root setup: `/opt/taha/bin/update-cms.sh` wrapper + `/etc/sudoers.d/taha-deploy` NOPASSWD line + docker group. | Re-derived sudoers/wrapper set for the new scripts and paths if the owner keeps the passwordless-deploy model; owner decision required. |
| `deploy/opt-taha-bin-update-cms.sh` | rewrite | Root-owned passwordless wrapper delegating to `update-cms.sh`. | Same pattern against the new update script/path. |
| `deploy/install-scheduled-publish-timer.sh` | rewrite | Installs publish script + unit + timer into `/usr/local/sbin` and `/etc/systemd/system`. | Same install against new source paths. |
| `deploy/install-scheduled-publish-timer-sudo.sh` | rewrite | NOPASSWD wrapper installer for the timer path (writes the full sudoers line incl. `caddy-sync.sh`). | Same pattern for the new wrapper set; owner decision on sudo model. |
| `deploy/opt-taha-bin-install-scheduled-publish-timer.sh` | rewrite | Root wrapper delegating to the timer installer. | Same pattern, new paths. |
| `deploy/cd-install-scheduled-publish-timer.sh` | rewrite | CD/attended orchestration: ff-only pull + passwordless sudo wrapper call; fails closed when sudoers grant is missing. | Same flow against new paths. |
| `deploy/smoke-cms.sh` | rewrite | Backend-owned smoke: `/health/` returns CMS JSON with `"db"`, `/admin/` + `/admin/login/` (SPA shell or sign-in), `/staff/login/` (server-rendered form), `/health.json` must stay static (no `/health*` proxy). Also asserts legacy web loopback/static payload — those parts are not backend-owned. | Standalone backend smoke script: keep `/health/`, `/admin/*`, `/staff/*`, `/health.json` distinction checks; drop web-loopback (13080) and static-payload assertions. |
| `deploy/apply-caddy-api.sh` | reference | Historical DEFER-0017 inline edit of `/etc/caddy/Caddyfile` (insert `/api/*`, `/media/*` handles). The inline-edit-on-`/etc` pattern is legacy; evidence of cache headers and anchor ordering only. | None (future proxy config is repo-managed, per DEPLOYMENT-TOPOLOGY evidence rules). |
| `deploy/caddy-compose-reload.sh` | reference | Compose-edge caddy reload (DEFER-0031): derives app images from running containers, `/var/www/html` guard, in-container validate. Tied to the legacy `taha-cms` project and legacy host paths. | None yet; evidence for a future reload helper after the proxy decision. |
| `deploy/cd-rebuild-web.sh` | reference | CD rebuild of the public `web` container after CMS publish — frontend artifact pipeline, cross-repo contract evidence. | None in this repo (frontend/deploy-contract ownership per STANDALONE-MIGRATION.md step 4). |
| `deploy/rebuild-web.sh` | reference | Builds `apps/web` inside Docker with `CMS_API_BASE` (loopback preflight → public origin fallback for the build container), restarts `web`, smokes 13080 + public site. Frontend coupling. | None in this repo; documents the rebuild contract the frontend repo must own. |
| `deploy/build-static-with-cms.sh` | reference | Builds the Astro artifact with `CMS_API_BASE`, requires `dist/health.json`, stages `release-<sha>`. Frontend build coupling. | None in this repo (same as above). |
| `deploy/smoke.sh` | reference | Static-site smoke (locales, robots, sitemap, 404) plus the `/health.json` (static) vs `/health/` (CMS) distinctness rule — key evidence for the BACKEND-101 proxy config. | None; the health-path distinction must be preserved by the rewrite of the proxy/smoke. |
| `deploy/smoke-blog.sh` | reference | Public content smoke (`/en|fa/writing/`, `/research/`, blog→writing redirect, `/api/articles/en`). Mostly public-site delivery; the `/api/articles/en` check is the backend-relevant part. | None; standalone API smoke should cover published-only public endpoints. |
| `deploy/update-release.sh` | reference | Static-era release switch (`/opt/taha/site/current` symlink) still wired into sudoers and legacy rebuild scripts. Static delivery is not backend scope. | None. |
| `deploy/decommission-old-stack.md` | reference | CLOSED runbook (LOG-0216) for the old `taha-prod-*` stack with hard safety rules (never `down -v` without confirmed backup) and rollback history. | None (history; the volume-preservation warning still applies to any future decommission). |
| `deploy/owner-vps-maintenance.sh` | reference | Owner-attended apt upgrade + host-Caddy→Compose-edge cutover with ACME `/var/lib/caddy` seeding and rollback. Executed legacy cutover; live host facts inside. | None (a new maintenance runbook is authored per task when the new host layout exists). |
| `deploy/prod-cms-update-migrate.sh` | reference | Root-run update+migrate with manual backup; self-marked superseded for routine use by the CD path; incident-only. | None (the cd-cms-migrate rewrite covers the sequence). |
| `deploy/prod-cms-reset-and-migrate.sh` | reference | Incident-only hard-reset (`reset --hard`, `clean -fd`) + `.env` backup + update+migrate; self-marked superseded for routine use. | None (pattern noted for incident runbook authoring). |
| `deploy/run-prod-cms-migrate.ps1` | reference | Incident-only SSH helper (owner password prompt) for the above; documents owner access pattern (host, port 2222, key path). | None. |
| `deploy/s5-admin-cutover.sh` | reference | One-time ADR-0032 admin-SPA cutover (build admin image, `up -d admin`, Caddy `admin:80` upstream, public checks). Already executed; historical. | None. |
| `deploy/caddy-apply.sh` | delete | Header: "SUPERSEDED — replaced by caddy-sync.sh which deploys the full repo-managed Caddyfile … Kept for reference only." Superseding script is in this tree. | None. |
| `deploy/deploy.sh` | delete | Header: "SUPERSEDED — static-symlink era (ADR-0017). Deploys now go through cd.yml (web image + Compose, LOG-0216)." Static-symlink deploy is closed history. | None. |
| `deploy/rollback.sh` | delete | Header: "SUPERSEDED — static-symlink era … web rollback is a previous image via cd.yml." | None. |
| `deploy/prod-p1.sh` | delete | Header: "SUPERSEDED — staging-era script (P0A-09); staging decommissioned (ADR-0025) and public HTML now served by the `web` container (LOG-0216)." | None. |
| `deploy/stage-p1.sh` | delete | Header: "SUPERSEDED — staging host decommissioned (ADR-0025)." | None. |
| `deploy/rebuild-static.sh` | delete | Header: "SUPERSEDED — use infra/deploy/rebuild-web.sh" (present in this tree); static-symlink flow kept only for the closed transition period. | None. |
| `deploy/dev-local-stack.ps1` | delete | Only drives the legacy `taha-local` compose project. The new-platform dev DB workflow is `Back-End/docker-compose.dev.yml` + `docs/operations/LOCAL-DATABASE.md`; no owned doc references this helper. The referenced compose file itself is retained (see `cms/docker-compose.local.yml`). | None. |

## Path assumptions found (input for BACKEND-100 / BACKEND-101)

Every item below is hardcoded in the inspected files and breaks outside the legacy monorepo.

1. **Monorepo repo layout** — scripts and compose assume repo root containing `infra/cms/`, `infra/deploy/`, `infra/caddy/` (referenced, not in snapshot), `apps/cms/`, `apps/web/`, `apps/admin/`; compose build contexts `../..`, `../admin`; `Dockerfile.cms` copies `apps/cms/pyproject.toml`, `apps/cms/uv.lock`; docs paths like `docs/governance/DEPLOY_RUNBOOK.md`. New repo: app code at `Back-End/` root; no `apps/*`, no `infra/caddy` in the snapshot.
2. **VPS filesystem paths** — `/home/deploy/cms-repo` (default `CMS_REPO_DIR` in most scripts), `/opt/taha/bin/*` root wrappers, `/opt/taha/site/current` + `/opt/taha/site/releases` (static era), `/opt/taha/repository/compose.yaml` + `compose.production.yaml` (old stack), `/home/deploy/taha-stage`, `/home/deploy/backups` and `~/cms-migrate-backups` (pre-migrate dumps), `/etc/caddy/Caddyfile`, `/var/lib/caddy` (ACME seed), `/var/www/html` (legacy `/fonts`, `/presentation` bind), `/etc/taha-backup.env`, `/root/.config/rclone/rclone.conf`, `/root/.config/taha-backup/restic-password`.
3. **Compose project / service / container names** — project `taha-cms`; containers `taha-cms-db-1`, `taha-cms-cms-1`, `taha-cms-web-1` (inspected by scripts); leftover cleanup targets `cms-cms-1`, `cms-db-1`; network aliases `db`/`postgres`/`cms`/`web`/`admin`/`caddy`; legacy dev project `taha-local` with container `taha-local-db`; decommissioned `taha-prod-frontend-1`, `taha-prod-backend-1`, `taha-prod-postgres-1`; volume names `taha-cms_cms_media`, `taha_prod_media_data`, `taha-cms_caddy_data`.
4. **Ports** — loopback `127.0.0.1:18000→8000` (cms), `127.0.0.1:13080→8080` (web), `127.0.0.1:13081→80` (admin), public `80/443/443udp` (edge profile), dev `15432` (taha-local) and `18001` (a2 rehearsal). New-platform dev DB is `5433` per LOCAL-DATABASE.md; the standalone deploy port set is a BACKEND-100 decision.
5. **Caddy site config** — host file `/etc/caddy/Caddyfile` with a `tahamohamadi.ir {` site block, `import taha_application_routes` anchor, `(taha_application_routes)` snippet, `root * /opt/taha/site/current`, handles for `/admin/*`, `/staff/*`, `/static/*`, `/health/*`, `/api/*`, `/media/*` ordered before `file_server`; `/health.json` must never be proxied (no `/health*` glob); Compose-edge variant `Caddyfile.compose` proxies `web:8080`, `cms:8000`, `admin:80` via Docker DNS.
6. **Frontend build coupling** — `REBUILD_TRIGGER_ENABLED` / `REBUILD_TRIGGER_SECRET` / `REBUILD_SCRIPT_PATH` env keys; publish → `invoke_static_rebuild` → `/rebuild-trigger/` → `rebuild-web.sh`; Astro builds take `CMS_API_BASE` (loopback `http://127.0.0.1:18000` for host preflight, `https://tahamohamadi.ir` fallback inside Docker build); artifact must contain `dist/health.json`. STANDALONE-MIGRATION.md step 4 requires replacing this with an explicit deployment contract.
7. **Domains, hosts, remote endpoints** — `tahamohamadi.ir` / `www.tahamohamadi.ir` in `ALLOWED_HOSTS` and every smoke/CD default (`SITE_URL`); decommissioned `staging.tahamohamadi.ir`; SSH `deploy@85.192.29.196:2222` (port 22 kept for VPN), key `~/.ssh/taha-platform-ops`; GitHub repo `tahamohamadi-ir/Taha-personal-platform`; GHCR images `ghcr.io/tahamohamadi-ir/taha-cms|taha-web:<sha>` and `CADDY_EDGE` repository variable.
8. **systemd + sudo model** — units `taha-platform-backup.service/.timer` (daily 03:20 UTC) and `taha-publish-scheduled-content.service/.timer` (every minute) installing to `/usr/local/sbin` + `/etc/systemd/system`; sudoers fragment `/etc/sudoers.d/taha-deploy` granting `deploy` NOPASSWD on `/opt/taha/bin/*` wrappers.
9. **Settings/image env names** — `DJANGO_SETTINGS_MODULE=config.settings.production` baked into both the Dockerfile and compose; `POSTGRES_HOST=db` derived from the compose service name; `infra/cms/.env` (chmod 600) as the runtime secret file.

## Files with judgment calls

All 46 files were classified. These four involved a close call, stated so the owner can overrule:

- `cms/.env.example` — classified **rewrite** (production env shape still needed); the alternative reading is reference-only because the new repo already has `Back-End/.env.example` for development. The production template must be re-derived, not copied (`REBUILD_TRIGGER_*` coupling).
- `cms/docker-compose.local.yml` — classified **reference** rather than delete only because `LOCAL-DATABASE.md` still points at it for the legacy-parity profile (port 15432). Becomes deletable when that profile is retired.
- `deploy/caddy-sync.sh` and the sudoers/wrapper set (`install-update-cms-sudo.sh`, `opt-taha-bin-*`, `install-scheduled-publish-timer*`, `cd-install-scheduled-publish-timer.sh`) — classified **rewrite** because the capability (safe proxy config apply, passwordless deploy, scheduled-publish install) is part of the operating model; the actual deploy model for the new platform is not yet decided, so their rewrite targets are parameterized, not pinned.
- `deploy/smoke-cms.sh` — classified **rewrite**; it mixes backend-owned checks with legacy web-loopback/static assertions, and only the backend subset carries over.
