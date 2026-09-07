# PU-06-talk Delivery Handoff

Owner repository: `Back-End` (`d:\Project\tahamohammadi-platform\Back-End`)  
Packet: `PU-06-talk`  
Specification: `Docs/05-delivery/concept-alignment-v2/product-packets/PU-06-talk.md`  
Contract: `Docs/03-contracts/PRODUCT-INTERFACES-V2.md` §I01/I03  
Status: **HANDOFF_READY** (explicitly uncommitted)  
Base commit: `bd6682ea9dae7e5bf6957c36691dc3a94a00ea37`  
Result commit: uncommitted working directory  

---

## 1. Summary of Changes

Extended existing `Talk` model, generic admin editor map, and public detail endpoint with nullable story composition and metadata projections per §I01/§I03:

- **Model Definition (`apps/content/models.py`)**:
  - Added nullable `story` ForeignKey to `Talk`:
    ```python
    story = models.ForeignKey(
        "composition.CompositionPage",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="attached_talks",
    )
    ```
- **Database Migration (`apps/content/migrations/0027_pu_talk_story.py`)**:
  - Generated additive migration adding `story_id` column to table `content_talk`.
  - Verified forward (`migrate content 0027`), backward (`migrate content 0026`), and forward (`migrate content 0027`) migrations without data loss or integrity issues.
  - Verified `makemigrations --check` clean with `No changes detected`.
- **Admin Content Schema & CRUD (`apps/api/admin_content.py`)**:
  - Registered `"story": "storyId"` in `DETAIL_FIELD_MAPS["talk"]`.
  - Generic admin schema `/api/v1/admin/content/schema` exposes `storyId` (type: `"number"`) under `talk`.
  - Admin `content_create` and `content_update` validate `storyId`:
    - Fails closed (400 `VALIDATION`) if referenced ID is not a story composition (`kind != "story"`).
    - Fails closed (400 `VALIDATION`) if referenced story locale does not match talk locale (`story.locale != item.locale`).
    - Serializes `story_id` under `fields.storyId` in detail responses.
- **Public API Projections (`apps/api/api.py`)**:
  - Added `story: StoryDocumentOut | None = None` to `TalkDetailOut`.
  - Implemented `resolve_story` on `TalkDetailOut` using `public_story_document(getattr(obj, "story", None), obj.locale)`.
  - Optimized `get_talk` query with `.select_related("slides_media", "story", "social_image")`.
  - Preserved existing endpoints `GET /api/talks/{locale}` and `GET /api/talks/{locale}/{slug}`.
  - Public detail projection automatically exposes `story`, `seo`, `alternates`, and `relatedRecords` via `PublicPublicationMetadataMixinOut`.
  - Public draft isolation: draft story attached to a published talk resolves to `None`.
- **OpenAPI Schema Export (`docs/contracts/openapi/current/`)**:
  - Re-exported `public-openapi.json`, `admin-openapi.json`, `endpoint-inventory.md`, and updated `PROVENANCE.json`.
- **Test Suite (`tests/test_product_talk.py`)**:
  - Added 5 comprehensive automated tests verifying model relation, admin schema exposure, admin validation and CRUD, public detail projections, and draft isolation.

---

## 2. Changed Paths (Within Exact Allowlist)

- `apps/content/models.py` (MODIFIED): Added `story` foreign key to `Talk`.
- `apps/content/migrations/0027_pu_talk_story.py` (NEW): Migration adding `story_id` column to `content_talk`.
- `apps/api/admin_content.py` (MODIFIED): Added `"story": "storyId"` to `DETAIL_FIELD_MAPS["talk"]`.
- `apps/api/api.py` (MODIFIED): Added `story` field and resolver to `TalkDetailOut`, updated `get_talk` query.
- `tests/test_product_talk.py` (NEW): Test suite covering model, admin, and public endpoints.
- `docs/contracts/openapi/current/public-openapi.json` (MODIFIED): Exported public schema with `TalkDetailOut.story`.
- `docs/contracts/openapi/current/admin-openapi.json` (MODIFIED): Exported admin schema with `talk.storyId`.
- `docs/contracts/openapi/current/PROVENANCE.json` (MODIFIED): Updated OpenAPI provenance with regenerated artifact hashes.
- `docs/contracts/openapi/current/endpoint-inventory.md` (MODIFIED): Exported endpoint inventory.
- `docs/quality/product-v2/PU-06-talk-HANDOFF.md` (NEW): This delivery report.

---

## 3. Acceptance and Verification Results

### 3.1 Initial Failing Gap Evidence
Before applying model and schema changes:
```bash
uv run pytest tests/test_product_talk.py
```
**Output**: `5 failed in 3.24s`
- `AssertionError: Talk does not have 'story'`
- `AssertionError: 'storyId' not in fields`
- `TypeError: Talk() got unexpected keyword arguments: 'story'`

### 3.2 Targeted Test Suite
```bash
uv run pytest tests/test_product_talk.py -vv
```
**Result**: `5 passed in 3.06s`
- `test_talk_model_has_story_field`: PASSED
- `test_admin_content_schema_exposes_talk_story_id`: PASSED
- `test_admin_crud_talk_story`: PASSED
- `test_public_talk_detail_exposes_story_and_metadata`: PASSED
- `test_public_talk_detail_draft_isolation`: PASSED

### 3.3 Regressions (Product-V2 Test Suite)
```bash
uv run pytest tests/test_product_course_story.py tests/test_product_creative_story.py tests/test_product_publication_story.py tests/test_product_metadata.py tests/test_product_record_resolver.py tests/test_product_localized_settings.py tests/test_product_lessons.py tests/test_product_book.py tests/test_product_talk.py -vv
```
**Result**: `89 passed in 3.81s`

### 3.4 Code Quality & Formatting
```bash
uv run ruff check .
```
**Result**: `All checks passed!` across the entire repository.

### 3.5 Database Migration Forward/Backward Verification
```bash
uv run python manage.py migrate content 0027
uv run python manage.py migrate content 0026
uv run python manage.py migrate content 0027
uv run python manage.py makemigrations --check
```
**Result**:
- `Unapplying content.0027_pu_talk_story... OK`
- `Applying content.0027_pu_talk_story... OK`
- `makemigrations --check`: `No changes detected`

### 3.6 OpenAPI Export & Artifact Hashes
```bash
uv run python scripts/export_openapi.py
```
**Exported artifacts**:
- `public-openapi.json`:
  - Paths: 44
  - SHA256: `bbc465c3690ffebf5974f87098c5a3e26aebcd95e70b632855d631606d42c75b`
  - Version: `0.4.0`
- `admin-openapi.json`:
  - Paths: 51
  - SHA256: `a15ce9111639838a175257847aa3a295373d107653038aa5128a031ca5fae336`
  - Version: `0.1.0`
- `endpoint-inventory.md`:
  - Operations: 113
  - SHA256: `69ad4d1a83651ff643accf01fb366580070e629653e970106f86b4e38bede6a8`

---

## 4. UI Changes
None (backend data model, admin API, public API, and OpenAPI contract packet).

---

## 5. Security & Isolation Confirmation
- **Draft Isolation**: Attached story composition is only included in public detail if `story.status == "published"` and `story.locale == talk.locale`. Otherwise, `story` resolves to `None`.
- **Content Sanitization**: Story blocks are sanitized via `public_story_document`, stripping scripts, dangerous attributes, and filtering disabled sections.
- **Fail-Closed Admin Validation**: Rejects missing story IDs, non-story compositions (`kind != "story"`), and cross-locale story attachments (`story.locale != item.locale`).
- **Optimistic Locking**: Enforced via `If-Match` headers on admin updates.

---

## 6. Dirty Status and Remaining Risks

1. **Central Queue & Dispatch Status**:
   - In `Docs/05-delivery/concept-alignment-v2/execution-tasks.json`, status remains `NOT_STARTED`.
   - Written handoff report does **not** constitute acceptance; coordinator review, acceptance, and commit/merge remain required.
2. **Shared File Coupling**:
   - Working directory contains dirty uncommitted changes across predecessor packets (`PU-03-resolver`, `PU-03-settings`, `PU-04-catalog`, `PU-04-metadata`, `PU-04-publication`, `PU-04-course`, `PU-04-creative`, `PU-05-lessons`, `PU-06-book`).
3. **OpenAPI & Fixture Drift**:
   - Baseline hash tests in `test_openapi_hash_drift.py` and `test_contract_fixtures.py` reflect expected drifts pending coordinator acceptance and synchronization packets (`PU-SYNC-public`, `PU-SYNC-admin`).

---

PU-06-talk_HANDOFF_READY
