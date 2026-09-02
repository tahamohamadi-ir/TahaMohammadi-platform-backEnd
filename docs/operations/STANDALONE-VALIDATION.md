# Standalone Validation — BACKEND-101 / BACKEND-102

Date: 2026-09-02
Tasks: BACKEND-101 (replace monorepo path assumptions in active scripts), BACKEND-102 (validate `config.settings.local` against the Docker profile).
Environment: Windows, PowerShell 5.1, Python 3.12 via `uv`. **Docker daemon is DOWN** — no runtime validation against a live PostgreSQL was possible; see the pending-Docker note in section B.

Scope of "active scripts": every file under `Back-End/scripts/` (5 files). `Infra/legacy-monorepo/` was not modified or executed (reference-only per `AGENTS.md`); its path assumptions were classified in `LEGACY-MONOREPO-INVENTORY.md` (BACKEND-090).

## A — BACKEND-101: active-scripts path audit

| Script | Legacy assumptions found | Action taken | Status |
|---|---|---|---|
| `scripts/export_openapi.py` | None. Repo root derived from `Path(__file__).resolve().parents[1]` (`Back-End/`); `apps.api` imports resolve to the new layout. | None. | clean |
| `scripts/verify_openapi_export.py` | None. Same root-relative pattern; invokes `scripts/export_openapi.py` relative to `REPOSITORY_ROOT`. | None. | clean |
| `scripts/seed_e2e_fixtures.py` | Comment-only (line 26): "Shared with `apps/web/qa/e2e/fixtures/credentials.ts`" — a monorepo path. The counterpart file was searched for and is **not present** in `Front-End/public-site` or `Front-End/admin-panel`. | None. No runtime effect; re-pointing the comment to a nonexistent new path would invent a location. | documented |
| `scripts/run_e2e_stack.sh` | **Functional**: `ADMIN_DIST="$CMS_ROOT/../admin/dist"` assumed the monorepo sibling `apps/admin` (ADR-0032); in the new layout `$CMS_ROOT/../admin` does not exist, so every run fails the SPA check. Header and the missing-build hint also referenced `apps/admin`. | `ADMIN_DIST` → `$CMS_ROOT/../Front-End/admin-panel/dist` (the documented ADMIN repository per `PROJECT-MANIFEST.md`; Vite default outDir `dist`, `base: '/admin/'`, build script `tsc -b && vite build` verified in `Front-End/admin-panel`). Header and hint updated. The fail-closed `index.html` check is retained. Evidence: `bash -n` exit 0; `uv run python -m pytest tests/test_e2e_stack_script.py -q` → 3 passed. | fixed |
| `scripts/manual-rebuild.sh` | **Fully monorepo-coupled**: invocation path `apps/cms/scripts/manual-rebuild.sh`, delegation target `infra/deploy/rebuild-static.sh`, build fallback `apps/web`, and `../../..` root math assuming monorepo depth. None of these exist in the standalone repo. | None. The rebuild chain is frontend-owned and the standalone rebuild/deploy contract is an open owner decision (BACKEND-090 inventory; STANDALONE-MIGRATION step 4). Re-pointing it would invent paths; retirement or rewrite needs explicit authorization. | blocked — owner decision |

### Out-of-scope observations (active app code, not scripts — not modified)

- `apps/api/admin_spa.py` — docstring and the 404 build hint reference the monorepo `apps/cms/admin-frontend/dist`; the module-level fallback `BASE_DIR / "admin-frontend" / "dist"` is the legacy in-image path. The supported override `ADMIN_SPA_ROOT` is env-driven (`config.settings.base.py`) and is what `run_e2e_stack.sh` sets. Candidate for a future task with contract/test review.
- `apps/rebuild/services.py:53-62` — `rebuild_script_path()` computes `parents[4] / "infra" / "deploy" / "rebuild-web.sh"`, a monorepo depth assumption that resolves to the wrong root here. It is default-disabled (`REBUILD_TRIGGER_ENABLED=False`), fails closed (missing script → warning, returns `False`), and the standalone rebuild contract is undecided, so behavior was preserved and the finding is flagged for the deployment-contract task.
- `apps/content/management/commands/import_profile_seed.py:43` — help text cites provenance `apps/web/src/data/profile*.ts`; description only, no path lookup.

## B — BACKEND-102: `config.settings.local` validation

### What was checked

- The settings module imports cleanly and passes Django system checks (with and without `DATABASE_URL`).
- Hardcoded default `postgres://taha:taha_local_only@127.0.0.1:15432/taha` matches the documented legacy-parity profile (`docs/operations/LOCAL-DATABASE.md` "Alternative" section; `.env.example` legacy block: "Uses config.settings.local, NOT config.settings.development. Port 15432."). The referenced compose file exists at `Infra/legacy-monorepo/cms/docker-compose.local.yml`.
- The env-driven `DATABASE_URL` override resolves the new-platform Docker profile (`Back-End/docker-compose.dev.yml`, host port 5433, db `taha_platform_dev`, user `taha_dev`) exactly as `LOCAL-DATABASE.md` specifies.
- `ALLOWED_HOSTS = ["localhost", "127.0.0.1", "testserver"]` and `CSRF_TRUSTED_ORIGINS` (ports 4321/5173) match the admin-panel Vite dev server (`vite.config.ts`: `server.port 5173`) and Astro dev conventions.
- No `.env` auto-loader exists in this repository (no dotenv/django-environ dependency; `manage.py` sets only `DJANGO_SETTINGS_MODULE`). `DATABASE_URL` must be exported into the process environment (e.g. `$env:DATABASE_URL = ...`); putting it in `.env` alone has no effect on a bare `manage.py` invocation. The `local.py` docstring now states this.

### Did `local.py` need a change?

**Functionally, no.** The profile selection is already env-driven and the 15432 default is documented, intentional legacy-parity behavior — changing it would alter documented behavior without a task authorizing that. **Comment-only reconciliation applied**: the docstring referenced the monorepo path `infra/cms/docker-compose.local.yml` and did not mention how to select the new-platform profile; it now cites `Infra/legacy-monorepo/cms/docker-compose.local.yml` and points to `DATABASE_URL` + `LOCAL-DATABASE.md`. No runtime behavior changed, so no behavior test was added beyond the BACKEND-101 regression pin.

### Exact commands and results

| # | Command | Result |
|---|---|---|
| 1 | `bash -n scripts/run_e2e_stack.sh` (WSL bash, `/mnt/d/...` path; syntax check only, script not executed) | exit 0 |
| 2 | `uv run python <script>` printing `DATABASES["default"]`, `DJANGO_SETTINGS_MODULE=config.settings.local`, no `DATABASE_URL` in env | `NAME=taha USER=taha HOST=127.0.0.1 PORT=15432` (legacy default) |
| 3 | Same with `$env:DATABASE_URL = 'postgres://taha_dev:taha_dev_local_only@127.0.0.1:5433/taha_platform_dev'` | `NAME=taha_platform_dev USER=taha_dev HOST=127.0.0.1 PORT=5433` (new-platform Docker profile) |
| 4 | `uv run python manage.py check --settings=config.settings.local` (no `DATABASE_URL`) | `System check identified no issues (0 silenced).` — exit 0 |
| 5 | Same with `DATABASE_URL=...5433...` | `System check identified no issues (0 silenced).` — exit 0 |
| 6 | `uv run python manage.py migrate --plan --settings=config.settings.local` with `DATABASE_URL=...5433...` | **exit 1** — `django.db.utils.OperationalError: connection timeout expired` (psycopg could not reach `127.0.0.1:5433`; `MigrationExecutor` needs to read `django_migrations`) |
| 7 | `uv run python -m pytest tests/test_e2e_stack_script.py -q` | 3 passed |
| 8 | `uv run ruff check` / `uv run ruff format --check` on `config/settings/local.py`, `tests/test_e2e_stack_script.py` | All checks passed / 2 files already formatted |

Note on commands 4-5: `check` does not open a database connection, so a passing check proves the settings module is valid, not that a server is reachable.

### Runtime validation against 5433 — pending Docker

Evidence of the blocked environment (captured 2026-09-02):

- `docker version --format '{{.Server.Version}}'` → `failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine ... The system cannot find the file specified.`
- `Test-NetConnection 127.0.0.1` on ports 5432, 5433, 15432 → all `False` (no local PostgreSQL instance is listening either).

Pending when the Docker daemon is available again, per `LOCAL-DATABASE.md`:

1. `docker compose -f docker-compose.dev.yml up -d db` and wait for `taha-platform-dev-db` to report `healthy`.
2. `uv run python manage.py migrate --plan --settings=config.settings.local` with `DATABASE_URL=postgres://taha_dev:taha_dev_local_only@127.0.0.1:5433/taha_platform_dev` — expect exit 0 and a full migration plan on the empty database.
3. Optionally `migrate` + `GET /health/` to confirm the profile end to end.
