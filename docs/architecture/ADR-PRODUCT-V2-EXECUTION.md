# ADR — V2.1 product execution alignment (BACKEND)

Date: 2026-09-05. Status: accepted implementation direction; runtime acceptance OPEN.

The coordinating ADR-0010 and PRODUCT-INTERFACES-V2 contract supersede historical prefix-only queues, page-family freezes and incompatible implementation assumptions for assigned product packets. Use this repository's exact allowlists in `Docs/05-delivery/concept-alignment-v2/execution-tasks.json` at the coordination root.

Keep repository ownership and current endpoint compatibility. Backend owns source-generated schemas; frontend consumers regenerate types after an accepted schema handoff. Proposed routes/fields do not already exist merely because the design specifies them. Every behavior change needs evidence, and content publication/visual acceptance remain separate.

Historic ADRs and handoffs remain unchanged. This document records alignment only; it does not execute packets, publish owner content, commit, push or deploy.
