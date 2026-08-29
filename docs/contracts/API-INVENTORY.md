# API Inventory

Verified top-level routes from `config/urls.py`:

| Prefix | Current role | Migration status |
|---|---|---|
| `/health/` | Health | Preserve |
| `/media/<path>` | Public media | Preserve and harden |
| `/preview/` | Public preview shares | Preserve and verify |
| `/api/` | Django Ninja public API | Canonical public surface pending snapshot |
| `/api/profiles/...` | Public profile endpoints | Compatibility surface |
| `/api/v1/admin/` | Admin Ninja API | Canonical admin surface |
| `/api/admin/profiles/...` | Older admin profile API | Deprecation decision required |
| `/staff/` | Django auth/MFA/staff fallback | Keep through cutover |
| `/admin/` | Legacy React SPA serving | Remove only after new admin cutover |
| `/rebuild-trigger/` | Guarded rebuild integration | Rework with standalone operations |

Registered admin capability routers are content, media, composition, overview, site configuration, MFA, home modules, timeline, and graph. Individual methods and schemas must be exported from versioned public and authenticated-admin OpenAPI artifacts or verified source before frontend implementation. The artifact contract, required test fixture, commit provenance, and consumer generation rules are central in `../../../Docs/03-contracts/OPENAPI-ARTIFACT-CONTRACT.md`.

## Source-generated review snapshots

Run `python scripts/export_openapi.py` with `DJANGO_SETTINGS_MODULE=config.settings.development` to create deterministic review snapshots under `docs/contracts/openapi/current/`. Their `PROVENANCE.json` records paths, hashes, source commit, settings module, and the remaining endpoint-access evidence. These snapshots are explicitly **unaccepted**: they do not close the anonymous-public or verified-staff-plus-OTP checks required before either frontend generates types or relies on a field.
