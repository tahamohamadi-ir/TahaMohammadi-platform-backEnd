# Backend Task List

Detailed execution queue. Cross-repo board: `../../Docs/05-delivery/MULTI-AGENT-TASK-BOARD.md` (IDs prefixed `BACKEND-`).

Status: `[x]` done, `[ ]` open, `[~]` in progress.

---

## BE-0 — Database and local runtime

- [x] **BACKEND-010** Document disposable PostgreSQL profile (host port, db, user, password policy) in `docs/operations/LOCAL-DATABASE.md`.
- [x] **BACKEND-020** Reconcile `.env.example` for Docker-local and standalone PostgreSQL; document conflict avoidance with legacy `tahamohamadi-website` stack.
- [x] **BACKEND-030** Boot Django with `config.settings.development` against disposable PostgreSQL.
- [x] **BACKEND-040** Run `migrate` on empty database; capture migration plan artifact.
- [x] **BACKEND-050** Verify `GET /health/`; record example response in `docs/operations/HEALTH-CHECK.md`.

## BE-1 — Seed v1.1 import

- [x] **BACKEND-060** Implement `import_content_seed` management command reading `Docs/01-product/owner-content-seed-v1/cms-package/content-records.v1.1-seed.json`.
- [x] **BACKEND-070** Import all 85 records as draft/not-public; apply `supplement/seed-settings.json` defaults where mapped.
- [x] **BACKEND-080** Add pytest: seed import idempotency + public API does not expose unpublished records.
- [ ] **BACKEND-081** Map `seed.empty.*` records to route copy / unavailable surfaces without inventing facts.
- [ ] **BACKEND-082** Map `admin.*` supplement records to admin-only models; verify public serializers omit them.

## BE-2 — Legacy infra extraction

- [ ] **BACKEND-090** Classify every file under `Infra/legacy-monorepo/` (rewrite, reference, delete).
- [ ] **BACKEND-100** Author new-platform `docker-compose.yml` (API + PostgreSQL on non-conflicting port).
- [ ] **BACKEND-101** Replace monorepo path assumptions in active scripts.
- [ ] **BACKEND-102** Validate `config.settings.local` against Docker profile.
- [ ] **BACKEND-160** Document backup/restore for new repository layout.

## BE-3 — Contracts and OpenAPI

- [x] Freeze accepted OpenAPI snapshot (`OPENAPI-ACCEPTANCE.md`; provenance `scaffold-accepted`).
- [x] Verify public OpenAPI anonymously and admin OpenAPI with staff+OTP fixture.
- [x] Export OpenAPI artifacts and endpoint inventory.
- [ ] **BACKEND-110** Reconcile public endpoints vs central `ROUTE-REGISTRY.md`.
- [ ] **BACKEND-120** Reconcile admin endpoints vs `Front-End/admin-panel/docs/architecture/WORKFLOW-API-MAP.md`.
- [ ] **BACKEND-130** Add response fixtures per `ERROR-COMPATIBILITY-MATRIX.md`.
- [ ] **BACKEND-140** OpenAPI hash drift tests (fail when artifact changes without acceptance).
- [ ] **BACKEND-150** Error envelope normalization plan (non-breaking).
- [ ] **BACKEND-151** Deprecation plan for legacy `/admin/`, `/staff/`, `/api/admin/` routes.

## BE-4 — CI and quality

- [ ] **BACKEND-041** GitHub Actions: `uv sync`, Ruff, pytest, `manage.py check`, OpenAPI fixture tests.
- [ ] **BACKEND-042** GitHub Actions: OpenAPI export hash matches accepted provenance.
- [ ] Migration forward/reverse rehearsal on production-like copy.
- [ ] Security review: media, preview tokens, contact, rebuild callbacks.

## BE-5 — Integration and release

- [ ] **BACKEND-170** Same-origin integration test plan with new frontends.
- [ ] **BACKEND-180** Browser smoke: sign-in, MFA, CSRF failure, session expiry, contact, preview, media.
- [ ] **BACKEND-190** Permission matrix tests for all admin mutations.
- [ ] **BACKEND-200** Staging artifact + rollback evidence (`R7` backend slice).

---

## Completed baseline (do not redo)

- [x] Independent Git repository connected.
- [x] 198-file migration with zero hash mismatches.
- [x] Governance, architecture, contracts, quality docs.
- [x] CPython 3.12 + `uv` environment; 636 pytest pass locally.
- [x] Default CLI/WSGI/ASGI → development SQLite settings.
