# Backup and Restore — New Platform (Disposable Profile)

Status: BACKEND-160. Scope: the **new platform only** — PostgreSQL
`taha_platform_dev` on host port **5433** (compose service `db`, container
`taha-platform-dev-db`, file `Back-End/docker-compose.dev.yml`) and the media
directory **`Back-End\media`** (`MEDIA_ROOT` in `config/settings/base.py`).
Not scoped: the legacy monorepo stacks; their tooling stays reference-only
(see the last section). Production restore commands are added only after the
new infrastructure paths are accepted, per
`Docs/08-operations/BACKUP-RESTORE-RUNBOOK.md`.

## Relation to the legacy backup logic

`Infra/legacy-monorepo/backup/taha-platform-backup.sh` (reference-only,
unchanged) was read and its logic is re-used here with new-platform paths,
ports, and names:

| Legacy logic (verified in the script) | New-platform mapping (this doc) |
|---|---|
| Fail-closed preflight: required env (`RCLONE_CONFIG`, `RESTIC_PASSWORD_FILE`, `RESTIC_REPOSITORY`) must be set; required inputs must be readable; abort when the DB container is not running | Same preflight idea: verify `taha-platform-dev-db` is running and healthy before dumping; credentials come only from the environment — no secret is embedded in any command below |
| Single-instance `flock` lock and a `--dry-run` inventory mode | Carried over as requirements for any future backup script; no script is created by this task |
| DB dump streamed out of the container: `docker exec taha-cms-db-1 sh -ceu 'exec pg_dumpall -U "$POSTGRES_USER"'` into restic via stdin | `pg_dump` **custom format** of the single new database: `docker exec taha-platform-dev-db pg_dump -U taha_dev -d taha_platform_dev -Fc` written to a file. `-Fc` replaces plain `pg_dumpall` because the disposable profile is one database and `-Fc` supports compressed, selective restore |
| Optional second dump of the legacy pre-CMS stack while its container existed | Dropped — legacy stacks are out of scope and must not be touched |
| File backup of the media volume directory, Caddyfile, and compose files | Media directory copy (`Back-End\media`); config is repo-tracked (`docker-compose.dev.yml`, `Dockerfile`, `.dockerignore`), so Git is its backup — record the commit SHA with each backup; `.env` holds secrets and goes only to the owner's secret store, never into generic backups |
| Retention: `restic forget --keep-daily 7 --keep-weekly 4 --keep-monthly 12 --prune` | Same cadence carried over as guidance once a backup backend exists; the restic/rclone repository and credential paths are owner decisions and are not invented here |

No backup storage backend (restic/rclone) is created or configured by this
task; the legacy systemd unit/timer shape (`taha-platform-backup.service`/
`.timer`, daily 03:20 UTC) is the reference pattern for a future rewrite.

## Backup

Run from `Back-End/`. Keep dump output out of Git — never commit dumps or
media copies.

1. Preflight — the db container must be running and healthy:

   ```powershell
   docker inspect --format '{{.State.Health.Status}}' taha-platform-dev-db   # expect: healthy
   ```

2. Database dump (custom format, timestamped). The archive is binary, so it is
   written inside the container and copied out with `docker cp` — piping it
   through PowerShell would corrupt it:

   ```powershell
   New-Item -ItemType Directory -Force -Path backup | Out-Null
   $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
   docker exec taha-platform-dev-db pg_dump -U taha_dev -d taha_platform_dev -Fc -f "/tmp/taha_platform_dev-$stamp.dump"
   docker cp "taha-platform-dev-db:/tmp/taha_platform_dev-$stamp.dump" "backup/taha_platform_dev-$stamp.dump"
   docker exec taha-platform-dev-db rm "/tmp/taha_platform_dev-$stamp.dump"
   ```

3. Media directory copy (`media/` is `MEDIA_ROOT`):

   ```powershell
   Copy-Item -Recurse -Path media -Destination "backup/media-$stamp"
   ```

   Note: a compose `api` container writes uploads inside the container unless
   a mount is added; `Back-End\media` on the host is what host-side runs
   (runserver) and this backup use.

4. Config inventory: `docker-compose.dev.yml`, `Dockerfile`, `.dockerignore`,
   and `docs/` are tracked in Git — record the current commit SHA with the
   backup. `.env` contains secrets: copy it only into the owner's secret
   store, never into `backup/`.

5. Retention guidance (carried over from the legacy script): keep 7 daily,
   4 weekly, 12 monthly snapshots once a backup backend is chosen.

## Restore

Target: the disposable profile (db `taha_platform_dev` on host port 5433).
The steps below **destroy the current `taha_platform_dev` database** — verify
the target is disposable first. Run from `Back-End/`.

1. Start only the db service and wait for healthy:

   ```powershell
   docker compose -f docker-compose.dev.yml up -d db
   docker inspect --format '{{.State.Health.Status}}' taha-platform-dev-db   # expect: healthy
   ```

2. Drop and recreate the database:

   ```powershell
   docker exec taha-platform-dev-db psql -U taha_dev -d postgres -c "DROP DATABASE IF EXISTS taha_platform_dev;"
   docker exec taha-platform-dev-db psql -U taha_dev -d postgres -c "CREATE DATABASE taha_platform_dev OWNER taha_dev;"
   ```

3. Restore the dump:

   ```powershell
   docker cp "backup/taha_platform_dev-<stamp>.dump" taha-platform-dev-db:/tmp/restore.dump
   docker exec taha-platform-dev-db pg_restore -U taha_dev -d taha_platform_dev --no-owner --exit-on-error /tmp/restore.dump
   docker exec taha-platform-dev-db rm /tmp/restore.dump
   ```

4. Restore media: copy the backed-up `backup/media-<stamp>` contents back into
   `Back-End\media`.

5. Post-restore checks:
   - No pending migrations against the restored schema:

     ```powershell
     uv run python manage.py migrate --plan --settings=config.settings.development
     ```

     Expect `No planned migration operations.`
   - Application health: start the API (`docker compose -f
     docker-compose.dev.yml up -d api` with `DJANGO_SECRET_KEY` set in
     `.env`) or the host dev server (`uv run python manage.py runserver
     127.0.0.1:8000 --settings=config.settings.development`), then request
     `http://127.0.0.1:18010/health/` (compose) or
     `http://127.0.0.1:8000/health/` (runserver). Expect `"status": "ok"` and
     `"db": "ok"`.

## Restore-drill checklist (NOT yet drilled)

Per the workspace runbook, a backup is accepted only when it restores into an
isolated environment. This drill has **not** been executed yet; the evidence
gate is **R7** (staging execution).

- [ ] Dump taken with the Backup section commands (size recorded).
- [ ] Restore executed into a freshly recreated disposable `taha_platform_dev`.
- [ ] `pg_restore` exits 0 with `--exit-on-error`.
- [ ] Media restored into `Back-End\media`; file count matches the source.
- [ ] `manage.py migrate --plan` reports no planned operations.
- [ ] `/health/` returns `"status": "ok"` and `"db": "ok"`.
- [ ] Drill evidence (commands + output) recorded against R7.

## Legacy reference

`Infra/legacy-monorepo/backup/` (backup script, systemd unit/timer, env
template, README) remains **reference-only**: no file there is modified, and
its monorepo paths and names (`taha-cms-db-1`, `taha-cms_cms_media`,
`/etc/taha-backup.env`, the rclone/restic repository locations) must not be
reused for the new platform.
