# Legacy Route Deprecation Plan

Status: Approved by owner (chat instruction, 2026-09-02).

> This document plans deprecation; it removes nothing and changes no route.
> It invents no dates: every phase is gated on evidence, and each removal is
> executed only via a separately accepted architecture decision record, per
> `Docs/00-governance/AUTHORITY-ORDER.md`. `Back-End/docs/contracts/API-INVENTORY.md`
> remains the normative route inventory.

## Verified prefix baseline (from `config/urls.py`)

| Prefix | Wiring | API-INVENTORY migration status |
|---|---|---|
| `/preview/` | `apps.content.urls_public_preview` | Preserve and verify |
| `/health/` | `apps.health.views.health` | Preserve |
| `/media/<path:name>` | `apps.media.views.serve_public_media` | Preserve and harden |
| `/admin/` and `/admin/<path:spa_path>` | `apps.api.admin_spa.serve_admin_ui` | Remove only after new admin cutover |
| `/staff/` | `apps.security.urls` + `apps.content.urls_staff` | Keep through cutover |
| `/api/v1/admin/` | Django Ninja `admin_api` | Canonical admin surface |
| `/api/profiles/...` | `apps.content.public_api` | Compatibility surface |
| `/api/admin/profiles/...` | `apps.content.admin_api` (detail GET/PUT, siblings POST) | Deprecation decision required |
| `/api/` | Django Ninja public API | Canonical public surface pending snapshot |
| `/rebuild-trigger/` | `apps.rebuild.views.rebuild_trigger` | Rework with standalone operations |

This plan covers the three route families flagged for deprecation decisions:
`/admin/` SPA serving, `/staff/` HTML, and `/api/admin/profiles/...`.
`/rebuild-trigger/` has its own inventory status ("Rework with standalone
operations") and is out of scope here.

## Route family 1 — `/admin/` legacy SPA serving

### Current role

`apps/api/admin_spa.py::serve_admin_ui` serves the built React admin SPA over
`GET` under `/admin/` and `/admin/<path:spa_path>`:

- The SPA root comes from `settings.ADMIN_SPA_ROOT` when configured; the
  default is the legacy in-image path `BASE_DIR / "admin-frontend" / "dist"`
  (S1 note referencing ADR-0032: the SPA source moved to `apps/admin`;
  in the Docker image the dist is baked in by the `Dockerfile.cms`
  `frontend-builder` stage).
- Path traversal is blocked by resolving the requested path and requiring it
  to stay inside the SPA root; violations raise `Http404`.
- Any request that does not map to a real file falls back to
  `index.html` (SPA client-side routing).
- A missing build fails loudly with a 404 and a build hint.
- Responses carry `X-Robots-Tag: noindex, nofollow, noarchive` and
  `Cache-Control: no-store`.

### Consumers

- Whatever staff currently reach by browsing `/admin/` (the served SPA and
  its client-side routes, e.g. the `/admin/content/profile/{pk}` editor
  routes that `/api/admin/profiles/...` sibling creation returns as
  `editorUrl` — see route family 3).
- `/staff/` logout redirects to `next_page="/admin/login/"`
  (`apps/security/urls.py`), so the login/MFA fallback currently lands users
  on the `/admin/` SPA login route. This is a recorded coupling that must be
  resolved before `/admin/` removal.

### Deprecation preconditions

All must hold before removal:

1. **Admin-panel cutover evidence:** the new `Front-End/admin-panel` SPA is
   deployed and served same-origin per `Docs/02-architecture/DEPLOYMENT-TOPOLOGY.md`
   (proxy serves admin static SPA routes/assets; no CORS), with the browser
   evidence the topology document requires (sign-in, MFA, session expiry,
   CSRF failure, admin mutation) recorded.
2. **Coupling resolution:** `/staff/` logout no longer targets
   `/admin/login/` (or a new accepted login route), and no remaining backend
   payload (e.g. `editorUrl`) points into `/admin/**`.
3. **Consumer evidence:** no workflow, bookmark contract, or integration
   depends on the in-image SPA being served by Django.
4. **`ADMIN_SPA_ROOT` decision:** the serving path (proxy-served volume vs
   baked copy vs removal) is settled in the accepted ADR; this plan does not
   choose it.

### Planned phases (evidence-gated, no dates)

1. **Announce:** record in `API-INVENTORY.md` and owner-facing notes that
   `/admin/` serving is planned for removal after admin-panel cutover; no
   behavior change.
2. **Observe:** while admin-panel is built and staged, verify nothing new
   depends on `/admin/` serving (route traffic, integration checks, and the
   coupling list above). Any new dependency found resets this phase.
3. **Remove:** via a separate accepted ADR that supersedes the relevant part
   of ADR-0026/ADM-1 cutover assumptions, updates `API-INVENTORY.md`, and
   carries the cutover evidence from precondition 1.

## Route family 2 — `/staff/` Django staff HTML

### Current role

Two includes under `/staff/` (`config/urls.py`):

- `apps/security/urls.py`: Django login with the `OTPLoginForm` MFA form
  (`/staff/login/`), logout (`/staff/logout/`, redirecting to
  `/admin/login/`), and TOTP account management pages — setup, QR code,
  recovery-code reveal, regenerate, disable.
- `apps.content/urls_staff.py`: `/staff/` entry redirect to the profile
  index, staff-only content preview shares
  (`/staff/preview/<kind>/<pk>/`), and the legacy HTML profile editor pages
  (`/staff/profiles/`, `/staff/profiles/<locale>/<slug>/`).

Per `Docs/03-contracts/AUTH-CONTRACT.md`, same-origin session authentication
with CSRF and MFA is the accepted baseline, and `/staff/` is the login/MFA
fallback. `API-INVENTORY.md` status: **"Keep through cutover"**.

### Consumers

- Staff sign-in and MFA enrollment/recovery flows that are not yet (or not
  fully) provided by the new admin API/admin-panel.
- Content preview share URLs used by the staff preview workflow.

### Deprecation preconditions

1. The new admin-panel cutover covers sign-in, MFA setup/recovery, session
   expiry, and logout with browser evidence (the topology document's required
   browser tests).
2. Every preview consumer of `/staff/preview/...` has an accepted replacement
   or an explicit owner decision that the legacy preview route remains.
3. The legacy HTML profile editor at `/staff/profiles/...` has no remaining
   workflow role after cutover, or the owner decides to keep it (a decision,
   not an assumption).
4. `API-INVENTORY.md` status is changed by an accepted decision, not by this
   document.

### Planned phases (evidence-gated, no dates)

1. **Announce:** after admin-panel cutover evidence exists, record the intent
   to retire specific `/staff/` sub-paths (not the whole prefix at once —
   login/MFA fallback and preview are separable).
2. **Observe:** confirm through usage evidence which sub-paths are still
   hit; the MFA fallback must remain functional while any account is not
   enrolled through the new surface.
3. **Remove:** per sub-path family, via a separate accepted ADR, keeping
   `/staff/login/` + TOTP fallback until the accepted decision explicitly
   retires it. `/staff/` is never removed silently or as a side effect of
   `/admin/` removal.

## Route family 3 — `/api/admin/profiles/...` legacy admin profile API

### Current role

Two Django views in `apps/content/admin_api.py`, wired in `config/urls.py`:

- `GET/PUT /api/admin/profiles/{locale}/{slug}` — serialized profile
  detail (`status`, `revision`, `translationStatus` added on top of the
  public serializer) with If-Match revision handling (`428
  PRECONDITION_REQUIRED`, `409 REVISION_CONFLICT` with `currentRevision`).
- `POST /api/admin/profiles/{locale}/{slug}/siblings/{target_locale}` —
  creates the sibling-locale profile draft and returns `editorUrl`
  (`/admin/content/profile/{pk}`) pointing into the `/admin/` SPA.

Auth guard: session + staff + OTP, error shape `{code, detail, **extras}`
(see `Docs/03-contracts/ERROR-COMPATIBILITY-MATRIX.md` and ADR-0006 for the
envelope relationship).

`API-INVENTORY.md` status: **"Deprecation decision required"** — the decision
itself is not made by this document.

### Consumers

- The legacy in-image admin SPA whose editor routes this API's `editorUrl`
  targets (`/admin/content/profile/{pk}`, served by the `/admin/` catch-all).
  This is the recorded coupling that makes this API and `/admin/` serving a
  single deprecation unit unless evidence separates them.
- Whether `Front-End/admin-panel` reads any of these endpoints is a
  cross-repository question this plan cannot answer from this repository;
  consumer evidence must be collected (admin-panel adapter inventory) before
  any removal. Until then the route is treated as consumed.

### Deprecation preconditions

1. **Replacement coverage:** every capability used from these endpoints
   (profile detail read, full-content PUT with optimistic locking, sibling
   creation) exists on the canonical `/api/v1/admin/` surface and is covered
   by admin-panel consumer fixtures.
2. **Admin-panel cutover evidence:** admin-panel no longer calls
   `/api/admin/profiles/...` (adapter inventory + fixtures as evidence).
3. **No 404-detecting consumers:** removal changes these paths from
   "responds with auth/validation errors" to Django/Ninja-level 404s.
   Evidence is required that no consumer (frontend code, scripts,
   integrations, monitoring) detects route availability by probing these
   paths for 404.
4. **`editorUrl` coupling resolved:** no surviving payload references
   `/admin/**` editor routes (ties into route family 1, precondition 2).

### Planned phases (evidence-gated, no dates)

1. **Announce:** mark the routes as deprecation candidates in
   `API-INVENTORY.md` once replacement coverage is verified; add a
   deprecation note to the endpoint documentation without changing behavior.
2. **Observe:** run with both surfaces available; collect evidence that the
   canonical surface carries the full workload and that legacy traffic is
   gone (access logs, audit log patterns, frontend adapter inventory). The
   routes are never disabled by timeout — only by decision.
3. **Remove:** delete the URL wiring and views via a separate accepted ADR,
   with the test and compatibility notes required by `Back-End/AGENTS.md`
   (API changes require contract, compatibility, frontend impact, tests, and
   migration notes). Removal of the views also removes their error shape from
   the error-compatibility matrix; that edit is part of the same accepted
   change, never a silent one.

## Explicit statement on dates

No phase in this plan carries a calendar date. Phases advance only when the
listed precondition evidence exists and the owner accepts the record for the
next phase. If evidence never arrives for a family, that family is never
removed — "Keep through cutover" and "Deprecation decision required" are the
current owned statuses, and they stand until changed by an accepted decision.

## Relationship to other documents

- `Docs/03-contracts/AUTH-CONTRACT.md`: same-origin baseline; `/staff/`
  login/MFA fallback is part of the accepted authentication posture.
- `Docs/02-architecture/DEPLOYMENT-TOPOLOGY.md`: admin static SPA is served
  same-origin through the reverse proxy; the cutover evidence requirements
  live there.
- `Docs/09-decisions/ADR-0006-ERROR-ENVELOPE-NORMALIZATION.md` (Proposed):
  inventories the error shapes served by these route families, including
  `/api/admin/profiles/...`; removal sequencing there is also evidence-gated.
- `Back-End/docs/contracts/API-INVENTORY.md`: normative route statuses; any
  status change here must be applied there by an accepted change, not by this
  document.
