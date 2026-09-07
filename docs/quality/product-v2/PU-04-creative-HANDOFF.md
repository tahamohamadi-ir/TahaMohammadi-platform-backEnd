# PU-04-creative Delivery Handoff

Owner repository: `Back-End` (`d:\Project\tahamohammadi-platform\Back-End`)  
Packet: `PU-04-creative`  
Specification: `Docs/05-delivery/concept-alignment-v2/product-packets/PU-04-creative.md`  
Contract: `Docs/03-contracts/PRODUCT-INTERFACES-V2.md` §I03  
Status: **HANDOFF_READY** (explicitly uncommitted)  
Base commit: `bd6682ea9dae7e5bf6957c36691dc3a94a00ea37`  
Result commit: uncommitted working directory  

---

## 1. Summary of Changes

Attached nullable localized story composition to `CreativeWork`, added validation and schema exposure for admin `storyId`, and exposed sanitized story in the public detail endpoint per §I03:

- **Model Relation (`apps/content/models.py`)**:
  - Added nullable `story` ForeignKey to `CreativeWork`:
    ```python
    story = models.ForeignKey(
        "composition.CompositionPage",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="attached_creative_works",
    )
    ```
- **Database Migration (`apps/content/migrations/0024_pu_creative_story.py`)**:
  - Created migration adding `story_id` column to `content_creative_work`.
  - Validated forward migration (`0024`), rollback (`0023`), and re-application (`0024`) on database without data loss.
- **Admin Schema & Validation (`apps/api/admin_content.py`)**:
  - Added `"story": "storyId"` to `DETAIL_FIELD_MAPS["creative-work"]`, exposing `storyId` in generic admin schema `/api/v1/admin/content/schema` with type `"number"`.
  - Enforced fail-closed validation:
    - Verifies referenced ID exists and its composition kind is `story` (`CompositionPage.KIND_STORY`).
    - Verifies that `storyId` locale strictly matches the content entity locale in both `content_create` and `content_update`.
  - Serializes `story_id` in `_detail_response` under `fields.storyId`.
- **Public API Projection (`apps/api/api.py`)**:
  - Added `story: StoryDocumentOut | None = None` to `CreativeWorkDetailOut`.
  - Implemented `resolve_story` delegating to `public_story_document(getattr(obj, "story", None), obj.locale)`.
  - Draft isolation: returns `None` if the story is unpublished, draft, non-story, or if the story locale mismatches the creative work locale.
  - Sanitization: filters disabled sections/blocks, sanitizes rich text and MathML, resolves active public media.
- **Test Suite (`tests/test_product_creative_story.py`)**:
  - Implemented 6 unit and integration tests covering:
    - Model field declaration.
    - Admin schema exposure of `storyId`.
    - Admin CRUD persistence and clear under optimistic locking (`If-Match`).
    - Admin validation against non-existent IDs, non-story compositions, and locale mismatches.
    - Public creative work detail exposure of sanitized story document.
    - Public draft isolation (draft story resolves to `None`).

---

## 2. Changed Paths (Within Exact Allowlist)

- `apps/content/models.py` (MODIFIED): Added `story` foreign key to `CreativeWork`.
- `apps/content/migrations/0024_pu_creative_story.py` (NEW): Database migration adding `story_id` column.
- `apps/api/api.py` (MODIFIED): Added `story` field and resolver to `CreativeWorkDetailOut`.
- `apps/api/admin_content.py` (MODIFIED): Added `"story": "storyId"` mapping and locale validation.
- `tests/test_product_creative_story.py` (NEW): Test suite covering model, admin validation, and public projection.
- `docs/contracts/openapi/current/public-openapi.json` (MODIFIED): Re-exported schema reflecting `CreativeWorkDetailOut.story`.
- `docs/contracts/openapi/current/admin-openapi.json` (MODIFIED): Re-exported schema reflecting `creative-work` schema `storyId`.
- `docs/contracts/openapi/current/PROVENANCE.json` (MODIFIED): Updated OpenAPI provenance with regenerated hashes.
- `docs/contracts/openapi/current/endpoint-inventory.md` (MODIFIED): Re-exported endpoint inventory.
- `docs/quality/product-v2/PU-04-creative-HANDOFF.md` (NEW): This delivery report.

---

## 3. Acceptance and Verification Results

### 3.1 Initial Failing Gap Evidence
Before applying model and schema changes:
```bash
uv run pytest tests/test_product_creative_story.py
```
**Output**: `6 failed in 3.00s`
- `TypeError: CreativeWork() got unexpected keyword arguments: 'story'`
- `AssertionError: CreativeWork must define a 'story' relation`
- `AssertionError: assert 'storyId' in cw_fields`

### 3.2 Targeted Test Suite
```bash
uv run pytest tests/test_product_creative_story.py -vv
```
**Result**: `6 passed in 2.91s`
- `test_creative_work_model_has_story_field`: PASSED
- `test_admin_content_schema_exposes_creative_work_story_id`: PASSED
- `test_admin_crud_creative_work_story_id`: PASSED
- `test_admin_creative_work_story_validation`: PASSED
- `test_public_creative_work_detail_exposes_story`: PASSED
- `test_public_creative_work_detail_draft_isolation`: PASSED

### 3.3 Regressions (Course, Publication & Metadata)
```bash
uv run pytest tests/test_product_course_story.py tests/test_product_publication_story.py tests/test_product_metadata.py -vv
```
**Result**: `19 passed in 2.92s`

### 3.4 Code Quality & Formatting
```bash
uv run ruff check .
```
**Result**: `All checks passed!` across the entire working tree.

### 3.5 Database Migration Forward/Backward Verification
```bash
uv run python manage.py migrate content 0024
uv run python manage.py migrate content 0023
uv run python manage.py migrate content 0024
uv run python manage.py makemigrations --check
```
**Result**:
- `Unapplying content.0024_pu_creative_story... OK`
- `Applying content.0024_pu_creative_story... OK`
- `makemigrations --check`: `No changes detected`

### 3.6 OpenAPI Export & Artifact Hashes
```bash
uv run python scripts/export_openapi.py
```
**Exported artifacts**:
- `public-openapi.json`:
  - Paths: 42
  - SHA256: `f49958d0484f2efa0e330092b3f654b0cc1aa34dc6b8cfcc9a9d17ea774ca847`
- `admin-openapi.json`:
  - Paths: 51
  - SHA256: `a15ce9111639838a175257847aa3a295373d107653038aa5128a031ca5fae336`
- `endpoint-inventory.md`:
  - Operations: 111
  - SHA256: `05bf86513574c7fc397d6121a892422245e78b2b614da43e8a07547ea4a69e50`

---

## 4. UI Changes
None (backend API, model, and OpenAPI schema packet).

---

## 5. Security & Isolation Confirmation
- **Draft Isolation**: Attached story composition is only included in public detail if `story.status == "published"` and `story.locale == cw.locale`. Otherwise, `story` resolves to `None`.
- **Content Sanitization**: Story text, quotes, and math blocks pass through standard HTML sanitizers; script tags and malicious attributes are stripped.
- **Fail-Closed Admin Validation**: Rejects missing story IDs, non-story compositions (`kind != "story"`), and cross-locale story attachments (`story.locale != item.locale`).
- **Optimistic Locking**: Enforced via `If-Match` headers for admin updates.

---

## 6. Dirty Status and Remaining Risks

1. **Central Queue & Dispatch Status**:
   - In `Docs/05-delivery/concept-alignment-v2/execution-tasks.json`, status is `NOT_STARTED`.
   - Written handoff report does **not** constitute acceptance; coordinator review, acceptance, and commit/merge remain required.
2. **Shared File Coupling**:
   - `apps/api/api.py`, `apps/api/admin_content.py`, and `apps/content/models.py` contain dirty uncommitted changes across predecessor packets (`PU-03-resolver`, `PU-03-settings`, `PU-04-catalog`, `PU-04-metadata`, `PU-04-publication`, `PU-04-course`, `PU-04-creative`).
3. **OpenAPI & Fixture Drift**:
   - Full test suite baseline checks in `test_openapi_hash_drift.py` and `test_contract_fixtures.py` reflect expected drifts pending coordinator acceptance and synchronization packet PS-05.

---

PU-04-creative_HANDOFF_READY
