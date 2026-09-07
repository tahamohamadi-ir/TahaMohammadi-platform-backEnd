# PU-05-lessons Delivery Handoff

Owner repository: `Back-End` (`d:\Project\tahamohammadi-platform\Back-End`)  
Packet: `PU-05-lessons`  
Specification: `Docs/05-delivery/concept-alignment-v2/product-packets/PU-05-lessons.md`  
Contract: `Docs/03-contracts/PRODUCT-INTERFACES-V2.md` §I05  
Status: **HANDOFF_READY** (explicitly uncommitted)  
Base commit: `bd6682ea9dae7e5bf6957c36691dc3a94a00ea37`  
Result commit: uncommitted working directory  

---

## 1. Summary of Changes

Added independent course lessons with stable localized slugs, parent publication guards, ordered neighbors, and admin registration per contract §I05:

- **Model Definition (`apps/content/models.py`)**:
  - Implemented `Lesson(LocalizedContentMixin, ContentPublicationMetadataMixin, LifecycleMixin)`:
    - Required `course` ForeignKey to `Course` (`on_delete=models.CASCADE`, `related_name="lessons"`).
    - `position` (`models.PositiveIntegerField(default=0)`).
    - `summary` (`models.TextField(blank=True)`).
    - `story` (`ForeignKey("composition.CompositionPage", null=True, blank=True, on_delete=models.SET_NULL)`).
    - Inherits `(locale, slug, title)` from `LocalizedContentMixin`.
    - Inherits `(seo_title, seo_description, social_image, translation_key, related_records)` from `ContentPublicationMetadataMixin`.
    - Inherits `(status, created_at, updated_at, published_at, scheduled_for)` and `.public()` manager from `LifecycleMixin`.
    - Enforced `UniqueConstraint(fields=["course", "locale", "slug"], name="content_lesson_unique_course_locale_slug")`.
    - Implemented `clean()` validating that parent `course.locale == lesson.locale`.
- **Database Migration (`apps/content/migrations/0025_pu_lessons.py`)**:
  - Created migration creating table `content_lesson` with constraints and indexes.
  - Verified forward (`migrate content 0025`), backward (`migrate content 0024`), and forward (`migrate content 0025`) migrations without data loss or integrity issues.
  - Verified `makemigrations --check` detects no pending changes.
- **Admin Common Registry (`apps/api/admin_common.py`)**:
  - Imported `Course`, `CreativeWork`, `Lesson` from `apps.content.models`.
  - Preserved wire compatibility of `GRAPH_RELATED_FAMILIES` (for legacy graph activation tests).
  - Defined `CONTENT_RELATED_FAMILIES` extending with `"course"`, `"creativework"`, and `"lesson"`.
- **Admin Content Management (`apps/api/admin_content.py`)**:
  - Registered `"lesson": Lesson` in `ENTITY_MODELS`.
  - Defined `DETAIL_FIELD_MAPS["lesson"]` exposing: `courseId`, `position`, `summary`, `storyId`, `seoTitle`, `seoDescription`, `socialImageId`, `translationKey`, `relatedRecords`.
  - In `content_create`:
    - Enforced required `courseId`.
    - Enforced `courseId` locale strictly matches `lesson.locale`.
    - Enforced unique slug constraint scoped to `(course, locale, slug)`.
    - Enforced `storyId` locale strictly matches `lesson.locale`.
  - In `content_update`:
    - Enforced `(course, locale, slug)` uniqueness upon slug modification.
    - Enforced `courseId` locale matching if updated.
- **Public API Endpoints & Projections (`apps/api/api.py`)**:
  - Added `PUBLIC_RESOLVER_FAMILIES` and `PUBLIC_ROUTE_FAMILY_MAP` mapping `"lesson": "education"`.
  - Updated `_resolve_public_alternates` and `_resolve_public_related_records` to supply `courseSlug` for lesson references.
  - Defined schemas:
    - `LessonCardOut(locale, slug, title, summary, courseSlug, position)`
    - `LessonListOut(count, items)`
    - `LessonDetailOut(locale, slug, title, summary, courseSlug, position, story, resources, previous, next, seo, alternates)`
  - Implemented `GET /api/v1/lessons/{locale}?course={courseSlug}`:
    - Verifies parent course is published in `locale`. Returns `{count: 0, items: []}` if parent course is draft or missing.
    - Excludes draft lessons.
    - Orders items by `position, id`.
  - Implemented `GET /api/v1/lessons/{locale}/{courseSlug}/{lessonSlug}`:
    - Guards against draft/missing parent course (returns 404).
    - Guards against draft/missing lesson (returns 404).
    - Resolves ordered published neighbors (`previous` and `next`) as `WorkRefOut` (`family="lesson"`, `routeFamily="education"`, `courseSlug=parent_course.slug`), skipping drafts.
    - Projects sanitized public `story`, `resources` (resolved related records), `seo`, and `alternates` with `courseSlug`.
- **OpenAPI Schema Export (`docs/contracts/openapi/current/`)**:
  - Re-exported `public-openapi.json`, `admin-openapi.json`, `endpoint-inventory.md`, and updated `PROVENANCE.json`.
- **Test Suite (`tests/test_product_lessons.py`)**:
  - Added 9 comprehensive automated tests verifying all model constraints, admin CRUD/validation, public list, public detail, ordered neighbors skipping drafts, draft isolation, parent publication guard, translation alternates, and WorkRef resolution.

---

## 2. Changed Paths (Within Exact Allowlist)

- `apps/content/models.py` (MODIFIED): Defined `Lesson` model with Course FK, position, summary, story, and unique constraints.
- `apps/content/migrations/0025_pu_lessons.py` (NEW): Migration creating `content_lesson` table.
- `apps/api/admin_common.py` (MODIFIED): Added `Lesson` import and `CONTENT_RELATED_FAMILIES`.
- `apps/api/admin_content.py` (MODIFIED): Registered `lesson` entity, field mappings, and course validation in admin endpoints.
- `apps/api/api.py` (MODIFIED): Added `LessonCardOut`, `LessonListOut`, `LessonDetailOut`, and `/api/v1/lessons` endpoints.
- `tests/test_product_lessons.py` (NEW): Test suite covering model, admin endpoints, and public endpoints.
- `docs/contracts/openapi/current/public-openapi.json` (MODIFIED): Exported public schema with lesson endpoints and models.
- `docs/contracts/openapi/current/admin-openapi.json` (MODIFIED): Exported admin schema with lesson entity schema.
- `docs/contracts/openapi/current/PROVENANCE.json` (MODIFIED): Updated OpenAPI provenance with regenerated artifact hashes.
- `docs/contracts/openapi/current/endpoint-inventory.md` (MODIFIED): Exported endpoint inventory with lesson routes.
- `docs/quality/product-v2/PU-05-lessons-HANDOFF.md` (NEW): This delivery report.

---

## 3. Acceptance and Verification Results

### 3.1 Initial Failing Gap Evidence
Before implementing admin and public endpoints:
```bash
uv run pytest tests/test_product_lessons.py
```
**Output**: `5 failed, 1 passed in 3.14s`
- `AssertionError: assert 'lesson' in entities`
- `AssertionError: assert 404 == 400` on `/api/v1/admin/content/lesson`
- `AssertionError: assert 404 == 200` on `/api/v1/lessons/fa?course=algebra-101`
- `AssertionError: assert 404 == 200` on `/api/v1/lessons/fa/cs-101/variables`

### 3.2 Targeted Test Suite
```bash
uv run pytest tests/test_product_lessons.py -vv
```
**Result**: `9 passed in 3.10s`
- `test_lesson_model_declaration_and_clean`: PASSED
- `test_admin_content_schema_exposes_lesson`: PASSED
- `test_admin_crud_lesson`: PASSED
- `test_public_lesson_list`: PASSED
- `test_public_lesson_detail_with_ordered_neighbors`: PASSED
- `test_public_lesson_draft_isolation_and_parent_publication_guard`: PASSED
- `test_public_lesson_ordered_neighbors_skip_drafts`: PASSED
- `test_public_lesson_alternates_with_course_slug`: PASSED
- `test_lesson_as_related_record_work_ref`: PASSED

### 3.3 Regressions (Product-V2 Test Suite)
```bash
uv run pytest tests/test_product_course_story.py tests/test_product_creative_story.py tests/test_product_publication_story.py tests/test_product_metadata.py tests/test_product_record_resolver.py tests/test_product_localized_settings.py tests/test_product_lessons.py -vv
```
**Result**: `79 passed in 3.69s`

### 3.4 Code Quality & Formatting
```bash
uv run ruff check .
```
**Result**: `All checks passed!` across the entire repository.

### 3.5 Database Migration Forward/Backward Verification
```bash
uv run python manage.py migrate content 0024
uv run python manage.py migrate content 0025
uv run python manage.py makemigrations --check
```
**Result**:
- `Unapplying content.0025_pu_lessons... OK`
- `Applying content.0025_pu_lessons... OK`
- `makemigrations --check`: `No changes detected`

### 3.6 OpenAPI Export & Artifact Hashes
```bash
uv run python scripts/export_openapi.py
```
**Exported artifacts**:
- `public-openapi.json`:
  - Paths: 44
  - SHA256: `df17b376ac8e3c1c15f99c3409f53cf68afd2fd89df2f5cd2d35eab21fcd194b`
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
- **Parent Publication Guard**: Detail endpoint returns 404 and list endpoint returns count 0 if the parent course is not published in the requested locale.
- **Draft Isolation**: Draft lessons are never visible in the public list, return 404 on public detail, and are never selected as `previous` or `next` neighbors.
- **Exact-Locale Enforcement**: `Lesson.clean()` and admin endpoints reject any attempt to link a lesson to a parent course with a different locale.
- **Ordered Neighbors**: Neighbor resolution evaluates only published lessons in the exact same course and locale ordered by `position, id`.
- **WorkRef CourseSlug**: Only lesson WorkRefs populate `courseSlug`; other content families leave it null.
- **Sanitized Story Projection**: Lesson stories are sanitized via `public_story_document`, removing disabled sections and blocks.

---

## 6. Dirty Status and Remaining Risks

1. **Central Queue & Dispatch Status**:
   - In `Docs/05-delivery/concept-alignment-v2/execution-tasks.json`, status remains `NOT_STARTED`.
   - Written handoff report does **not** constitute acceptance; coordinator review, acceptance, and commit/merge remain required.
2. **Shared File Coupling**:
   - Working directory contains dirty uncommitted changes across predecessor packets (`PU-03-resolver`, `PU-03-settings`, `PU-04-catalog`, `PU-04-metadata`, `PU-04-publication`, `PU-04-course`, `PU-04-creative`).
3. **OpenAPI & Fixture Drift**:
   - Baseline hash tests in `test_openapi_hash_drift.py` and `test_contract_fixtures.py` reflect expected drifts pending coordinator acceptance and synchronization packets (`PU-SYNC-public`, `PU-SYNC-admin`).

---

PU-05-lessons_HANDOFF_READY
