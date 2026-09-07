# PU-04-metadata Delivery Handoff

Owner repository: `Back-End` (`d:\Project\tahamohammadi-platform\Back-End`)  
Packet: `PU-04-metadata`  
Specification: `Docs/05-delivery/concept-alignment-v2/product-packets/PU-04-metadata.md`  
Contract: `Docs/03-contracts/PRODUCT-INTERFACES-V2.md` §I03  
Status: **HANDOFF_READY** (explicitly uncommitted)  
Base commit: `bd6682ea9dae7e5bf6957c36691dc3a94a00ea37`  
Result commit: uncommitted working directory  

---

## 1. Summary of Changes

Delivered typed publication metadata fields, additive database migration, admin CRUD mappings with validation, and public projections per §I03:

- **Model Mixin and Content Models (`apps/content/models.py`)**:
  - Introduced `ContentPublicationMetadataMixin(models.Model)` with:
    - `seo_title = models.CharField(max_length=200, blank=True, default="")`
    - `seo_description = models.TextField(blank=True, default="")`
    - `social_image = models.ForeignKey("media.Media", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")`
    - `translation_key = models.UUIDField(null=True, blank=True, db_index=True)`
    - `related_records = models.JSONField(default=list, blank=True)`
  - Subclassed mixin across all content models: `Landing`, `Series`, `Article`, `ResearchTopic`, `ResearchStatement`, `Publication`, `Project`, `Book`, `Talk`, `Download`, `Collection`, `Course`, `CreativeWork`.
  - Added `social_image` and `related_records` to `Profile` model (preserving its existing `translation_key` and SEO fields).

- **Additive Migration (`apps/content/migrations/0021_pu_content_metadata.py`)**:
  - Created additive schema migration adding metadata fields to all content tables.
  - Verified forward migration (`0021`), backward rollback (`0020`), and re-application (`0021`) on database.

- **Admin Content Mappings & Validation (`apps/api/admin_content.py`)**:
  - Registered camelCase keys in `DETAIL_FIELD_MAPS` for all 13 content entities:
    - `"seo_title": "seoTitle"`
    - `"seo_description": "seoDescription"`
    - `"social_image": "socialImageId"`
    - `"translation_key": "translationKey"`
    - `"related_records": "relatedRecords"`
  - Updated `_field_type` to map `JSONField` to `"json"` widget type.
  - In `_coerce_field_value`:
    - Validated `social_image` references active image Media rows.
    - Validated `translationKey` as canonical UUID.
    - Validated `relatedRecords` as array of `{family, id}`: enforces allowed model families from `RESOLVER_FAMILIES`, canonical ASCII positive decimal IDs (`^[1-9][0-9]*$`, fits 64-bit int), rejects leading zeros, non-ASCII digits, and unknown keys.
  - In `_detail_response`: serialized `translation_key` to string (or null).

- **Public API Projections (`apps/api/api.py`)**:
  - Defined `PublicSeoOut` (`title`, `description`, `image`).
  - Defined `AlternateLocaleOut` (`locale`, `slug`, `routeFamily`, `courseSlug`).
  - Defined `PublicPublicationMetadataMixinOut` with resolvers:
    - `_resolve_public_seo`: falls back to record's own title/summary, resolves social/featured/cover media URL.
    - `_resolve_public_alternates`: resolves published siblings sharing the explicit `translation_key` (excluding current record), mapped to canonical route family.
    - `_resolve_public_related_records`: validates each reference, queries published records in the exact same locale, omitting drafts, cross-locale records, or non-existent items, and constructs canonical `WorkRefOut`.
  - Applied `PublicPublicationMetadataMixinOut` across all public detail schemas: `ArticleDetailOut`, `ResearchTopicDetailOut`, `ResearchStatementOut`, `ProjectDetailOut`, `PublicationDetailOut`, `BookDetailOut`, `TalkDetailOut`, `DownloadDetailOut`, `CourseDetailOut`, `CreativeWorkDetailOut`.

- **Test Suite (`tests/test_product_metadata.py`)**:
  - Implemented 7 targeted unit and integration tests covering:
    - Model field presence across all content models.
    - Admin schema exposure of camelCase metadata fields.
    - Admin PUT and GET persistence under optimistic locking (`If-Match`).
    - Admin validation rejection of malformed related records (bad family, leading zero ID).
    - Public SEO fallback to title and excerpt.
    - Public translation alternates resolution (published only).
    - Public related records resolution with draft and cross-locale omission.

---

## 2. Changed Paths (Within Exact Allowlist)

- `apps/content/models.py` (MODIFIED): Added `ContentPublicationMetadataMixin` and applied to content models.
- `apps/content/migrations/0021_pu_content_metadata.py` (NEW): Additive database migration.
- `apps/api/admin_content.py` (MODIFIED): Extended `DETAIL_FIELD_MAPS`, validation for UUID and relatedRecords, and detail serialization.
- `apps/api/api.py` (MODIFIED): Exposed `seo`, `alternates`, and `relatedRecords` across all detail projections.
- `tests/test_product_metadata.py` (NEW): Test suite covering model fields, admin CRUD, and public projections.
- `docs/contracts/openapi/current/public-openapi.json` (MODIFIED): Source-exported public schema reflecting new projections.
- `docs/contracts/openapi/current/admin-openapi.json` (MODIFIED): Source-exported admin schema reflecting metadata fields.
- `docs/contracts/openapi/current/PROVENANCE.json` (MODIFIED): Updated OpenAPI provenance with regenerated hashes.
- `docs/contracts/openapi/current/endpoint-inventory.md` (MODIFIED): Updated endpoint inventory.
- `docs/quality/product-v2/PU-04-metadata-HANDOFF.md` (NEW): This delivery report.

---

## 3. Acceptance and Verification Results

### 3.1 Targeted Test Suite
```bash
uv run pytest tests/test_product_metadata.py -vv
```
**Result**: `7 passed in 2.98s`
- `test_models_have_metadata_fields`: PASSED
- `test_admin_content_schema_exposes_metadata_fields`: PASSED
- `test_admin_put_and_get_metadata_fields`: PASSED
- `test_admin_validates_related_records`: PASSED
- `test_public_article_detail_seo_fallback`: PASSED
- `test_public_article_detail_alternates`: PASSED
- `test_public_article_detail_related_records_resolution`: PASSED

### 3.2 Full Repository Test Suite (`uv run pytest`)
```bash
uv run pytest
```
**Result**: `758 passed, 13 failed in 22.33s` (771 collected total)

Breakdown of the 13 failing cases:
1. **OpenAPI baseline drift (`tests/test_openapi_hash_drift.py`) — 9 failures**:
   - `test_accepted_artifact_content_unchanged[admin-openapi.json, endpoint-inventory.md, public-openapi.json]`
   - `test_canonical_content_reencodes_to_accepted_hash[admin-openapi.json, endpoint-inventory.md, public-openapi.json]`
   - `test_artifact_shape_matches_acceptance[public-openapi.json, admin-openapi.json]`
   - `test_provenance_matches_accepted_record`
   *Reason*: Expected drift per `PRODUCT-INTERFACES-V2.md §I08` and `Docs/03-contracts/OPENAPI-ACCEPTANCE.md`. Exported schemas contain additive metadata fields and new endpoints (`/v1/site/{locale}`, `/v1/records/{locale}/resolve`); hash re-pinning is owned by downstream synchronization packets (PS-05 / ADR-0010).
2. **Contract fixture snapshot checks (`tests/test_contract_fixtures.py`) — 3 failures**:
   - `test_fixture_articles_detail`, `test_fixture_publication_detail`, `test_fixture_project_detail`
   *Reason*: The additive public projections (`seo`, `alternates`, `relatedRecords`) added to detail endpoints in this packet expand the returned keys beyond the old V1 fixture snapshots. Fixtures require regeneration (`CONTRACT_FIXTURES_WRITE=1`) upon coordinator acceptance.
3. **Legacy API field set assert (`tests/test_api.py`) — 1 failure**:
   - `test_detail_article_by_slug`
   *Reason*: Strict set equality `assert set(data) == ARTICLE_DETAIL_FIELDS` failed because `data` now carries the additive contract fields `{'seo', 'alternates', 'relatedRecords'}`. Note that `tests/test_api.py` is outside the write allowlist for `PU-04-metadata` and was not modified.
4. **Admin Revisions & Lifecycle**:
   - `tests/test_admin_revisions_schedule.py`: `6 passed` (resolved nullable UUID handling during restore-as-draft).

### 3.3 Code Quality & Formatting
```bash
uv run ruff check .
```
**Result**: `All checks passed!` across the entire working tree (including `tests/test_product_story_blocks.py`).

### 3.4 Database Migration Forward/Backward Verification
```bash
uv run python manage.py migrate content 0021
uv run python manage.py migrate content 0020
uv run python manage.py migrate content 0021
```
**Result**: Migration applies, rolls back cleanly, and re-applies without data loss or integrity issues.

### 3.5 OpenAPI Export & Artifact Hashes
```bash
uv run python scripts/export_openapi.py
```
**Exported artifacts**:
- `public-openapi.json`:
  - Paths: 42
  - SHA256: `18f5f9010163ef3d7ff374e6c3142e17b2addd0f3c5d2e7b4a76f7ee4c7013eb`
- `admin-openapi.json`:
  - Paths: 51
  - SHA256: `a15ce9111639838a175257847aa3a295373d107653038aa5128a031ca5fae336`
- `endpoint-inventory.md`:
  - Operations: 111
  - SHA256: `05bf86513574c7fc397d6121a892422245e78b2b614da43e8a07547ea4a69e50`

---

## 4. UI Changes
None (backend API, database models, and OpenAPI schema packet).

---

## 5. Security & Isolation Confirmation
- **Exact-Locale Isolation**: `relatedRecords` only resolves items published in the exact same locale as the requesting record. Cross-locale references are safely omitted.
- **Publication Gating**: Only items in `LifecycleStatus.PUBLISHED` with `published_at <= now` are resolved. Draft, review, scheduled, or archived records are never leaked through public projections.
- **Fail-Closed Validation**: Non-canonical IDs (leading zeros, negative values, non-ASCII digits, out-of-range 64-bit integers) and unknown model families return HTTP 400 validation errors in the admin API.
- **Optimistic Locking**: Maintained via `If-Match` headers for all admin updates.

---

## 6. Dirty Status and Remaining Risks

1. **Central Queue & Dispatch Status**:
   - In the coordinator's central register (`Docs/05-delivery/concept-alignment-v2/execution-tasks.json`), the packets remain in their pre-delivery statuses:
     - `PU-03-resolver`: `REVISE` (coordinator review R1 pending resolution)
     - `PU-03-settings`: `NOT_STARTED` (handoff produced, unreviewed/unmerged)
     - `PU-04-catalog`: `NOT_STARTED` (handoff produced, unreviewed/unmerged)
     - `PU-04-metadata`: `NOT_STARTED` (this packet, handoff produced)
   - A written handoff report does **not** constitute acceptance; coordinator review, acceptance, and commit/merge remain required.

2. **Shared File Coupling on `apps/api/api.py`**:
   - `apps/api/api.py` is concurrently dirty with uncommitted changes spanning four successive packets:
     - `PU-03-resolver` (record resolver endpoint `/v1/records/{locale}/resolve`)
     - `PU-03-settings` (localized settings endpoint `/v1/site/{locale}`)
     - `PU-04-catalog` (story catalog blocks & public projection)
     - `PU-04-metadata` (publication metadata projections `seo`, `alternates`, `relatedRecords`)
   - If any predecessor packet (such as `PU-03-resolver` under `REVISE`) requires breaking edits or file resets, `apps/api/api.py` carries significant merge collision and regression risk.

3. **Tree Lint Sensitivity**:
   - Although `ruff check .` currently passes cleanly (`All checks passed!`), uncommitted files across packets must continue to be linted together to prevent cross-packet lint breakages.

4. **OpenAPI & Fixture Drift**:
   - Full suite will show 13 failing tests (`test_openapi_hash_drift.py`, `test_contract_fixtures.py`, `test_api.py`) until coordinator acceptance and synchronization packet PS-05 re-pin hashes and regenerate fixtures.

---

PU-04-metadata_HANDOFF_READY
