# Admin Endpoint Reconciliation vs Admin-Panel Workflow Map (BACKEND-120)

Evidence sources:

- Consumer map (read-only, other repository):
  `Front-End/admin-panel/docs/architecture/WORKFLOW-API-MAP.md` (8 workflow rows)
- Accepted admin OpenAPI artifact:
  `docs/contracts/openapi/current/admin-openapi.json` (version `0.1.0`, 47 paths /
  63 operations, SHA-256 `1328f8244c5541f225648082891a0a1244961c0dead6692488992ac8c7606f09`)
- Artifact provenance: `docs/contracts/openapi/current/PROVENANCE.json`
  (generated 2026-08-29, sourceCommit `82e3984520154b60146009ae4a0d21eb5c30373e`,
  access evidence `tests/test_admin_openapi.py` — staff-plus-OTP fixture)
- Runtime URL wiring: `config/urls.py`, `apps/security/urls.py`, `apps/content/urls_staff.py`
- API source: `apps/api/admin_api.py` plus routers `admin_content.py`,
  `admin_media.py`, `admin_media_ext.py`, `admin_composition.py`,
  `admin_graph.py`, `admin_health.py`, `admin_home.py`, `admin_mfa.py`,
  `admin_siteconfig.py`, `admin_timeline.py`; guards in `apps/api/admin_common.py`;
  pure validator `apps/api/admin_graph_validate.py` (no HTTP routes)

Reconciliation date: 2026-09-02. No schema, endpoint, or code changes were made
by this task. The workflow map is an implementation gate, not an endpoint
inventory, so coverage is judged per workflow row, with endpoint-level gaps
recorded separately.

## Artifact parity check (2026-09-02)

A decorator sweep of the eleven router files plus `register_media_ext` counts
exactly 63 route operations, matching the accepted artifact's 63 operations
operation-for-operation (methods and paths). No drift between the accepted
artifact and current source was detected. The legacy Django surfaces below
(`apps/content/admin_api.py`, `/staff/` HTML) are outside the Ninja API and
intentionally absent from the artifact.

## Actual admin surface (source-derived)

Mounted at `/api/v1/admin/` (`config/urls.py:25`) by `admin_api.py`:

| Group | File (mount) | Operations |
|---|---|---|
| Auth | `admin_api.py` (root) | GET `/auth/csrf`; POST `/auth/login`, `/auth/logout`; GET `/auth/me`; GET `/dashboard/summary` |
| MFA | `admin_mfa.py` (`/auth/mfa`) | GET `/status`, `/recovery-codes`; POST `/confirm`, `/regenerate`, `/disable` |
| Content | `admin_content.py` (`/content`) | GET `/schema`, `/{entity}`, `/{entity}/{id}`, `/{entity}/{id}/revisions`, `/project/{id}/case-media`; POST `/{entity}`, `/{entity}/bulk-archive` (feature-flagged), `/{entity}/{id}/transition`, `/{entity}/{id}/revisions`, `/{entity}/{id}/revisions/{revision_id}/restore`, `/{entity}/{id}/preview-link`; PUT `/{entity}/{id}`, `/project/{id}/diagrams/{diagram_id}`, `/project/{id}/screenshots/{screenshot_id}` |
| Media | `admin_media.py` (`/media`) | GET ``, `/orphans`, `/{media_id}`; POST ``, `/{media_id}/replace`; PUT `/{media_id}` |
| Media ext | `admin_media_ext.py` (root group, registered before `/{media_id}`) | GET `/media/licenses`; PATCH `/media/{media_id}/presentation` |
| Composition | `admin_composition.py` (`/composition`) | GET ``, `/schema`, `/{page_id}`; POST ``; PUT `/{page_id}` |
| Overview | `admin_health.py` (`/overview`) | GET `/translation-queue`, `/content-health` |
| Site config | `admin_siteconfig.py` (root) | GET/PUT `/site`; GET/POST `/tags`, PUT/DELETE `/tags/{id}`; GET/POST `/featured`, PUT/DELETE `/featured/{id}` |
| Home modules | `admin_home.py` (`/home-modules`) | GET/PUT `/{locale}`; POST `/{locale}/validate` |
| Timeline | `admin_timeline.py` (`/timeline`) | GET/POST `/{locale}`; POST `/{locale}/reorder`; PATCH/DELETE `/{locale}/{id}` |
| Graph | `admin_graph.py` (`/graph`) | GET/POST `/versions`; GET `/versions/{version_id}`; PUT `/versions/{version_id}/payload`; POST `/versions/{version_id}/activate`; GET `/validation/{version_id}` |

`admin_graph_validate.py` is a pure validator service consumed by
`admin_graph.py`; it exposes no HTTP routes.

Legacy Django surfaces outside `/api/v1/admin/` (present in `config/urls.py`,
absent from the accepted artifact):

- `GET/PUT /api/admin/profiles/<locale>/<slug>` (`apps/content/admin_api.py:admin_profile_detail`)
  and `POST /api/admin/profiles/<locale>/<slug>/siblings/<target_locale>`
  (`admin_profile_create_sibling`) — function views with a local
  staff+OTP guard (`_require_admin_session`), `If-Match` revision check on PUT,
  and Django middleware CSRF (no `csrf_exempt` on these views).
- `/staff/` HTML (ADR-0026 fallback; `LOGIN_URL = "/staff/login/"`,
  `config/settings/base.py:76`): login, logout, and
  `account/two-factor/{,qrcode/,recovery-codes/,regenerate/,disable/}`
  (`apps/security/urls.py`); staff home redirect,
  `preview/<kind>/<pk>/`, `profiles/`, `profiles/<locale>/<slug>/`
  (`apps/content/urls_staff.py`).
- `/admin/` React SPA catch-all (`serve_admin_ui`) — page serving, not an API route.
- `/rebuild-trigger/` — POST-only, HMAC-signed, `csrf_exempt` machine-to-machine
  view (`apps/rebuild/views.py`), loopback/infra scoped, outside the admin API.

## Coverage map

| Workflow-map row | Backend endpoint(s) | Status |
|---|---|---|
| Sign-in, MFA, re-authentication | `/auth/csrf`, `/auth/login`, `/auth/logout`, `/auth/me`, `/auth/mfa/*` (5), plus `/dashboard/summary` post-auth counts | Covered (guard nuances G-H, timeout evidence G-I) |
| Content create/edit/revision | `/content/schema`, `/{entity}` GET/POST, `/{entity}/{id}` GET/PUT, `/transition`, `/revisions` GET/POST, `/revisions/{id}/restore`; support: preview-link, bulk-archive, project case-media/diagrams/screenshots | Covered (support endpoints unnamed by the map — G-D) |
| Media | `/media` GET/POST, `/orphans`, `/{media_id}` GET/PUT, `/{media_id}/replace`, `/media/licenses`, `/{media_id}/presentation` | Covered except deletion — gap G-E |
| Home modules | `/home-modules/{locale}` GET/PUT, `/{locale}/validate` | Covered |
| Timeline | `/timeline/{locale}` GET/POST, `/{locale}/reorder`, PATCH/DELETE `/{locale}/{id}` | Covered |
| Research graph | `/graph/versions` GET/POST, `/versions/{id}` GET, `/payload` PUT, `/activate` POST, `/validation/{id}` GET | Covered; public-preview parity evidence not satisfiable — gap G-F |
| Profile/site settings | `/site` GET/PUT, `/tags` CRUD, `/featured` CRUD; profile editing via `/content/profile` entity | Covered; sibling translation only on legacy surface — gap G-G; tags/featured unnamed by the map — G-C |
| Rebuild/health — excluded from admin v1 | `/overview/content-health`, `/overview/translation-queue` exist (read-only, OTP); `/rebuild-trigger/` is infra, outside the admin API | Honored — no admin rebuild surface; overview endpoints have no map row (G-A) |

Row tally: 5 rows fully covered (sign-in/MFA, content, home modules, timeline,
rebuild/health-exclusion), 3 rows covered with gaps (media, graph,
profile/site settings). The workflow map names no methods or paths, so no
method-level mismatch exists between map and source; the gaps below are
capability-level.

## Gap list

### Backend endpoints with no workflow-map row

- **G-A — `/overview/translation-queue` + `/overview/content-health`.** Read-only
  OTP-guarded summaries (`admin_health.py`). Map row 8 excludes "rebuild/health"
  from admin v1 ("Not rendered in the admin client"), and no row covers the
  translation queue. Informational: additive backend surface; whether the admin
  client may render it is an admin-repository decision.
- **G-B — `/dashboard/summary`.** Serves the post-sign-in dashboard counts; no
  map row names it (row 1 covers the session contract only). Informational.
- **G-C — siteconfig `/tags` and `/featured` CRUD (8 operations).** Row 7
  ("Profile/site settings") does not name topic tags or featured spotlights.
  Informational: map-row coverage is an admin-repository call; the endpoints are
  in the accepted artifact.
- **G-D — content support endpoints without explicit map rows.** `/content/schema`,
  project case-media + diagram/screenshot Media-FK PUTs, preview-link,
  feature-flagged bulk-archive, `/composition/schema`. They serve rows 2's
  create/edit/revision workflow broadly. Informational.

### Workflow-map rows referencing capabilities the backend does not have

- **G-E — media deletion (row 3 "usage/deletion rules") — RESOLVED 2026-09-02.**
  Owner decision (DECISION-LOG): add the endpoint. `DELETE /api/v1/admin/media/{id}`
  now exists (staff+OTP+CSRF; 409 `MEDIA_IN_USE` with `usageCount` when referenced;
  audit `media.delete`), accepted into the artifact via the 2026-09-02 PS-05
  amendment (`Docs/03-contracts/OPENAPI-ACCEPTANCE.md`). Admin-panel delete UX is
  unblocked.
- **G-F — graph "public preview parity" (row 6 acceptance evidence).** The admin
  graph surface is complete, but the public graph read (BK-05) has not shipped:
  `admin_graph.py` module docstring ("the public read gate (BK-05, not shipped
  yet)") and the SYNC-GUARD duplicating `edge_public_id`
  (`admin_graph_validate.py:65-73`). Final graph-editor acceptance evidence
  (parity with the public preview) is impossible today. **Blocks graph-workflow
  acceptance, not editor development; backend dependency on BK-05.**
- **G-G — profile sibling-locale creation — RESOLVED 2026-09-02.** Owner decision
  (DECISION-LOG): migrate to the accepted admin surface.
  `POST /api/v1/admin/content/profile/{id}/sibling-locale` now exists (staff+OTP+
  CSRF; stable codes `VALIDATION`/`DUPLICATE`; audit `admin.profile.sibling_created`),
  accepted into the artifact via the same PS-05 amendment. The legacy
  `POST /api/admin/profiles/{locale}/{slug}/siblings/{target_locale}` remains
  untouched for compatibility and follows the approved BACKEND-151 deprecation
  plan (remove via separate ADR after consumer cutover evidence).

### Verifiable method/permission observations (no map conflict, recorded for the client)

- **G-H — MFA enrollment guards are two-stage.** `/auth/mfa/status` and
  `/auth/mfa/confirm` require staff session only (`_require_staff_session`,
  `admin_mfa.py:83,103`) — necessary because enrollment precedes a verified OTP
  session. `/auth/mfa/regenerate`, `/auth/mfa/disable`, and
  `/auth/mfa/recovery-codes` require full staff+OTP (`_require_admin_otp`,
  `admin_mfa.py:122,132,143`). The client must not assume OTP enforcement on
  all MFA calls. Informational.
- **G-I — session "timeout" (row 1) has no explicit backend override.** No
  `SESSION_COOKIE_AGE` setting exists in `config/settings/`; Django's default
  session age applies. Production sets cookie flags only
  (`SESSION_COOKIE_SECURE/HTTPONLY/SAMESITE`, `config/settings/production.py:71-82`).
  The auth acceptance test needs an owned timeout contract (setting or
  documented default). Informational; contract clarification only.

### Legacy surface status (record-only; decisions belong to BACKEND-151)

- **`/api/admin/profiles/...`** — two function views (detail GET/PUT, sibling
  POST), staff+OTP guarded locally, `If-Match` revision on PUT, richer profile
  projection than the generic `/content/profile` entity surface. Outside the
  Ninja API and the accepted artifact. Sibling creation now has an accepted
  replacement (G-G, 2026-09-02); the legacy surface stays per the approved
  BACKEND-151 deprecation plan until consumer cutover evidence.
- **`/staff/` HTML** — login/logout, TOTP enrollment fallback (setup, QR code,
  recovery-code reveal, regenerate, disable), content preview, and the legacy
  profile editor pages. It remains the `LOGIN_URL` target
  (`config/settings/base.py:76`). No workflow-map row exists for it (the SPA is
  the primary UX). BACKEND-151 owns the deprecation plan.
- **`/rebuild-trigger/`** — HMAC-signed infra route outside the admin API;
  consistent with map row 8's "infrastructure-only operational control".

## Verified against backend behavior

Guards live in `apps/api/admin_common.py` and are enforced server-side on every
admin route; `Authorization`-free same-origin session + CSRF + TOTP is the
baseline (ADR-0026):

- `_check_csrf` (`admin_common.py:104`) — called by every unsafe method: content
  POST/PUT (create, update, transition, revisions create/restore, bulk-archive,
  diagram/screenshot PUTs, preview-link), media POST/PUT/replace, media-ext
  PATCH, composition POST/PUT, siteconfig POST/PUT/DELETE (site, tags,
  featured), MFA POSTs, home-modules PUT/validate, timeline POST/PATCH/DELETE
  (including reorder), graph POST/PUT. Ninja views are CSRF-exempt at the
  middleware level, so this explicit check is the enforcement point
  (`admin_common.py:104-118`).
- `_require_admin_otp` (`admin_common.py:129`) — chains
  `_require_staff_session` plus the `otp_device` verification check; used by
  every read and write in `/content`, `/media` (via `_apply_filters`,
  `admin_media.py:262`), media-ext, `/composition`, `/overview`, siteconfig,
  `/home-modules`, `/timeline`, and `/graph`. Only the deliberate exceptions
  above (`/auth/csrf` unguarded token bootstrap, `/auth/login` CSRF+rate-limit,
  `/auth/logout` CSRF, `/auth/me` staff session, `/auth/mfa/status` +
  `/auth/mfa/confirm` staff session) differ (G-H).
- Login rate limiting — 5 attempts / 300 s per IP with audit rows
  (`admin_api.py:53-55,133-144`); login success/failure/blocked outcomes are
  audited (`admin_api.py:111-131`).
- Optimistic locking — `_require_if_match` (`admin_common.py:192`, 428
  PRECONDITION_REQUIRED / 409 STALE_REVISION) guards media-ext presentation,
  home-modules PUT, timeline PATCH/DELETE, and graph payload PUT; content,
  media, composition, and siteconfig PUTs compare `If-Match` under a
  `select_for_update` row lock and raise 409 `CONFLICT` with
  `currentUpdatedAt` (per-module handlers registered on `admin_api`).
- Graph immutability and validation gates — PUT payload rejects non-draft
  versions with 409 `IMMUTABLE_ACTIVE` (`admin_graph.py:487-492`); validator
  issues reject atomically (400 on PUT, 409 `VALIDATION_BLOCKED` on activate);
  activate re-validates and archives the previous active version of the locale.
- Acceptance-flow note: server-enforced behavior above is the authority; the
  workflow map's "exact methods and schemas come only from accepted
  authenticated admin OpenAPI artifacts" is satisfied by the accepted artifact,
  which matches source operation-for-operation (see parity check).
