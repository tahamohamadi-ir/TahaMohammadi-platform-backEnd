# Taha Mohammadi Platform — Backend

<!-- PRODUCT-V2.1 -->
Current execution target: research-first bilingual portfolio, independently publishable detail pages and broad CMS editing under ADR-0010. Dispatch only this repository's packets from `../Docs/05-delivery/concept-alignment-v2/EXECUTION.md` (paths here are repository-relative). Older scaffold/phase status below is a dated baseline, not current feature acceptance. Preserve current endpoints until the additive target contract is implemented and exported.
<!-- /PRODUCT-V2.1 -->

Independent Django 5.2 and Django Ninja backend for the platform. The usable backend was copied from `D:\Project\Taha-personal-platform\apps\cms` at source commit `cdaa283fac9da57c6d88e22aa0751be6214b6cf6`. All 198 tracked backend files were verified against the source with SHA-256 before this repository's development baseline was created.

## Status

- Application source, migrations, tests, lock file, and scripts: migrated.
- Legacy monorepo infrastructure: preserved under `Infra/legacy-monorepo/` as reference; paths are not yet standalone-safe.
- Current behavior: intentionally preserved for baseline verification.
- Future work: continue development and debugging in this repository, not the old monorepo.

## Runtime

- Python `>=3.12,<3.13`
- Django `5.2.9`
- Django Ninja `1.6.2`
- PostgreSQL in production; local settings support a local database URL/default.
- Dependencies and development tools are locked by `uv.lock`.

## Local baseline

```powershell
uv sync --frozen
uv run python manage.py migrate --settings=config.settings.local
uv run python manage.py runserver 127.0.0.1:18000 --settings=config.settings.local
```

Run checks with:

```powershell
uv run ruff check .
uv run pytest
```

Read [AGENTS.md](AGENTS.md), [PROJECT-MANIFEST.md](PROJECT-MANIFEST.md), and [docs/architecture/ARCHITECTURE.md](docs/architecture/ARCHITECTURE.md) before changing behavior.
