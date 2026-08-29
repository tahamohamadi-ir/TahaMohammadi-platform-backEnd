# Backend Task List

## Migration baseline

- [x] Connect the independent Git repository.
- [x] Copy 198 tracked backend files from the legacy monorepo.
- [x] Verify every copied backend file by SHA-256.
- [x] Exclude virtual environments, caches, local databases, media, build output, and secrets.
- [x] Preserve legacy infrastructure under an explicitly reference-only path.
- [x] Add repository governance, architecture, API inventory, migration, quality, and operations documents.
- [x] Establish a clean dependency sync and test result in the new path.
- [x] Commit and push the verified migration baseline to `origin/main`.

## Standalone extraction

- [x] Make bare Django CLI/WSGI/ASGI default to isolated SQLite development settings; production/local/test remain explicit.
- [ ] Inventory every old monorepo path in scripts and infrastructure.
- [ ] Decide and document standalone container/process topology.
- [ ] Replace path assumptions and add environment validation.
- [ ] Validate local PostgreSQL and disposable E2E profiles.
- [ ] Rebuild backup and restore commands for this repository.
- [ ] Validate deployment, health, logging, media, scheduled publishing, and rollback.
- [ ] Verify the documented Docker-local database profile and standalone PostgreSQL alternative against `config.settings.local`.

## Contract and integration

- [x] Freeze an accepted public/admin OpenAPI snapshot (`Docs/03-contracts/OPENAPI-ACCEPTANCE.md`; provenance `scaffold-accepted`).
- [x] Verify public OpenAPI anonymously and admin OpenAPI with a disposable verified staff-plus-OTP fixture.
- [x] Generate source review snapshots and provenance with `scripts/export_openapi.py` (40 public paths; 47 admin paths; scaffold-accepted).
- [x] Generate a source-plus-schema endpoint inventory with commit and SHA-256 metadata.
- [ ] Reconcile public endpoints with public-site requirements.
- [ ] Reconcile admin endpoints, permissions, CSRF/session, and MFA with admin requirements.
- [ ] Normalize documented error envelopes without breaking compatibility.
- [ ] Add response fixtures for admin `code/message/fields`, public `detail`, contact `ok/error`, form HTML, and framework validation variants.
- [ ] Add frontend-consumer contract tests and seeded fixtures.
- [ ] Decide deprecation plan for legacy `/admin/`, `/staff/`, `/api/admin/`, and rebuild routes.

## Quality and release

- [ ] Full tests and lint green in CI; the local migration baseline is green.
- [ ] Migration forward/reverse rehearsal on a production-like copy.
- [ ] Security, media, preview, contact, and rebuild threat review.
- [ ] Backup/restore and disaster-recovery drill.
- [ ] Staging integration with both new frontends.
- [ ] Verify same-origin reverse-proxy session, CSRF, MFA, preview, contact, media, and logout behavior with both new frontends.
- [ ] Cutover and rollback evidence accepted.
