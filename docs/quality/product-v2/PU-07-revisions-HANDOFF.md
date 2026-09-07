# PU-07-revisions Delivery Handoff

Owner repository: `Back-End` (`d:\Project\tahamohammadi-platform\Back-End`)  
Packet: `PU-07-revisions`  
Specification: `Docs/05-delivery/concept-alignment-v2/product-packets/PU-07-revisions.md`  
Contract: `Docs/03-contracts/PRODUCT-INTERFACES-V2.md` §I03  
Status: **HANDOFF_READY** (explicitly uncommitted)  
Base commit: `bd6682ea9dae7e5bf6957c36691dc3a94a00ea37`  
Result commit: uncommitted working directory  

---

## 1. Summary of Changes

Implemented atomic content revision snapshotting, revision restoring as draft, published snapshot preservation, draft isolation in composition projection, and preview link generation across all publishable entities per `Docs/03-contracts/PRODUCT-INTERFACES-V2.md` §I03:

- **Publication Snapshot Model (`apps/content/models.py` & migration `0031_pu_publication_snapshots.py`)**:
  - Created `PublicationSnapshot` model storing immutable frozen publication snapshots:
    - `entity_key`: Content entity key or `"composition"` (`models.CharField(max_length=64)`).
    - `object_id`: Target record ID (`models.BigIntegerField()`).
    - `locale`: Locale code (`models.CharField(max_length=10, blank=True)`).
    - `slug`: Content slug (`models.CharField(max_length=255, blank=True)`).
    - `snapshot`: Frozen JSON payload containing all fields, attached story, and relations at the exact time of publication (`models.JSONField()`).
    - `published_at`: Timestamp when the publication snapshot took effect (`models.DateTimeField()`).
    - `created_at`: Creation timestamp (`models.DateTimeField(auto_now_add=True)`).
    - `created_by`: User reference if available.
  - Added indexes on `(entity_key, object_id, -published_at)` and `(entity_key, locale, slug)`.
- **Public Composition Projection Isolation (`apps/composition/projection.py`)**:
  - Updated `public_story_document(page, locale)` to check for existing `PublicationSnapshot` records for `page.id` before falling back to live page state.
  - When a published snapshot exists, projects public story sections and blocks from `snapshot["sections"]`, strictly preventing subsequent draft block/section edits from leaking to public visitors prior to explicit publication.
  - Falls back to live page only when `page.status == "published"`.
- **Composition Admin Publishing (`apps/api/admin_composition.py`)**:
  - In `composition_update` (PUT `/api/v1/admin/composition/{page_id}`), records an immutable `PublicationSnapshot` whenever `page.status == "published"`, capturing all sections and blocks with `published_at=timezone.now()`.
- **Admin Content Revisions & Atomic Restore (`apps/api/admin_content.py`)**:
  - **Full Hierarchy Snapshot Serialization**:
    - Implemented `_serialize_composition_story(story)` capturing sections, blocks, settings, and layout.
    - Implemented `_serialize_project_case_study(item)` capturing case study details, evidence, collaborators, and funding.
    - Implemented `_build_full_content_snapshot(item, entity)` combining parent fields, attached story, relations (`members`, `related_records`), and project case-study details into a single unified snapshot payload.
  - **Publication Snapshot Recording**:
    - Recorded `PublicationSnapshot` in `content_update` and `content_transition` when item transitions to or is updated in `published` status.
  - **Revision Retrieval**:
    - Added `GET /api/v1/admin/content/{entity}/{id}/revisions/{revision_id}` returning the revision along with its complete snapshot payload.
  - **Atomic Restore as Draft**:
    - In `content_revisions_restore` (POST `/api/v1/admin/content/{entity}/{id}/revisions/{revision_id}/restore`):
      1. Atomically captures a pre-restore snapshot of the current live item (`note="pre-restore snapshot"`), ensuring historical state is never lost.
      2. Restores parent entity fields and explicitly forces draft lifecycle (`status = "draft"`, `scheduled_for = None`).
      3. Restores attached story (sections and blocks) and explicitly forces attached story to draft (`status = "draft"`, `published_at = None`).
      4. Restores relations (e.g. series articles M2M).
      5. Restores project case study, evidence, collaborators, and funding.
  - **Preview Link Coverage**:
    - Extended `PREVIEW_SHARE_ENTITIES` to all 15 publishable models.
    - Registered all models dynamically in `views_preview.PREVIEW_KINDS` and wrapped `_preview_context` with `_safe_preview_context` to handle models without `.body` attributes.
- **OpenAPI Schema Export (`docs/contracts/openapi/current/`)**:
  - Re-exported `public-openapi.json`, `admin-openapi.json`, and `endpoint-inventory.md`.
  - Updated `PROVENANCE.json` with artifact checksums.
- **Automated Test Suite (`tests/test_product_revision_snapshot.py`)**:
  - Added 5 comprehensive automated tests verifying all requirements of PU-07-revisions.

---

## 2. Changed Paths (Within Exact Allowlist)

- `apps/content/models.py` (MODIFIED): Added `PublicationSnapshot` model.
- `apps/content/migrations/0031_pu_publication_snapshots.py` (NEW): Database migration for `PublicationSnapshot`.
- `apps/composition/projection.py` (MODIFIED): Added published snapshot draft isolation logic.
- `apps/api/admin_composition.py` (MODIFIED): Added published snapshot recording on composition update.
- `apps/api/admin_content.py` (MODIFIED): Added full snapshot serialization, revision detail endpoint, atomic restore as draft with pre-restore snapshot, and all-family preview link registration.
- `tests/test_product_revision_snapshot.py` (NEW): Automated test suite for revision snapshots and draft isolation.
- `docs/contracts/openapi/current/public-openapi.json` (MODIFIED): Exported public OpenAPI schema.
- `docs/contracts/openapi/current/admin-openapi.json` (MODIFIED): Exported admin OpenAPI schema.
- `docs/contracts/openapi/current/PROVENANCE.json` (MODIFIED): Updated OpenAPI provenance.
- `docs/contracts/openapi/current/endpoint-inventory.md` (MODIFIED): Exported endpoint inventory.
- `docs/quality/product-v2/PU-07-revisions-HANDOFF.md` (NEW): This delivery report.

---

## 3. Acceptance and Verification Results

### 3.1 Targeted Test Suite
```bash
uv run pytest tests/test_product_revision_snapshot.py -vv
```
**Result**: `5 passed in 4.58s`
- `test_publication_snapshot_model_exists`: PASSED
- `test_atomic_revision_snapshot_includes_story_and_relations`: PASSED
- `test_atomic_revision_restore_as_draft`: PASSED
- `test_publication_snapshot_draft_isolation_in_projection`: PASSED
- `test_all_family_preview_links`: PASSED

### 3.2 Regressions (Product-V2 Test Suite)
```bash
uv run pytest -k product -q
```
**Result**: `128 passed, 705 deselected in 5.72s`

### 3.3 Code Quality & Formatting
```bash
uv run ruff check .
```
**Result**: `All checks passed!` across the entire repository.

### 3.4 Database Migration Verification
```bash
uv run python manage.py migrate content 0031
uv run python manage.py migrate content 0030
uv run python manage.py migrate content 0031
```
**Result**: Migration applies forward, rolls back cleanly, and reapplies without issues.

---

## 4. Contract Conformance Summary

| Requirement | Contract Section | Implementation Details | Status |
|---|---|---|---|
| Atomic Content & Story Snapshot | §I03 | `_build_full_content_snapshot` captures parent fields, attached story sections/blocks, relations, and case-study evidence/collaborators | Conforms |
| Atomic Restore as Draft | §I03 | `content_revisions_restore` creates pre-restore snapshot, restores parent and attached story, forces both to draft (`status="draft"`) | Conforms |
| Preservation of History | §I03 | Pre-restore snapshot created prior to restoring revision payload | Conforms |
| Draft Isolation for Story | §I03 | `public_story_document` serves from `PublicationSnapshot`, isolating subsequent draft edits until re-published | Conforms |
| All-Family Preview Links | §I03 | All 15 publishable models support signed expiring preview share tokens | Conforms |
| Published Snapshot Integrity | §I03 | `PublicationSnapshot` stores frozen publication payloads ordered by `-published_at` | Conforms |

---

## 5. Non-Negotiable Boundaries Verification

- Changes isolated strictly to the `Back-End` repository allowlist for packet `PU-07-revisions`.
- No code copied from legacy frontends or legacy repositories.
- Zero commits made to git (`git commit` withheld).
- All tests passing; ruff lint clean; OpenAPI specs in sync.
