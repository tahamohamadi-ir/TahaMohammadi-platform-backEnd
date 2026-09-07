# PU-07-preview Delivery Handoff

Owner repository: `Back-End` (`d:\Project\tahamohammadi-platform\Back-End`)  
Packet: `PU-07-preview`  
Specification: `Docs/05-delivery/concept-alignment-v2/product-packets/PU-07-preview.md`  
Contract: `Docs/03-contracts/PRODUCT-INTERFACES-V2.md` §I03  
Status: **HANDOFF_READY** (explicitly uncommitted)  
Base commit: `bd6682ea9dae7e5bf6957c36691dc3a94a00ea37`  
Result commit: uncommitted working directory  

---

## 1. Summary of Changes

Extended existing expiring private preview to every publishable entity and its private attachments, preserving private/noindex/expiring boundaries and strictly preventing public `/media/` exposure for private files per `Docs/03-contracts/PRODUCT-INTERFACES-V2.md` §I03:

- **All-Family Preview Coverage (`apps/content/views_preview.py`)**:
  - Registered all 15 publishable models in `PREVIEW_KINDS`:
    `landing`, `profile`, `article`, `series`, `research-topic`, `research-statement`, `project`, `publication`, `book`, `talk`, `download`, `course`, `creative-work`, `lesson`, `collection`.
  - Added model field extraction logic across all 15 models (handling custom attributes such as `objective` for `Project`, `abstract` for `Publication` and `Talk`, and `summary` for `ResearchTopic` and `Lesson`).
- **Private Attachment Isolation and Security**:
  - Implemented `_preview_media_url` ensuring inactive media (`is_active=False`) and private download files NEVER leak public `/media/...` URLs into HTML or API responses.
  - Routed private media through stateless HMAC expiring preview routes:
    - `/preview/share/<token>/file/`: Streams private download files.
    - `/preview/share/<token>/attachment/<media_id>/`: Streams private media attached to the previewed object, its story, or its case-study diagrams/screenshots.
  - Implemented entity boundary validation in `public_share_preview_attachment`: validates that the requested `media_id` actually belongs to the previewed entity's attached story or project artifacts; returns 404 for unattached/foreign media IDs.
  - Added `X-Robots-Tag: noindex, nofollow, noarchive` and `Cache-Control: private, no-store` headers across all preview HTML and streaming attachment responses.
- **Story and Project Case Study Rendering in Preview**:
  - Implemented `_render_story_to_html` to render draft story sections and blocks (`paragraph`, `heading`, `quote`, `list`, `callout`, `math`, `code`, `figure`/`media`, `file`, `table`, `divider`, `embed`) into sanitized HTML with private attachment routing.
  - Rendered complete project case study details (`problem`, `constraints`, `technical_decisions`, `trade_offs`, `outcomes_summary`, `lessons_learned`, `testing_summary`), evidence items, architecture diagrams, screenshots, collaborators, and funding in `staff_preview.html`.
- **Token and URL Helpers (`apps/content/preview_token.py` & `apps/content/urls_public_preview.py`)**:
  - Added `build_preview_share_file_path(token)` and `build_preview_share_attachment_path(token, media_id)`.
  - Registered `share/<str:token>/file/` and `share/<str:token>/attachment/<int:media_id>/` routes under the public preview URL configuration.
- **Admin API Compatibility (`apps/api/admin_content.py`)**:
  - Kept POST `/api/v1/admin/content/{entity}/{id}/preview-link` aligned across all 15 models while maintaining compatibility with legacy test suite assertions.
- **OpenAPI Export (`docs/contracts/openapi/current/`)**:
  - Re-exported `public-openapi.json`, `admin-openapi.json`, and `endpoint-inventory.md`.
  - Updated `PROVENANCE.json` with artifact checksums.
- **Automated Test Suite (`tests/test_product_preview.py`)**:
  - Added 5 comprehensive automated tests verifying all requirements of PU-07-preview.

---

## 2. Changed Paths (Within Exact Allowlist)

- `apps/api/admin_content.py` (MODIFIED): Preserved preview link generation across all models.
- `apps/content/views_preview.py` (MODIFIED): Expanded `PREVIEW_KINDS` to 15 models, implemented private attachment streaming, story rendering, and security headers.
- `apps/content/urls_public_preview.py` (MODIFIED): Registered preview file and attachment routes.
- `apps/content/preview_token.py` (MODIFIED): Added preview file and attachment URL builders.
- `apps/content/templates/content/staff_preview.html` (MODIFIED): Added sections for case study, evidence, diagrams, screenshots, collaborators, funding, and attachments.
- `tests/test_product_preview.py` (NEW): Automated test suite for preview across all entities and private attachments.
- `docs/contracts/openapi/current/public-openapi.json` (MODIFIED): Exported public OpenAPI schema.
- `docs/contracts/openapi/current/admin-openapi.json` (MODIFIED): Exported admin OpenAPI schema.
- `docs/contracts/openapi/current/PROVENANCE.json` (MODIFIED): Updated OpenAPI provenance.
- `docs/contracts/openapi/current/endpoint-inventory.md` (MODIFIED): Exported endpoint inventory.
- `docs/quality/product-v2/PU-07-preview-HANDOFF.md` (NEW): This delivery report.

---

## 3. Acceptance and Verification Results

### 3.1 Targeted Preview Test Suite
```bash
uv run pytest tests/test_product_preview.py tests/test_preview_share.py -v
```
**Result**: `15 passed in 3.76s`
- `test_preview_kinds_coverage`: PASSED
- `test_all_15_entities_public_share_preview`: PASSED
- `test_private_download_does_not_expose_public_media_url`: PASSED
- `test_story_with_private_media_attachment`: PASSED
- `test_project_case_study_and_private_diagrams_in_preview`: PASSED
- `test_valid_token_shows_draft`: PASSED
- `test_expired_token_returns_410`: PASSED
- `test_tampered_token_returns_404`: PASSED
- `test_unknown_kind_returns_404`: PASSED
- `test_missing_object_returns_404`: PASSED
- `test_profile_and_article_kinds`: PASSED
- `test_sanitize_strips_script`: PASSED
- `test_anonymous_cannot_create_preview_link`: PASSED
- `test_staff_otp_creates_preview_link`: PASSED
- `test_unsupported_entity_returns_404`: PASSED

### 3.2 Regressions (Product-V2 Test Suite)
```bash
uv run pytest -k product -q
```
**Result**: `133 passed, 705 deselected in 5.81s`

### 3.3 Code Quality & Formatting
```bash
uv run ruff check .
```
**Result**: `All checks passed!` across the entire repository.

### 3.4 Database Migrations Check
```bash
uv run python manage.py makemigrations --check
```
**Result**: `No changes detected`

---

## 4. Contract Conformance Summary

| Requirement | Contract Section | Implementation Details | Status |
|---|---|---|---|
| Every Publishable Entity Preview | §I03 | All 15 models registered in `PREVIEW_KINDS` and supported in admin preview link generation | Conforms |
| Expiring Signed Tokens | §I03 | Stateless HMAC SHA256 tokens with configurable TTL; returns 410 on expiry and 404 on tamper | Conforms |
| Private File Isolation | §I03 | Inactive media and private download files never output `/media/...` URLs; streamed via preview token | Conforms |
| Private Attachment Isolation | §I03 | Story figures/files and project diagrams stream via `/preview/share/<token>/attachment/<media_id>/` | Conforms |
| Entity Boundary Validation | §I03 | Media ID requested under preview token verified against the previewed entity; foreign IDs return 404 | Conforms |
| Security Headers | §I03 | `X-Robots-Tag: noindex, nofollow, noarchive` and `Cache-Control: private, no-store` on all preview responses | Conforms |
| Full Hierarchy Display | §I03 | Story sections/blocks, case studies, evidence, diagrams, collaborators, and funding rendered in preview HTML | Conforms |

---

## 5. Non-Negotiable Boundaries Verification

- Changes isolated strictly to the `Back-End` repository allowlist for packet `PU-07-preview`.
- No code copied from legacy frontends or legacy repositories.
- Zero commits made to git (`git commit` withheld).
- All tests passing; ruff lint clean; OpenAPI specs in sync.
