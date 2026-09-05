# BACKEND-200 — Staging Artifact + Rollback Evidence (R7 backend slice)

**Date:** 2026-09-05 (Docker daemon available)
**Artifact:** `taha-platform-dev-api` image, built from `Back-End/Dockerfile`
(python:3.12-slim, uv frozen install, non-root `app` user, WhiteNoise
collectstatic at build, gunicorn CMD). Secrets only via runtime environment —
never baked.
**Status:** local-disposable rehearsal complete. A deployed staging host (TLS
proxy, real domain) remains COORD-060 P2/P4 — owner action.

---

## 1. Artifact build

| Command | Result |
|---|---|
| `docker compose -f docker-compose.dev.yml config --quiet` | OK (no warnings) |
| `docker compose -f docker-compose.dev.yml build api` | `Image taha-platform-dev-api Built` |
| `docker image inspect taha-platform-dev-api` | `sha256:cc5bd6a7ea40d50020409c19b32967bfd42953ae118bfb786cbd17a6547fc459`, created `2026-09-05T05:37:58Z` |

## 2. Disposable stack + fresh-database forward rehearsal

| Command | Result |
|---|---|
| `docker compose -f docker-compose.dev.yml down -v` | volume + network removed (fully fresh DB) |
| `docker compose -f docker-compose.dev.yml up -d db` | `taha-platform-dev-db` healthy (postgres:16-alpine, host port 5433) |
| `showmigrations --list` (empty DB) | **53** unapplied migrations |
| `manage.py migrate` (settings `config.settings.local`, `DATABASE_URL=postgres://taha_dev:taha_dev_local_only@127.0.0.1:5433/taha_platform_dev`) | all applied; final lines `siteconfig.0003… OK` / `siteconfig.0004_sitesettings_seed_policy… OK` |
| `showmigrations --list` after apply | **0** unapplied |

## 3. Rollback rehearsal (forward/reverse/forward)

| Command | Result |
|---|---|
| `manage.py migrate siteconfig 0003` | `Unapplying siteconfig.0004_sitesettings_seed_policy... OK` |
| `manage.py migrate siteconfig` | `Applying siteconfig.0004... OK` |
| `showmigrations siteconfig` | 0001–0004 all `[X]` |

Rollback command shape for real staging: `manage.py migrate <app> <previous>` —
verified here on the disposable profile. `migrate --plan` before any deploy run
remains the pre-deploy gate (`Docs/08-operations/DEPLOYMENT-RUNBOOK.md`).

## 4. Stack bring-up + `/health/` acceptance

| Command | Result |
|---|---|
| `docker compose up -d` (api + db) | both containers running; api bound `127.0.0.1:18010→8000` |
| `GET http://127.0.0.1:18010/health/` | **200** `{"status": "ok", "db": "ok", "contact": "ok"}` |
| `compose down` → `compose up -d` (named volume preserved) | re-up **200** `{"status": "ok", "db": "ok", "contact": "ok"}` — stack survives full recycle |

## 5. Environment defects found and fixed during the rehearsal

1. **Compose/`.env` DB mismatch:** the tracked `.env` (untracked local file) set
   `POSTGRES_DB=taha_cms`, which overrode the compose default
   `taha_platform_dev`; the container targeted a database the disposable
   Postgres never created (`FATAL: database "taha_cms" does not exist`). Fixed
   by aligning `.env` to the recommended disposable profile per the file's own
   comment (`LOCAL-DATABASE.md`).
2. **Compose did not pass `EMAIL_HOST`/`EMAIL_PORT`:** with the seed policy
   enabling the contact form, `/health/` answered `degraded … contact form
   enabled but EMAIL_HOST not configured`. Fixed in
   `docker-compose.dev.yml` by passing `EMAIL_HOST`/`EMAIL_PORT` from the
   environment (base.py already reads them; defaults preserve fail-honest 503
   behavior when unset).
3. **`DJANGO_SECRET_KEY` only lived in the operator shell:** a `--force-recreate`
   without it crashed workers (`ImproperlyConfigured`) — production settings
   fail closed, as designed. The disposable value now lives in the untracked
   local `.env` (clearly marked dev-only; production keeps requiring a real
   secret).

## 6. Honest boundary

This proves the **artifact + migration forward/rollback + stack lifecycle** on
the disposable local profile. It does **not** prove: deployed staging behind
the TLS reverse proxy (COORD-060 P2/P4), browser smoke (COORD-070 families
1–7), or the backup/restore drill's accepted-restore criterion — those need the
deployed staging host and remain owner/ops actions. `PUBLIC-320` /
`ADMIN-300` still require `PUBLIC_STAGING_SITE_URL`.
