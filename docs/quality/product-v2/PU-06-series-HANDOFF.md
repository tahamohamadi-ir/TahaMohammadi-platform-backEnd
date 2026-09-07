# PU-06-series Delivery Handoff

Owner repository: `Back-End` (`d:\Project\tahamohammadi-platform\Back-End`)  
Packet: `PU-06-series`  
Specification: `Docs/05-delivery/concept-alignment-v2/product-packets/PU-06-series.md`  
Contract: `Docs/03-contracts/PRODUCT-INTERFACES-V2.md` §I05 / §I03 / §I01  
Status: **HANDOFF_READY** (explicitly uncommitted)  
Base commit: `bd6682ea9dae7e5bf6957c36691dc3a94a00ea37`  
Result commit: uncommitted working directory  

---

## 1. Summary of Changes

Extended existing `Series` model, generic admin editor map, and public API with ordered article membership and nullable story composition per §I05, §I03, and §I01:

- **Model Definition (`apps/content/models.py`)**:
  - Added nullable `story` foreign key to `Series`:
    ```python
    story = models.ForeignKey(
        "composition.CompositionPage",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="attached_series",
    )
    ```
  - Added ordered `members` JSON field:
    ```python
    members = models.JSONField(
        default=list,
        blank=True,
        help_text="Ordered series members: [{family: 'article', id, position}].",
    )
    ```
  - Preserved existing `ordering` field and `Article.series` M2M relation.
- **Database Migration (`apps/content/migrations/0030_pu_series_members.py`)**:
  - Additive migration adding `members` and `story_id` columns to `content_series`.
  - Added `RunPython` data migration backfilling existing M2M article associations into ordered `members`.
  - Verified forward (`migrate content 0030`), backward (`migrate content 0029`), and re-application (`migrate content 0030`) cleanly without data loss.
  - Verified `makemigrations --check` clean with `No changes detected`.
- **Admin Content Schema & CRUD Validation (`apps/api/admin_content.py`)**:
  - Registered `"story": "storyId"` and `"members": "members"` in `DETAIL_FIELD_MAPS["series"]`.
  - Generic admin schema `/api/v1/admin/content/schema` exposes `storyId` and `members` under `series`.
  - Enforced series member constraint (contract §I05: *members are ordered articles only*):
    - Rejects any member where `family != "article"` with 400 `VALIDATION`.
    - Rejects duplicate articles in the same series with 400 `VALIDATION`.
    - Validates canonical positive integer ID and article existence.
    - Enforces exact locale matching between articles and series (`article.locale == series.locale`).
    - Validates member positions as non-negative integers.
  - Validates `storyId` (must be `kind == "story"` and matching locale).
  - Synchronizes `series.articles` M2M on `content_create` and `content_update` whenever `members` is supplied or updated, ensuring compatibility with `Article.series` queries and filters.
- **Public API Projections (`apps/api/api.py`)**:
  - Added `SeriesDetailOut` schema with `locale`, `slug`, `title`, `description`, `story`, `items: [WorkRefOut]`, `seo`, and `alternates`.
  - Implemented `_resolve_series_items`:
    - Resolves members strictly matching locale and `Article.objects.public()`.
    - Preserves explicit `position` ascending ordering.
    - Fallbacks gracefully to existing `series.articles` M2M if `members` JSON is empty.
  - Implemented endpoint:
    - `GET /v1/series/{locale}/{slug}`: Published series detail returning ordered articles and sanitized story.
  - Preserved existing endpoints:
    - `GET /api/series/{locale}`: List of published series for locale.
    - `GET /api/articles/{locale}?series={slug}`: Article filtering by series slug.
  - Draft isolation:
    - Draft or non-existent series returns 404.
    - Draft story attached to published series resolves to `None`.
    - Draft article member in series is omitted from public `items`.
    - Invalid locale returns 404.
- **OpenAPI Schema Export (`docs/contracts/openapi/current/`)**:
  - Re-exported `public-openapi.json`, `admin-openapi.json`, `endpoint-inventory.md`, and updated `PROVENANCE.json`.
- **Automated Test Suite (`tests/test_product_series.py`)**:
  - Added 5 comprehensive automated tests verifying model fields, admin schema exposure, admin validation & article-only member enforcement, public detail projection with ordered items, and draft isolation.

---

## 2. Changed Paths (Within Exact Allowlist)

- `apps/content/models.py` (MODIFIED): Added `story` foreign key and `members` JSON field to `Series`.
- `apps/content/migrations/0030_pu_series_members.py` (NEW): Additive migration adding `members` and `story_id` columns with data backfill.
- `apps/api/admin_content.py` (MODIFIED): Registered `storyId` and `members` in `DETAIL_FIELD_MAPS["series"]`, added article-only member validation and M2M sync.
- `apps/api/api.py` (MODIFIED): Added `SeriesDetailOut`, `_resolve_series_items`, and `GET /v1/series/{locale}/{slug}` detail endpoint.
- `tests/test_product_series.py` (NEW): Automated test suite for PU-06-series.
- `docs/contracts/openapi/current/public-openapi.json` (MODIFIED): Exported public OpenAPI schema.
- `docs/contracts/openapi/current/admin-openapi.json` (MODIFIED): Exported admin OpenAPI schema.
- `docs/contracts/openapi/current/PROVENANCE.json` (MODIFIED): Updated OpenAPI provenance with regenerated artifact hashes.
- `docs/contracts/openapi/current/endpoint-inventory.md` (MODIFIED): Exported endpoint inventory.
- `docs/quality/product-v2/PU-06-series-HANDOFF.md` (NEW): This delivery report.

---

## 3. Acceptance and Verification Results

### 3.1 Initial Failing Gap Evidence
Before model and schema implementation:
```bash
uv run pytest tests/test_product_series.py
```
**Output**: `5 failed in 3.70s`
- `TypeError: Series() got unexpected keyword arguments: 'story', 'members'`

### 3.2 Targeted Test Suite
```bash
uv run pytest tests/test_product_series.py -vv
```
**Result**: `5 passed in 3.82s`
- `test_series_model_has_story_and_members_fields`: PASSED
- `test_admin_content_schema_exposes_series_entity_and_fields`: PASSED
- `test_admin_crud_series_with_members_and_story`: PASSED
- `test_public_series_detail_and_members`: PASSED
- `test_public_series_draft_isolation`: PASSED

### 3.3 Regressions (Product-V2 Test Suite)
```bash
uv run pytest -k product -q
```
**Result**: `119 passed, 705 deselected in 5.38s`

### 3.4 Code Quality & Formatting
```bash
uv run ruff check .
```
**Result**: `All checks passed!` across the entire repository.

### 3.5 Database Migration Forward/Backward Verification
```bash
uv run python manage.py migrate content 0030
uv run python manage.py migrate content 0029
uv run python manage.py migrate content 0030
uv run python manage.py makemigrations --check
```
**Result**:
- `Unapplying content.0030_pu_series_members... OK`
- `Applying content.0030_pu_series_members... OK`
- `makemigrations --check`: `No changes detected`

### 3.6 OpenAPI Export & Artifact Hashes
```bash
uv run python scripts/export_openapi.py
```
**Exported artifacts**:
- `public-openapi.json`:
  - Paths: 47
  - SHA256: `1642d70c168bc2d1ea6bf3b3b122cb81ccf2074f6fdf0906bc999e1e9af22377`
  - Version: `0.4.0`
- `admin-openapi.json`:
  - Paths: 51
  - SHA256: `a15ce9111639838a175257847aa3a295373d107653038aa5128a031ca5fae336`
  - Version: `0.1.0`
- `endpoint-inventory.md`:
  - Operations: 116
  - SHA256: `6264456fc65c801637e6a12049ed0b76d815337fe3343003510d1823d5de56c9`

---

## 4. UI Changes
None (backend data model, admin API, public API, and OpenAPI contract packet).

---

## 5. Security & Isolation Confirmation
- **Draft Isolation**:
  - Attached story composition is only included in public series detail if `story.status == "published"` and `story.locale == series.locale`. Otherwise, `story` resolves to `None`.
  - Non-published articles are omitted from the public series `items` list.
  - Draft series return 404 on public detail endpoint.
- **Family Constraint & Member Validation**:
  - Series members are strictly restricted to `family == "article"`.
  - Duplicate article members within the same series are rejected with 400 `VALIDATION`.
- **Locale Boundary**:
  - Admin validation rejects story attachments with mismatched locale.
  - Admin validation rejects article members with mismatched locale.
- **Content Sanitization**:
  - Story blocks are sanitized via `public_story_document`.
- **Optimistic Locking**:
  - Admin updates enforce `If-Match` revision concurrency guards.

---

## 6. Dirty Status and Remaining Risks

1. **Central Queue & Dispatch Status**:
   - In `Docs/05-delivery/concept-alignment-v2/execution-tasks.json`, status remains `NOT_STARTED`.
   - Written handoff report does **not** constitute acceptance; coordinator review, acceptance, and commit/merge remain required.
2. **Shared File Coupling**:
   - Working directory contains dirty uncommitted changes across predecessor packets (`PU-03-resolver`, `PU-03-settings`, `PU-04-catalog`, `PU-04-metadata`, `PU-04-publication`, `PU-04-course`, `PU-04-creative`, `PU-05-lessons`, `PU-06-book`, `PU-06-talk`, `PU-06-resource`, `PU-06-collection`).
3. **OpenAPI & Fixture Drift**:
   - Baseline hash tests in `test_openapi_hash_drift.py` and `test_contract_fixtures.py` reflect expected drifts pending coordinator acceptance and synchronization packets (`PU-SYNC-public`, `PU-SYNC-admin`).

---

PU-06-series_HANDOFF_READY
