# PU-06-collection Delivery Handoff

Owner repository: `Back-End` (`d:\Project\tahamohammadi-platform\Back-End`)  
Packet: `PU-06-collection`  
Specification: `Docs/05-delivery/concept-alignment-v2/product-packets/PU-06-collection.md`  
Contract: `Docs/03-contracts/PRODUCT-INTERFACES-V2.md` §I05 / §I03 / §I01  
Status: **HANDOFF_READY** (explicitly uncommitted)  
Base commit: `bd6682ea9dae7e5bf6957c36691dc3a94a00ea37`  
Result commit: uncommitted working directory  

---

## 1. Summary of Changes

Extended existing `Collection` model, generic admin registration, and public API with ordered membership and nullable story composition per §I05, §I03, and §I01:

- **Model Definition (`apps/content/models.py`)**:
  - Added nullable `story` foreign key:
    ```python
    story = models.ForeignKey(
        "composition.CompositionPage",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="attached_collections",
    )
    ```
  - Added ordered `members` JSON field:
    ```python
    members = models.JSONField(
        default=list,
        blank=True,
        help_text="Ordered collection members: [{family, id, position}].",
    )
    ```
  - Retained legacy M2M fields (`articles`, `projects`, `publications`) for backward database compatibility.
- **Database Migration (`apps/content/migrations/0029_pu_collection_members.py`)**:
  - Additive migration adding `members` and `story` fields to `Collection`.
  - Added `RunPython` to deterministically migrate existing M2M relationships (`articles`, `projects`, `publications`) into ordered `members`.
  - Verified forward (`migrate content 0029`), backward (`migrate content 0028`), and forward (`migrate content 0029`) migrations cleanly without data loss.
  - Verified `makemigrations --check` is clean with `No changes detected`.
- **Admin Common (`apps/api/admin_common.py`)**:
  - Registered `Collection` in `CONTENT_RELATED_FAMILIES`.
- **Admin Content Schema & CRUD Validation (`apps/api/admin_content.py`)**:
  - Registered `"collection"` in `ENTITY_MODELS`.
  - Configured `DETAIL_FIELD_MAPS["collection"]` with all 13 fields:
    - `description`, `curatorName`, `curatorTitle`, `criteria`, `curatedDate`, `coverMediaId`, `storyId`, `members`, `seoTitle`, `seoDescription`, `socialImageId`, `translationKey`, `relatedRecords`.
  - Added strict member validation and normalization:
    - Verifies member list format and ensures `family` belongs to `CONTENT_RELATED_FAMILIES`.
    - Validates canonical ID format and integer range.
    - Rejects duplicate members within the same collection.
    - Verifies member record exists in database.
    - Enforces exact locale matching between members and collection (`member.locale == collection.locale`).
    - Validates positions as non-negative integers.
    - Cycle Detection (`_check_collection_cycles`): BFS graph traversal across collection members rejects direct and transitive membership loops with 400 `VALIDATION`.
  - Story validation:
    - Rejects non-existent story or invalid composition kind (`kind != "story"`).
    - Enforces exact locale matching between story and collection (`story.locale == collection.locale`).
- **Public API Projections (`apps/api/api.py`)**:
  - Added `Collection` to `PUBLIC_RESOLVER_FAMILIES` and `PUBLIC_ROUTE_FAMILY_MAP`.
  - Extended `_resolve_public_seo` and `_resolve_public_related_records` to support collection summary extraction.
  - Added `_resolve_collection_items` to project ordered members to `WorkRefOut`:
    - Enforces public lifecycle status (`status == "published"`).
    - Enforces exact locale matching (`item.locale == collection.locale`).
    - Orders items deterministically by `position` ascending.
  - Added public schemas: `CollectionCardOut`, `CollectionListOut`, `CollectionDetailOut`.
  - Implemented public endpoints:
    - `GET /v1/collections/{locale}`: Paginated list of published collection cards (`limit`, `offset`, total `count`). Cards exclude `story` and `items`.
    - `GET /v1/collections/{locale}/{slug}`: Detail endpoint returning collection metadata, alternates, SEO, ordered `items: [WorkRefOut]`, and sanitized `story: StoryDocumentOut | None`.
  - Draft isolation:
    - Draft or non-existent collections return 404.
    - Draft story attached to published collection resolves to `None`.
    - Draft members in collection are omitted from public `items`.
- **OpenAPI Schema Export (`docs/contracts/openapi/current/`)**:
  - Re-exported `public-openapi.json`, `admin-openapi.json`, `endpoint-inventory.md`, and updated `PROVENANCE.json`.
- **Automated Test Suite (`tests/test_product_collection.py`)**:
  - Added 5 comprehensive automated tests verifying model fields, admin schema exposure, admin validation & cycle rejection, public list/detail projection, and draft isolation.

---

## 2. Changed Paths (Within Exact Allowlist)

- `apps/content/models.py` (MODIFIED): Added `story` foreign key and `members` JSON field to `Collection`.
- `apps/content/migrations/0029_pu_collection_members.py` (NEW): Additive migration adding `members` and `story_id` columns with data backfill.
- `apps/api/admin_common.py` (MODIFIED): Registered `Collection` in `CONTENT_RELATED_FAMILIES`.
- `apps/api/admin_content.py` (MODIFIED): Registered `collection` in `ENTITY_MODELS` and `DETAIL_FIELD_MAPS`, added member and cycle validation.
- `apps/api/api.py` (MODIFIED): Added collection endpoints, member resolver, and schema projections.
- `tests/test_product_collection.py` (NEW): Test suite covering model, admin, public list/detail, and draft isolation.
- `docs/contracts/openapi/current/public-openapi.json` (MODIFIED): Exported public OpenAPI schema.
- `docs/contracts/openapi/current/admin-openapi.json` (MODIFIED): Exported admin OpenAPI schema.
- `docs/contracts/openapi/current/PROVENANCE.json` (MODIFIED): Updated OpenAPI provenance with regenerated artifact hashes.
- `docs/contracts/openapi/current/endpoint-inventory.md` (MODIFIED): Exported endpoint inventory.
- `docs/quality/product-v2/PU-06-collection-HANDOFF.md` (NEW): This delivery report.

---

## 3. Acceptance and Verification Results

### 3.1 Initial Failing Gap Evidence
Before model and schema implementation:
```bash
uv run pytest tests/test_product_collection.py
```
**Output**: `5 failed in 3.65s`
- `TypeError: Collection() got unexpected keyword arguments: 'story', 'members'`

### 3.2 Targeted Test Suite
```bash
uv run pytest tests/test_product_collection.py -vv
```
**Result**: `5 passed in 3.67s`
- `test_collection_model_has_story_and_members_fields`: PASSED
- `test_admin_content_schema_exposes_collection_entity_and_fields`: PASSED
- `test_admin_crud_collection_with_members_and_story`: PASSED
- `test_public_collections_list_and_detail`: PASSED
- `test_public_collection_draft_isolation`: PASSED

### 3.3 Regressions (Product-V2 Test Suite)
```bash
uv run pytest -k product -q
```
**Result**: `114 passed, 705 deselected in 4.91s`

### 3.4 Code Quality & Formatting
```bash
uv run ruff check .
```
**Result**: `All checks passed!` across the entire repository.

### 3.5 Database Migration Forward/Backward Verification
```bash
uv run python manage.py migrate content 0029
uv run python manage.py migrate content 0028
uv run python manage.py migrate content 0029
uv run python manage.py makemigrations --check
```
**Result**:
- `Unapplying content.0029_pu_collection_members... OK`
- `Applying content.0029_pu_collection_members... OK`
- `makemigrations --check`: `No changes detected`

### 3.6 OpenAPI Export & Artifact Hashes
```bash
uv run python scripts/export_openapi.py
```
**Exported artifacts**:
- `public-openapi.json`:
  - Paths: 46
  - SHA256: `b277f7eedd1447bcf13e5e0777de44079e2fc4f3aff7db3527311e38b2ef57ae`
  - Version: `0.4.0`
- `admin-openapi.json`:
  - Paths: 51
  - SHA256: `a15ce9111639838a175257847aa3a295373d107653038aa5128a031ca5fae336`
  - Version: `0.1.0`
- `endpoint-inventory.md`:
  - Operations: 115
  - SHA256: `02fa620d10f80a8017a00a224cc06ff79755f93caa2db9cdaf6440bdfbbf6409`

---

## 4. UI Changes
None (backend data model, admin API, public API, and OpenAPI contract packet).

---

## 5. Security & Isolation Confirmation
- **Draft Isolation**:
  - Attached story composition is only included in public detail if `story.status == "published"` and `story.locale == collection.locale`. Otherwise, `story` resolves to `None`.
  - Non-published members are omitted from the public collection's `items` list.
  - Draft collections return 404 on both public list and detail endpoints.
- **Cycle and Duplicate Prevention**:
  - Graph cycle detection prevents direct and indirect nested collection loops.
  - Duplicate `(family, id)` pairs within a collection are rejected with 400 `VALIDATION`.
- **Locale Boundary**:
  - Admin validation rejects story attachments with mismatched locale.
  - Admin validation rejects collection members with mismatched locale.
- **Content Sanitization**:
  - Story blocks are sanitized via `public_story_document`, stripping dangerous HTML/attributes.
- **Optimistic Locking**:
  - Admin updates enforce `If-Match` revision concurrency guards.

---

## 6. Dirty Status and Remaining Risks

1. **Central Queue & Dispatch Status**:
   - In `Docs/05-delivery/concept-alignment-v2/execution-tasks.json`, status remains `NOT_STARTED`.
   - Written handoff report does **not** constitute acceptance; coordinator review, acceptance, and commit/merge remain required.
2. **Shared File Coupling**:
   - Working directory contains dirty uncommitted changes across predecessor packets (`PU-03-resolver`, `PU-03-settings`, `PU-04-catalog`, `PU-04-metadata`, `PU-04-publication`, `PU-04-course`, `PU-04-creative`, `PU-05-lessons`, `PU-06-book`, `PU-06-talk`, `PU-06-resource`).
3. **OpenAPI & Fixture Drift**:
   - Baseline hash tests in `test_openapi_hash_drift.py` and `test_contract_fixtures.py` reflect expected drifts pending coordinator acceptance and synchronization packets (`PU-SYNC-public`, `PU-SYNC-admin`).

---

PU-06-collection_HANDOFF_READY
