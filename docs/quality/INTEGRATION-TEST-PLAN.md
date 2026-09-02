# Integration Test Plan — Same-Origin Delivery (BACKEND-170)

Scope: backend integration checklist for the accepted same-origin reverse-proxy
topology (`Docs/02-architecture/DEPLOYMENT-TOPOLOGY.md`) and auth contract
(`Docs/03-contracts/AUTH-CONTRACT.md`). This plan feeds BACKEND-180, and is
consumed downstream by PUBLIC-320 and ADMIN-300 integrated smokes.

## Evidence layers

| Layer | Environment | What it proves | Owner |
|---|---|---|---|
| Disposable-env smoke | Django test client, `config.settings.test` (SQLite) or disposable PostgreSQL (`docs/operations/LOCAL-DATABASE.md`) | Server-side boundaries: session, CSRF, MFA, expiry, contact, preview, media | BACKEND-180 (`tests/test_staging_smoke.py`) |
| Staging browser evidence | Staging stack behind the reverse proxy | Real cookie/`Secure`/`SameSite`, proxy headers, host routing | COORD-060/070 (out of backend scope) |

Disposable-env tests do **not** substitute for browser acceptance
(`AGENTS.md` completion-evidence rule; `MASTER-TASK-LIST.md` R7/R8 gates).

## Checklist

Each row: flow → automated smoke → staging evidence requirement.

| # | Flow | Disposable-env smoke | Staging browser evidence |
|---|---|---|---|
| 1 | `GET /health/` readiness | `test_staging_smoke.py` chain step 1 | HTTP 200 body `{status, db, contact}` |
| 2 | Anonymous / me guard | chain step 2 (401 `AUTH_REQUIRED`) | signed-out admin shows sign-in |
| 3 | Sign-in bad credentials | chain step 3 (401 `AUTH_FAILED`) | error shown; no session cookie change |
| 4 | Sign-in (pre-MFA enrollment) | chain step 4 (200, `mfaEnrolled=false`) | dashboard reachable |
| 5 | MFA enrollment via SPA API (`/auth/mfa/status` → `/auth/mfa/confirm`) | chain step 5 (recovery codes issued) | QR/secret + confirm code accepted |
| 6 | Sign-in with enrolled MFA requires OTP | chain step 6 (401 without token) | OTP prompt shown |
| 7 | CSRF failure on state-changing call | chain step 7 (403 `CSRF_FAILED`) | request without `X-CSRFToken` rejected |
| 8 | Session expiry forces re-auth | chain step 8 (expired row → 401) | stale session returns sign-in |
| 9 | Sign-in with valid OTP | chain step 9 (200, `otpVerified=true`) | OTP accepted; dashboard loads |
| 10 | CMS content mutation (article create) | chain step 10 (201) | draft saved and listed |
| 11 | Preview share link (valid token shows draft; no leak of unpublished via public surface) | chain step 11 (200 + `no-store`, sanitized body) | link opens draft outside session |
| 12 | Preview share expiry | `test_preview_share_expiry_smoke` (410) | expired link shows expiry page |
| 13 | Contact form HTML path (no-JS) | `test_contact_smoke` (200 HTML, one email) | footer form sends; styled response |
| 14 | Contact JSON path | same (200 `{ok: true}`, one email) | JSON client path |
| 15 | Contact cross-origin rejection | same (400, no email) | foreign-origin POST rejected |
| 16 | Contact non-persistence | same (no message body in `AuditLog`) | — |
| 17 | Media upload via admin API | chain step 12 (201, inactive by default) | upload succeeds |
| 18 | Public media delivery boundary | chain step 12 (active 200 / inactive 404) | `/media/` serves active files only |
| 19 | Logout ends session | chain step 13 (200 → 401 on `/auth/me`) | sign-out clears session |
| 20 | Login rate limiting / audit rows | covered by `test_admin_api_auth.py`, `test_security.py` | repeat-failure block observed |

## Same-origin expectations asserted implicitly

- Admin API auth is session-cookie based; no CORS middleware exists and none
  may be added without a separate topology decision (`DEPLOYMENT-TOPOLOGY.md`).
- Unsafe admin API methods enforce `X-CSRFToken` against the `csrftoken`
  cookie (`apps/api/admin_common.py::_check_csrf`).
- Contact rejects foreign `Origin`/`Referer` and stores nothing
  (`apps/api/public_contact.py`).
- Preview share URLs are short-lived, `noindex`/`noarchive`/`no-store`
  (`apps/content/views_preview.py`).

## Execution

```powershell
uv run pytest tests/test_staging_smoke.py -v
```

Runs on the disposable test settings by default; the same module runs against
the disposable PostgreSQL profile documented in `docs/operations/LOCAL-DATABASE.md`.
