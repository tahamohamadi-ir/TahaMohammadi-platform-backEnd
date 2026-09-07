# PU-04-catalog Revision Handoff — CATALOG-GRAPH-REVIEW C1–C3

Owner repository: `Back-End`
Packet: `PU-04-catalog` (REVISE → ready for re-review)
Specification: `Docs/05-delivery/concept-alignment-v2/product-packets/PU-04-catalog.md`
Contract: `Docs/03-contracts/PRODUCT-INTERFACES-V2.md` §I03/§I04 R1
Review evidence: `Docs/05-delivery/concept-alignment-v2/reviews/CATALOG-GRAPH-REVIEW-2026-09-06.md` (C1–C3)
Status: **HANDOFF_READY** (explicitly uncommitted)
Base commit: `bd6682ea9dae7e5bf6957c36691dc3a94a00ea37`
Result commit: uncommitted working directory

---

## 1. Summary of corrections (existing implementation corrected, not rebuilt)

- **C1 — gated file delivery (`apps/composition/projection.py`)**:
  - `_public_download_map(request, ids, locale)` now enforces the same
    publication/access policy as `GET /downloads/{locale}/{slug}` and
    `DownloadDetailOut`: `Download.objects.public()` (status published +
    `published_at <= now`) + exact story locale + `public_media_is_downloadable()`
    (PUBLIC access + active media + file) + publication-snapshot fallback via
    `resolve_published_target(Download, "download", pk, locale)`.
  - Ineligible downloads (restricted/metadata-only/draft/future/inactive/
    wrong-locale/missing) are omitted entirely — no `download`/`file` keys,
    no direct storage URL disclosure. Eligible entries keep the existing
    `{id,title,slug,url,mime,size}` shape for no-JS consumers.
  - `_project_settings(..., locale)` threads story locale through live and
    snapshot-backed paths; snapshot sections use the same download filter.
- **C2 — related resolution (`apps/composition/projection.py`)**:
  - `related` blocks no longer copy `records` verbatim. New
    `_filter_public_related_records(records, locale)` resolves each
    `{family,id}` through the shared published-record policy
    (`CONTENT_RELATED_FAMILIES` + `FAMILY_TO_ENTITY_KEY` +
    `resolve_published_target`), exact locale, preserves order, omits
    missing/private/draft/archived/other-locale refs without inventing slugs.
    IDs normalized to canonical decimal strings per §I03.
- **C3 — bounded canonical IDs (`apps/composition/blocks.py`, `projection.py`)**:
  - `blocks.py`: `_CANONICAL_ID_RE = ^[1-9][0-9]{0,18}$` (ASCII) +
    `MAX_CANONICAL_ID = 9223372036854775807` (signed 64-bit BigAutoField,
    matches `record_resolver.py`). New `_parse_canonical_pk()` rejects
    bools/floats/leading-zeros/non-ASCII/zero/negative/overlong/overflow with
    `BlockValidationError`, never `ValueError`. Applied to `_check_media_pk`,
    `_check_download_pk`, `_validate_related_list` (int range also widened).
  - `projection.py`: new `_coerce_canonical_pk()` (never raises, returns None
    on malformed) replaces all `isdigit`/`int()` parsing in media/download
    collectors, `_project_settings` file/media resolution and snapshot
    collectors, so malformed stored rows omit instead of 500ing. Shared legacy
    `isdigit` patterns elsewhere (admin_media, preview) were inspected but left
    untouched per allowlist/legacy caution.

## 2. Changed paths (exact allowlist only)

- `apps/composition/blocks.py` (MODIFIED): canonical 64-bit IDs + fail-closed parser.
- `apps/composition/projection.py` (MODIFIED): locale-aware gated downloads,
  related filtering, canonical coercion, snapshot parity.
- `apps/api/admin_composition.py` (UNCHANGED): no schema change required.
- `tests/test_product_story_blocks.py` (MODIFIED, NEW tests): fixed
  `sample_download` to be truly public (`published_at` past + `public`
  access) + 16 regression tests (C1×8, C2×2, C3×6 — see §4).
- `docs/contracts/openapi/current/public-openapi.json` (RE-EXPORTED, no schema
  delta from this fix): SHA256 `47980f8f1992d885398676cf984b80e7068c7aa8e8b8f76a856565ffc9033681`, paths 48.
- `docs/contracts/openapi/current/admin-openapi.json` (RE-EXPORTED, no delta):
  SHA256 `1176c0696222f9ac4c86495446d1f00988bdfde19dd147e93ece29a61e973564`, paths 57.
- `docs/contracts/openapi/current/PROVENANCE.json`, `endpoint-inventory.md`
  (RE-EXPORTED): inventory ops 124, SHA `154a2c1bf950978f9a8332571098e4bfb2b815c9308a38426976e08dc002e4eb`.
- `docs/quality/product-v2/PU-04-catalog-HANDOFF.md` (THIS FILE).

No other repository, migration, model, route or accepted packet file was modified.
`ACCEPTANCE.json` (untracked, created by another packet) was not touched.

## 3. Failing-before / passing-after

Failing-before (reproduced from review, pre-fix behavior):
- Restricted `Download(locale fa, access_state restricted, active media)` projected
  `file/download.url = /media/...` (disclosure); after fix omitted.
- `related {records:[{family:article,id:999999}]}` copied verbatim; after fix `[]`.
- `validate_block_settings('file', {'downloadId':'²'})` raised `ValueError`; after
  fix raises `BlockValidationError`; admin PUT with `²` now 400, not 500.

## 4. Acceptance and verification

```bash
uv run pytest tests/test_product_story_blocks.py -q
# 23 passed (7 original + 16 new C1–C3)
uv run pytest tests/test_product_story_blocks.py tests/test_product_localized_settings.py -q
# 40 passed (23 catalog + 17 settings)
uv run pytest tests/test_admin_composition_api.py tests/test_story_composition.py tests/test_product_record_resolver.py -q
# 69 passed
uv run ruff check apps/composition/blocks.py apps/composition/projection.py tests/test_product_story_blocks.py
# All checks passed!
uv run python scripts/verify_openapi_export.py
# MATCH all three artifacts; export matches accepted record.
```

New tests:
- `TestCatalogGraphReviewC1`: public enriched; restricted/metadata_only/draft/
  future/inactive/wrong-locale omitted; snapshot-backed live+snapshot filtering.
- `TestCatalogGraphReviewC2`: missing/draft/archived/other-locale omitted, valid
  kept; order + string-ID preservation.
- `TestCatalogGraphReviewC3`: unicode `²` → Validation (not ValueError);
  malformed list (empty/zero/leading-zero/negative/float/whitespace/non-ASCII/
  overlong/overflow) rejected; `2147483648` + `9223372036854775807` accepted,
  `9223372036854775808` rejected; media unicode rejected; malformed stored IDs
  never raise in projection; admin PUT `²` → 400.

## 5. UI / no-JS

No UI change. Public story keeps readable no-JS shape; file `download/file`
present only when downloadable, related `records` filtered. Positive file case
still exposes `{download,file}` for approved public downloads.

## 6. Security, data risk, rollback

- Fail-closed: malformed IDs → Validation/omit, never 500/ValueError; restricted
  URLs never disclosed; snapshot fallback preserves A04 draft isolation
  (live draft + published snapshot → snapshot served; archived → omitted).
- No migration, no model change, no production write. Disposable test DB only;
  synthetic media via `SimpleUploadedFile`, no real upload exfiltration.
- Rollback: revert the two `apps/composition/*` files; tests revert with them.

## 7. Dirty status and remaining risks

- Uncommitted within allowlist only (see §2). Pre-existing dirty/untracked from
  other packets preserved untouched.
- OpenAPI re-export shows no schema delta from C1–C3 (validation/projection
  only); hashes above match `PU-SYNC-graph` pin (`47980f...`), so consumer sync
  remains valid. No blind manifest refresh.
- Remaining: renderer/editor shape confirmation for enriched `download/file`
  (currently preserved shape) belongs to PU-13-story; lesson/collection related
  support awaits PU-05/PU-06 + resolver sync per review clarification.

---

PU-04-catalog_HANDOFF_READY
