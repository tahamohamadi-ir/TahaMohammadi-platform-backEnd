# Local Database — Disposable PostgreSQL (New Platform)

Status: Wave 0 baseline for `Back-End/` development.

## Do not use the legacy stack

| Stack | Host ports | Use for new platform? |
|---|---|---|
| **New platform (this doc)** | **5433** | **Yes — recommended** |
| Legacy `taha-local` (`Infra/legacy-monorepo/cms/docker-compose.local.yml`) | 15432 | No — monorepo reference only |
| Legacy `tahamohamadi-website` (nginx, Next.js, Django, Postgres) | 80, 443, internal Postgres | **No — do not connect** |

> **Warning:** Do **not** point new-platform development at the legacy `tahamohamadi-website` PostgreSQL instance or any of its containers. That database belongs to the old product and is unrelated to Wave 0 work.

## Profile summary

| Setting | Value |
|---|---|
| Host | `127.0.0.1` |
| Host port | `5433` |
| Database | `taha_platform_dev` |
| User | `taha_dev` |
| Password | `taha_dev_local_only` (local throwaway only) |
| Compose file | `Back-End/docker-compose.dev.yml` |
| Django settings | `config.settings.development` with `DATABASE_URL` in `.env` |

Connection URL (copy into `.env`):

```text
DATABASE_URL=postgres://taha_dev:taha_dev_local_only@127.0.0.1:5433/taha_platform_dev
```

## Start the database

From `Back-End/`:

```powershell
docker compose -f docker-compose.dev.yml up -d db
```

Wait until healthy:

```powershell
docker inspect --format '{{.State.Health.Status}}' taha-platform-dev-db
```

Expected: `healthy`.

Stop (keeps data volume):

```powershell
docker compose -f docker-compose.dev.yml down
```

Reset (delete data volume):

```powershell
docker compose -f docker-compose.dev.yml down -v
```

## Django setup

1. Copy `.env.example` to `.env` and uncomment the **recommended** `DATABASE_URL` (port 5433).
2. Install dependencies: `uv sync`
3. Apply migrations:

   ```powershell
   uv run python manage.py migrate --settings=config.settings.development
   ```

4. Verify configuration:

   ```powershell
   uv run python manage.py check --settings=config.settings.development
   ```

5. Run the dev server:

   ```powershell
   uv run python manage.py runserver 127.0.0.1:8000 --settings=config.settings.development
   ```

### Without Docker

If Docker is unavailable, install PostgreSQL 16 locally, create `taha_platform_dev`, and set `DATABASE_URL` to your instance. Use the same database name and a non-production user/password.

### Without PostgreSQL

Omit `DATABASE_URL` from `.env`. `config.settings.development` falls back to SQLite (`dev.sqlite3`) for bare CLI commands and quick checks. PostgreSQL on 5433 is still the recommended path for schema rehearsal and health verification.

## Migration plan (empty database)

Run against a disposable database before first `migrate`:

```powershell
uv run python manage.py migrate --plan --settings=config.settings.development
```

Capture the output in your task handoff when verifying BACKEND-040. On a fresh database, `migrate` should exit 0 with all apps applied.

## Alternative: legacy Docker-local profile (15432)

For monorepo parity only, `config.settings.local` targets `127.0.0.1:15432` via `Infra/legacy-monorepo/cms/docker-compose.local.yml`. That path is **not** the new-platform default. See `.env.example` for the legacy profile block.
