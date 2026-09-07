# PU-23-invalidation Delivery Handoff

Owner repository: `Back-End` (`d:\Project\tahamohammadi-platform\Back-End`)  
Packet: `PU-23-invalidation`  
Specification: `Docs/05-delivery/concept-alignment-v2/product-packets/PU-23-invalidation.md`  
Contract: `Docs/03-contracts/PRODUCT-INTERFACES-V2.md` §I06 / §I07  
Status: **HANDOFF_READY** (explicitly uncommitted)  
Base commit: `bd6682ea9dae7e5bf6957c36691dc3a94a00ea37`  
Result commit: uncommitted working directory  

---

## 1. Summary of Changes

Implemented invalidation enqueueing for all publish, archive, restore, schedule, graph, and settings transitions with canonical affected paths and removal status per `Docs/03-contracts/PRODUCT-INTERFACES-V2.md` §I06, while confirming zero backend search engine footprint per §I07:

- **Canonical Affected Paths Computation (`apps/rebuild/services.py`)**:
  - `compute_affected_paths(entity, item)`: Maps all 15 publishable content models to canonical frontend routes per §I02:
    - Root locale route: `/{locale}/`.
    - Landing: `/{locale}/` or `/{locale}/{slug}/`.
    - Profile: `/{locale}/about/` and `/{locale}/about/{slug}/`.
    - Article: `/{locale}/blog/`, `/{locale}/blog/{slug}/`, and related series routes `/{locale}/blog/series/{series_slug}/`.
    - Series: `/{locale}/blog/` and `/{locale}/blog/series/{slug}/`.
    - Research Topic: `/{locale}/research/` and `/{locale}/research/{slug}/`.
    - Research Statement: `/{locale}/research/` and `/{locale}/research/statements/{slug}/`.
    - Project: `/{locale}/projects/` and `/{locale}/projects/{slug}/`.
    - Publication: `/{locale}/publications/` and `/{locale}/publications/{slug}/`.
    - Book: `/{locale}/books/` and `/{locale}/books/{slug}/`.
    - Talk: `/{locale}/talks/` and `/{locale}/talks/{slug}/`.
    - Download: `/{locale}/resources/`, `/{locale}/resources/{slug}/`, and gated file route `/{locale}/resources/{slug}/file/`.
    - Course: `/{locale}/education/` and `/{locale}/education/{slug}/`.
    - Creative Work: `/{locale}/gallery/` and `/{locale}/gallery/{slug}/`.
    - Collection: `/{locale}/collections/` and `/{locale}/collections/{slug}/`.
    - Lesson: `/{locale}/education/`, parent course route `/{locale}/education/{course_slug}/`, and lesson route `/{locale}/education/{course_slug}/lessons/{slug}/`.
  - `enqueue_content_invalidation(entity, item, action, ...)`:
    - Helper wrapping `enqueue_publication_job`.
    - Computes affected paths for the item.
    - Sets `removal_state="pending"` for `"archived"`, `"unpublish"`, `"archive"`, and `"restore_draft"`.
    - Sets `removal_state="not_requested"` for `"published"`, `"publish"`, and `"schedule"`.
- **Lifecycle Transitions Integration (`apps/content/services/lifecycle.py`)**:
  - `transition_item`: Automatically enqueues publication job on transition to `"published"` or `"archived"`.
  - `bulk_archive_items`: Coalesces affected paths from all archived items across locales and enqueues coalesced publication jobs with `removal_state="pending"`.
- **Admin Content Actions (`apps/api/admin_content.py`)**:
  - `content_update`: Enqueues invalidation job when item is published or archived.
  - `content_revisions_restore`: When a previously published item is restored as a draft, automatically enqueues a publication job with `removal_state="pending"` and the item's canonical affected paths.
- **Scheduled Publishing Management Command (`apps/content/management/commands/publish_scheduled_content.py`)**:
  - Extended model map to encompass all 15 product entities.
  - Collects all items transitioned to `published` in the execution run.
  - Groups items by locale, computes affected paths, and enqueues publication jobs with `removal_state="not_requested"`.
- **Knowledge Graph Activation (`apps/api/admin_graph.py`)**:
  - `graph_version_activate`: Enqueues publication job for `/{version.locale}/` and `/{version.locale}/graph/` with `removal_state="not_requested"`.
- **Localized Site Settings (`apps/api/admin_siteconfig.py`)**:
  - `localized_site_settings_publish`: Enqueues publication job for `/{item.locale}/` and `/{item.locale}/site/` with `removal_state="not_requested"`.
- **Home Composition Modules (`apps/api/admin_home.py`)**:
  - `home_modules_put`: Enqueues publication job for `/{locale}/` with `removal_state="not_requested"`.
- **No Backend Search Engine Verification (§I07)**:
  - Confirmed no backend search engine endpoint or indexer is introduced; search indexing is executed downstream during static generation (e.g. Pagefind).

---

## 2. Verification Evidence

### Automated Tests
- `tests/test_product_publication_invalidation.py`: 9 passed.
  - `test_canonical_affected_paths_computation`: Verifies canonical frontend route generation across complex entity relationships (Article + Series, Lesson + Course, Gated Download + File route).
  - `test_publish_transition_enqueues_publication_job`: Verifies transition to published sets `removal_state="not_requested"` and correct affected paths.
  - `test_archive_transition_enqueues_removal_job`: Verifies transition to archived sets `removal_state="pending"` and correct affected paths.
  - `test_bulk_archive_enqueues_coalesced_removal_job`: Verifies bulk archive coalesces affected paths into a single publication job per locale.
  - `test_scheduled_content_publishing_enqueues_publication_job`: Verifies `publish_scheduled_content` command enqueues jobs with affected paths.
  - `test_graph_activation_enqueues_publication_job`: Verifies graph version activation enqueues jobs with graph paths.
  - `test_localized_site_settings_publish_enqueues_publication_job`: Verifies site settings publish enqueues jobs with site paths.
  - `test_home_modules_update_enqueues_publication_job`: Verifies home module update enqueues home page invalidation job.
  - `test_content_revisions_restore_previously_published_enqueues_removal`: Verifies revision restore of published item demotes to draft and enqueues removal job.
- `tests/test_product_publication_jobs.py`: 6 passed.
- Full product suite: 148 passed (`uv run pytest -k product -q`).
- Code quality: `uv run ruff check .` passed with 0 errors.
- Database: `uv run python manage.py makemigrations --check` passed with 0 unapplied changes.

---

## 3. OpenAPI and Contracts

- OpenAPI exported via `scripts/export_openapi.py`:
  - `docs/contracts/openapi/current/public-openapi.json`
  - `docs/contracts/openapi/current/admin-openapi.json`
  - `docs/contracts/openapi/current/endpoint-inventory.md`
- Status in `docs/contracts/openapi/current/PROVENANCE.json` remains `source-generated-unaccepted`.

---

## 4. Work Left Uncommitted

Per workspace instructions, changes are left unstaged and uncommitted in git. No commits have been made.
Handoff marker: `PU-23-invalidation_HANDOFF_READY`.
