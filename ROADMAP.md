# Backend Roadmap

<!-- PRODUCT-V2.1 -->
Current execution target: research-first bilingual portfolio, independently publishable detail pages and broad CMS editing under ADR-0010. Dispatch only this repository's packets from `../Docs/05-delivery/concept-alignment-v2/EXECUTION.md` (paths here are repository-relative). Older scaffold/phase status below is a dated baseline, not current feature acceptance. Preserve current endpoints until the additive target contract is implemented and exported.
<!-- /PRODUCT-V2.1 -->

1. **B0 — Migration baseline:** independent repo, verified copy, documentation, lock file, checks, commit and push.
2. **B1 — Standalone operations:** replace monorepo path assumptions, compose/runtime files, environment validation, backup/restore, health and logging; keep `.env.example` aligned with the selected local profile.
3. **B2 — Contract stabilization:** export accepted OpenAPI, normalize errors, document auth/locale/media/content contracts, add consumer contract tests.
4. **B3 — Data and workflow hardening:** content lifecycle, translations, revisions, scheduling, media usage, graph/timeline validation, contact abuse controls.
5. **B4 — Frontend integration:** disposable fixtures, CORS/CSRF/session decisions, preview and rebuild flows, public/admin E2E support.
6. **B5 — Release:** migration rehearsal, production observability, backup/restore drill, security review, staged cutover, rollback proof.
