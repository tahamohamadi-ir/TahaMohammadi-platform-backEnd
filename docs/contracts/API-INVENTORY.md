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

Registered admin capability routers are content, media, composition, overview, site configuration, MFA, home modules, timeline, and graph. Individual methods and schemas must be exported from live OpenAPI or verified source before frontend implementation.
