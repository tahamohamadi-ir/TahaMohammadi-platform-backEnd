# Backend Agent Contract

<!-- PRODUCT-V2.1 -->
Current product work: read `../Docs/09-decisions/ADR-0010-UNIFIED-EXECUTION-CONTRACTS.md`, `../Docs/05-delivery/concept-alignment-v2/EXECUTION.md`, and the assigned **BACKEND** leaf packet. New target interfaces live in `../Docs/03-contracts/PRODUCT-INTERFACES-V2.md`; generated OpenAPI remains current implementation evidence. Old prefix-only task selection and family freezes are superseded for this queue. CA-09–16 must not be dispatched separately.
<!-- /PRODUCT-V2.1 -->

## Read order

1. `README.md`
2. `PROJECT-MANIFEST.md`
3. `../Docs/00-governance/AUTHORITY-ORDER.md`
4. `../Docs/03-contracts/`
5. `docs/architecture/ARCHITECTURE.md`
6. `TASK-LIST.md`
7. `../Docs/05-delivery/MULTI-AGENT-TASK-BOARD.md` (select one active BACKEND packet from execution-tasks.json)

## Rules

- This repository is now the authority for backend development; do not edit the old monorepo as a substitute.
- Preserve migrated behavior until a task and test explicitly authorize a change.
- Never invent API fields, permissions, content, profile facts, infrastructure paths, or release state.
- API changes require contract, compatibility, frontend impact, tests, and migration notes.
- Schema changes require migrations, rollback/forward reasoning, and realistic data tests.
- Authorization is enforced on the server. Keep CSRF, MFA, rate/abuse controls, validation, sanitization, and audit behavior intact.
- Never commit secrets, local databases, media, logs, caches, virtual environments, or production data.
- Treat `Infra/legacy-monorepo/` as reference-only until its path assumptions are rewritten and verified.
- Add regression tests before or with every bug fix.

## Completion evidence

Report changed files, migrations, endpoint/contract effects, exact commands/results, security impact, data risk, deployment/rollback notes, and unresolved issues. Do not call a check passed unless it was run in this repository.
