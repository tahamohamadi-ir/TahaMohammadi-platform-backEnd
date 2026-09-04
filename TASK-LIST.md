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
- [x] **BACKEND-081** Map `seed.empty.*` records to route copy / unavailable surfaces without inventing facts.
- [x] **BACKEND-082** Map `admin.*` supplement records to admin-only models; verify public serializers omit them.

## BE-2 — Legacy infra extraction

- [x] **BACKEND-090** Classify every file under `Infra/legacy-monorepo/` (rewrite, reference, delete). → `docs/operations/LEGACY-MONOREPO-INVENTORY.md` (46 files: 20 rewrite / 18 reference / 8 delete-recommended; nothing moved or executed).
- [x] **BACKEND-100** Standalone compose: `docker-compose.dev.yml` gains `api` service (host `127.0.0.1:18010`→8000, avoids all legacy ports) + new root `Dockerfile`/`.dockerignore`. `docker compose config` passes; image build/run pending Docker daemon.
- [x] **BACKEND-101** Active-scripts path audit → `docs/operations/STANDALONE-VALIDATION.md` §A. Fixed `scripts/run_e2e_stack.sh` ADMIN_DIST path (+ regression test); 4/5 scripts already standalone-safe; `manual-rebuild.sh` blocked on rebuild-contract owner decision.
- [x] **BACKEND-102** `config.settings.local` validated: `manage.py check` passes (no-env and 5433 env); profile selection is env-driven; `migrate --plan` against live 5433 pending Docker daemon → `docs/operations/STANDALONE-VALIDATION.md` §B.
- [x] **BACKEND-160** `docs/operations/BACKUP-RESTORE.md` (new layout: pg_dump -Fc of 5433 profile + media dir; restore drill R7-gated, not yet drilled).

## BE-3 — Contracts and OpenAPI

- [x] Freeze accepted OpenAPI snapshot (`OPENAPI-ACCEPTANCE.md`; provenance `scaffold-accepted`).
- [x] Verify public OpenAPI anonymously and admin OpenAPI with staff+OTP fixture.
- [x] Export OpenAPI artifacts and endpoint inventory.
- [x] **BACKEND-110** Reconcile public endpoints vs central `ROUTE-REGISTRY.md`.
- [x] **BACKEND-120** Reconcile admin endpoints vs `Front-End/admin-panel/docs/architecture/WORKFLOW-API-MAP.md` → `docs/contracts/ADMIN-ROUTE-RECONCILIATION.md` (63 ops verified; gaps G-A..G-I; G-E media-delete and G-G sibling-create are blocking decisions).
- [x] **BACKEND-130** Add response fixtures per `ERROR-COMPATIBILITY-MATRIX.md`.
- [x] **BACKEND-140** OpenAPI hash drift tests (fail when artifact changes without acceptance).
- [x] **BACKEND-150** ADR-0006 **Accepted** 2026-09-02 (owner-delegated review; DECISION-LOG). Phase 1 shipped: AdminError responses now emit additive `field_errors` alongside `fields` (dual-key).
- [x] **BACKEND-151** Deprecation plan **approved** by owner 2026-09-02: `docs/contracts/LEGACY-ROUTE-DEPRECATION.md`; removals gated on cutover evidence + separate ADR.
- [x] **G-E/G-G** (2026-09-02, owner decisions in DECISION-LOG): `DELETE /api/v1/admin/media/{id}` (usage guard, `MEDIA_IN_USE` 409) and `POST /api/v1/admin/content/profile/{id}/sibling-locale` added; artifacts re-accepted via OPENAPI-ACCEPTANCE amendment (admin 47→48 paths, 103→105 ops); legacy sibling route stays per 151 plan.

## BE-4 — CI and quality

- [x] **BACKEND-041** GitHub Actions: `uv sync`, Ruff, pytest, `manage.py check`, OpenAPI fixture tests.
- [x] **BACKEND-042** CI gate: `scripts/verify_openapi_export.py` re-exports and hashes artifacts against `PROVENANCE.json` (3/3 match locally); wired as a step in `.github/workflows/ci.yml`.
- [ ] Migration forward/reverse rehearsal on production-like copy.
- [ ] Security review: media, preview tokens, contact, rebuild callbacks.

## BE-5 — Integration and release

- [x] **BACKEND-170** Same-origin integration test plan: `docs/quality/INTEGRATION-TEST-PLAN.md` (checklist against `DEPLOYMENT-TOPOLOGY.md` evidence list).
- [x] **BACKEND-180** Disposable-env smoke: `tests/test_staging_smoke.py` (health, sign-in, MFA enroll+OTP, CSRF failure, session expiry, re-auth, content create, preview share+expiry, media boundary, logout, contact HTML/JSON/cross-origin/non-persistence). 3/3 pass; full suite 675 pass. PostgreSQL-profile run pending: Docker daemon unavailable (Docker Desktop failed to start).
- [ ] **BACKEND-190** Permission matrix tests for all admin mutations.
- [ ] **BACKEND-200** Staging artifact + rollback evidence (`R7` backend slice).
- [x] **BACKEND-211** `SiteSettings.seed_policy` persisted at import (migration `siteconfig.0004`), served read-only via `GET /api/v1/admin/site`; OpenAPI admin artifact re-locked per OPENAPI-ACCEPTANCE addendum 2026-09-04b.
- [x] **BACKEND-210** Owner approval queue endpoint (`GET /api/v1/admin/approval-queue`), publication gate on `to=published` transitions and the scheduled-publish command (`409 APPROVAL_REQUIRED`), `approvalState` in admin content projections; OpenAPI re-accepted (49 paths / 106 ops) per OPENAPI-ACCEPTANCE addendum 2026-09-04.

---

## Completed baseline (do not redo)

- [x] Independent Git repository connected.
- [x] 198-file migration with zero hash mismatches.
- [x] Governance, architecture, contracts, quality docs.
- [x] CPython 3.12 + `uv` environment; 636 pytest pass locally.
- [x] Default CLI/WSGI/ASGI → development SQLite settings.
