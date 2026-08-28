# infra/cms — CMS runtime (Docker Compose) + GHCR versioned images

## Architecture (canonical)

```text
Internet
   │
   ▼
Caddy  (TLS + routing)  — host systemd until DEFER-0031 live cutover
   ├── /*            → reverse_proxy 127.0.0.1:13080  (nginx `web`)
   ├── /admin*       → reverse_proxy 127.0.0.1:18000
   ├── /static*      → reverse_proxy 127.0.0.1:18000
   ├── /api* /media* → reverse_proxy 127.0.0.1:18000
   └── /health/      → reverse_proxy 127.0.0.1:18000
       /health.json  → web container (do not glob /health*)

Docker Compose (this directory)
   ├── db    ← postgres:17-alpine + named volume
   ├── cms   ← ghcr.io/<owner>/taha-cms:<git-sha>
   ├── web   ← ghcr.io/<owner>/taha-web:<git-sha>  (nginx + Astro dist)
   └── caddy ← caddy:2.9-alpine (profile `edge` only; public 80/443)
```

After owner cutover (`DEFER-0031`): Compose `caddy` proxies `web:8080` /
`cms:8000` via Docker DNS; host systemd Caddy is stopped. See
`infra/caddy/HOST-CADDY-DISABLE.md` and DEPLOY_RUNBOOK § “Caddy-in-Compose cutover”.

- **Caddy** = edge HTTPS and path routing (host today; Compose profile `edge` when cut over).
- **Versioned artifact (web)** = `taha-web` nginx image (host `current` symlink remains rollback/CD rsync until removed later).
- **Versioned artifact (CMS)** = immutable container image tags on GHCR (git sha).
- **Compose** = `db` + `cms` + `web` (+ optional `caddy` via `--profile edge`).

Do **not** run a Node.js public runtime in production (ADR-0027). The public
`web` service is nginx serving a prebuilt Astro `dist`. Host Caddy remains the
TLS edge until Compose `caddy` cutover (`DEFER-0031`). The sentence “do not containerize
Astro” is superseded: the **artifact** is containerized; the **runtime** is not Node.

## Files

| File | Role |
|---|---|
| `Dockerfile.cms` | Multi-stage image; venv on `PATH`; gunicorn |
| `docker-compose.cms.yml` | `db` + `cms` + `web` + optional `caddy` (profile `edge`) |
| `../caddy/Caddyfile` | Host-edge Caddyfile (CD `caddy-sync` while `CADDY_EDGE` unset) |
| `../caddy/Caddyfile.compose` | Compose-edge Caddyfile (Docker DNS upstreams) |
| `../caddy/HOST-CADDY-DISABLE.md` | Stop host Caddy before Compose binds 80/443 |
| `Caddyfile.cms.snippet` | Historical `/admin*` fragment (superseded by full Caddyfile) |
| `Caddyfile.cms.api.snippet` | Historical `/api*` fragment (live in full Caddyfile) |
| `.env.example` | Template for server-side `.env` (never commit real secrets) |
| `../deploy/caddy-compose-reload.sh` | Reload Compose `caddy` after cutover |
## Image tags

CI workflow `.github/workflows/ci-cms-image.yml` pushes:

```text
ghcr.io/<owner>/taha-cms:<full-git-sha>
ghcr.io/<owner>/taha-cms:<short-sha>
ghcr.io/<owner>/taha-cms:main          # moving pointer on default branch
```

Production **must** set `CMS_IMAGE` to a sha tag for deploy/rollback clarity.
`:main` is a convenience pointer only.

## CD migrate (ADR-0027 Slice 2)

Owner checklist (canonical): `docs/governance/DEPLOY_RUNBOOK.md` — Actions → **CD —
Deploy to production** → Run workflow → `migrate_cms=true` + valid GHCR
`cms_image_tag`, then confirm job **CMS image migrate (gated)** PASS and log
lines `cd-cms-migrate PASS` / `CMS smoke PASS`. Leave `CMS_CD_AUTO_MIGRATE`
**unset** (`RISK-0012` CLOSED after attended PASS LOG-0179 / LOG-0180).

Manual on VPS:

```bash
cd /home/deploy/cms-repo
git pull --ff-only origin main
export CMS_IMAGE=ghcr.io/tahamohamadi-ir/taha-cms:<sha>
bash infra/deploy/cd-cms-migrate.sh
```

## CD web rebuild (ADR-0027 Slice 1+)

Owner checklist: `docs/governance/DEPLOY_RUNBOOK.md` — Actions → **CD —
Deploy to production** → Run workflow → `rebuild_web=true`, then confirm job
**Web container rebuild (gated)** PASS and log lines `rebuild-web PASS` /
`cd-rebuild-web PASS`. No repository variable enables this on ordinary pushes.

Manual on VPS:

```bash
cd /home/deploy/cms-repo
git pull --ff-only origin main
bash infra/deploy/cd-rebuild-web.sh
```

Scheduled publish timer (owner root): Actions → **CD — Deploy to production** →
Run workflow → `install_scheduled_timer=true`, then confirm job **Scheduled
publish timer install (gated)** PASS and log lines `install-scheduled-publish-timer PASS`
/ `cd-install-scheduled-publish-timer PASS`. **One-time VPS prereq** (before first CD dispatch):

```bash
cd /home/deploy/cms-repo
git pull --ff-only origin main
sudo bash infra/deploy/install-scheduled-publish-timer-sudo.sh
```

Manual (interactive sudo):

```bash
cd /home/deploy/cms-repo
git pull --ff-only origin main
sudo bash infra/deploy/install-scheduled-publish-timer.sh
```

## Deploy (operator)

Copy-paste safe (no angle-bracket placeholders):

```bash
cd /home/deploy/cms-repo
git pull --ff-only origin main

# Align required keys once (script also appends missing DJANGO_SETTINGS_MODULE / POSTGRES_HOST)
# Edit secrets in infra/cms/.env if needed:
#   ALLOWED_HOSTS=tahamohamadi.ir,www.tahamohamadi.ir

# Preferred: public GHCR image (no docker login)
export CMS_IMAGE=ghcr.io/tahamohamadi-ir/taha-cms:main
bash infra/deploy/update-cms.sh

# If GHCR is still private, either:
#   echo GITHUB_PAT | docker login ghcr.io -u tahamohamadi-ir --password-stdin
#   (PAT needs read:packages — account password will NOT work)
# Or build on the VPS:
#   export CMS_IMAGE=taha-cms:local CMS_BUILD=1
#   bash infra/deploy/update-cms.sh

# One-time / when snippet changes: merge Caddyfile.cms.snippet into site block
# (before file_server). Required handles: /admin* /static* /health/
# Do not use handle /health* — it would steal /health.json.
# Then: sudo caddy validate && sudo systemctl reload caddy
# Evidence 2026-08-16 (historical): without /static*, admin CSS 404s on the Astro 404 page.
bash infra/deploy/smoke-cms.sh https://tahamohamadi.ir

# Password must be at least 12 characters (do not bypass validation).
docker compose -f infra/cms/docker-compose.cms.yml exec cms python manage.py createsuperuser

# One-time (or after static-site content changes): load published CMS rows from repo seed data
docker compose -f infra/cms/docker-compose.cms.yml exec cms python manage.py seed_site_content
# Refresh existing canonical slugs: add --force

## Edit content in the admin

Public pages are **Astro static**; CMS stores editorial data exposed via `/api/*`.

Content is edited in the React admin SPA at `/admin/` (ADR-0026; ADM-1 cutover,
DEFER-0023 CLOSED). Django staff HTML at `/staff/` remains for LOGIN_URL, draft
preview and MFA fallback (Wagtail removed — DEBT-0003 CLOSED / LOG-0193;
"Wagtail admin" in older runbooks/logs is historical).

After login at `/admin/`, edit:

| Area | What it edits |
|-----------|----------------|
| Articles | Blog / writing (`/{locale}/writing/`; `/{locale}/blog/` redirects permanently) |
| Research topics, statements, publications, projects | Research section |
| Landing / Profiles | CMS copies of hero/about (static rebuild required) |
| Case studies | P6 project depth pages |
| Publications / Books / Talks / Downloads | P8 catalog routes |

Set **Status = Published** and **Published at** in the past. Publishing triggers
the HMAC rebuild hook (`invoke_static_rebuild` → `/rebuild-trigger/` →
`rebuild-web.sh`) when enabled; otherwise rebuild public HTML manually with
`infra/deploy/rebuild-web.sh` (post-cutover canonical; `rebuild-static.sh` is
superseded — see its header).

Upload files via the **Media** library and attach to content as needed.

## Public `/api/` and `/media/` (DEFER-0017)

Optional: expose read-only API at the edge for RSS, ISR, or off-VPS builds.

```bash
# Backup Caddyfile first
sudo cp -a /etc/caddy/Caddyfile /etc/caddy/Caddyfile.pre-api.$(date -u +%Y%m%dT%H%M%SZ)

# Merge infra/cms/Caddyfile.cms.api.snippet into tahamohamadi.ir block
# BEFORE import taha_application_routes / file_server (order matters).

sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy

curl -fsS https://tahamohamadi.ir/api/research/topics/en | head -c 200
curl -fsS -o /dev/null -w '%{http_code}\n' https://tahamohamadi.ir/api/articles/en
```

See `docs/plan/P3-public-api-caddy-task-spec.md` for rollback and smoke steps.

# After this image is running: Account → Two-factor authentication
# (or https://tahamohamadi.ir/admin/account/two-factor/) — scan QR, confirm code.
```

## Public HTML rebuild with CMS content (P3-08 / ADR-0027)

**After Caddy web cutover** (public routes proxy to `127.0.0.1:13080`):

```bash
cd /home/deploy/cms-repo
git pull --ff-only origin main
bash infra/deploy/rebuild-web.sh
```

Builds the Compose `web` nginx image with loopback `CMS_API_BASE`, restarts `web`, and
smokes `http://127.0.0.1:13080/health.json`. See `docs/governance/DEPLOY_RUNBOOK.md`.

**Until cutover / transition** (Caddy still serves `/opt/taha/site/current`):

```bash
cd /home/deploy/cms-repo
git pull --ff-only origin main
bash infra/deploy/rebuild-static.sh
# build-only: SKIP_DEPLOY=1 bash infra/deploy/rebuild-static.sh
```

Uses `CMS_API_BASE=http://127.0.0.1:18000` by default. See
`infra/deploy/build-static-with-cms.sh` and `docs/plan/P3-public-api-caddy-task-spec.md`
for public-edge builds after DEFER-0017.

Always invoke with `bash infra/deploy/...` (scripts are executable in git, but `bash` avoids Permission denied if the bit was lost on checkout).

`python` inside the container is the image venv (Django available). Do not use a
host `python` or an unactivated interpreter.

## Rollback

```bash
export CMS_IMAGE=ghcr.io/tahamohamadi-ir/taha-cms:<previous-sha>
./infra/deploy/update-cms.sh
```

Volumes are preserved (`postgres_data`, `cms_media`). Admin static files are baked into the image (`STATIC_ROOT=/app/staticfiles`); do not mount an empty volume over that path.

## Local build (optional)

```bash
export CMS_IMAGE=taha-cms:local
docker compose -f infra/cms/docker-compose.cms.yml build
docker compose -f infra/cms/docker-compose.cms.yml up -d
```
