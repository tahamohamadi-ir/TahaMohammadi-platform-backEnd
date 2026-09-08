# PU-03-settings Delivery Handoff

Owner repository: `Back-End` (`d:\Project\tahamohammadi-platform\Back-End`)  
Packet: `PU-03-settings`  
Specification: `Docs/05-delivery/concept-alignment-v2/product-packets/PU-03-settings.md`  
Contract: `Docs/03-contracts/PRODUCT-INTERFACES-V2.md` §I04  
Status: **HANDOFF_READY** (explicitly uncommitted)  
Base commit: `bd6682ea9dae7e5bf6957c36691dc3a94a00ea37`  
Result commit: uncommitted working directory  

---

## 1. Summary of Changes

Implemented localized draft/published site settings alongside legacy operational settings per §I04:
- **Model**: Added `LocalizedSiteSettings` to `apps/siteconfig/models.py` supporting `locale` (`fa`/`en` unique), `revision`, `brand_name`, `tagline`, `footer_text`, `seo_title`, `seo_description`, `nav_links`, `audience_links`, `graph_preset` (`atlas-v2`), `portal_preset` (`arch-v2`), `scene_motion` (`off`/`reduced`/`full`), `scene_density` (`low`/`standard`), lifecycle `status` (`draft`/`published`), `published_payload`, `published_at`, `created_at`, `updated_at`.
- **Migration**: Generated `apps/siteconfig/migrations/0005_localized_product_settings.py` and validated forward/backward rollback against disposable database state.
- **Public Endpoint**: Implemented `GET /api/v1/site/{locale}` on `api` in `apps/api/api.py`.
  - Serves published snapshot payload with strict draft isolation: returns HTTP 404 with normalized error envelope (`ErrorEnvelopeOut`) if settings are draft-only, absent, or for an unsupported locale.
  - No fallback to other locales.
  - Fully conforms to the public contract schema: `{locale, revision, brandName, tagline, footerText, seo: {title, description}, navLinks: [{label, href}], audienceLinks: [{kind, label, href}], scene: {graphPreset, portalPreset, motion, density}, updatedAt}`.
- **Admin Endpoints**: Implemented on `siteconfig_router` in `apps/api/admin_siteconfig.py` (mounted under `/api/v1/admin/`):
  - `GET /api/v1/admin/site/{locale}`: Returns draft state, metadata, and timestamps; requires staff authentication and verified TOTP session.
  - `PUT /api/v1/admin/site/{locale}`: Modifies draft fields under optimistic locking via `If-Match`. Returns HTTP 409 `CONFLICT` on timestamp mismatch. Validates `navLinks` (max 20, registered site-relative paths or valid `https://` URLs, rejects `javascript:`, `data:`, `http://`, and unknown local routes), `audienceLinks` (max 2, `kind` in `["research", "employment"]`), and `scene` presets (`graphPreset="atlas-v2"`, `portalPreset="arch-v2"`, `motion` in `["off", "reduced", "full"]`, `density` in `["low", "standard"]`).
  - `POST /api/v1/admin/site/{locale}/publish`: Issues a new revision ID, snapshots current draft fields to `published_payload`, sets `status="published"`, sets `published_at`, and makes the snapshot live on the public endpoint.
- **Legacy Compatibility**: Kept existing `/api/site` and `/api/v1/admin/site` untouched for backward operational compatibility.

---

## 2. Changed Paths (Within Exact Allowlist)

- `apps/siteconfig/models.py` (MODIFIED): Added `LocalizedSiteSettings` model.
- `apps/siteconfig/migrations/0005_localized_product_settings.py` (NEW): Database migration creating `LocalizedSiteSettings`.
- `apps/api/api.py` (MODIFIED): Added public `GET /v1/site/{locale}` endpoint and `LocalizedSiteSettingsPublicOut` schema.
- `apps/api/admin_api.py` (VERIFIED): Existing router registration automatically covers new `siteconfig_router` endpoints.
- `apps/api/admin_siteconfig.py` (MODIFIED): Added admin schemas (`LocalizedSiteSettingsAdminOut`, `LocalizedSiteSettingsUpdateIn`, `LocalizedSitePublishOut`, `LocalizedSceneOut`, `LocalizedSiteSeoOut`), validation helpers, and endpoints `GET|PUT /site/{locale}` and `POST /site/{locale}/publish`.
- `tests/test_product_localized_settings.py` (NEW): Test suite verifying initial failing gap, draft isolation, published schema matching, exact-locale isolation, unsupported locale handling, admin authentication, optimistic locking (`If-Match`), nav links validation, audience links validation, scene presets validation, and publish snapshot flow.
- `docs/contracts/openapi/current/public-openapi.json` (MODIFIED): Re-exported schema reflecting `GET /api/v1/site/{locale}` with 200 and 404 responses.
- `docs/contracts/openapi/current/admin-openapi.json` (MODIFIED): Re-exported schema reflecting `GET|PUT /api/v1/admin/site/{locale}` and `POST /api/v1/admin/site/{locale}/publish`.
- `docs/contracts/openapi/current/PROVENANCE.json` (MODIFIED): Updated OpenAPI provenance with regenerated hashes and path counts.
- `docs/contracts/openapi/current/endpoint-inventory.md` (MODIFIED): Added operations; updated inventory table.
- `docs/quality/product-v2/PU-03-settings-HANDOFF.md` (NEW): This delivery report.

---

## 3. Acceptance and Verification Results

### 3.1 Targeted Test Suite
```bash
uv run pytest tests/test_product_localized_settings.py -vv
```
**Result**: `12 passed in 1.79s`
- `test_public_endpoint_initial_gap_fails`: PASSED
- `test_public_returns_404_when_draft_only`: PASSED
- `test_public_returns_200_when_published`: PASSED
- `test_public_exact_locale_isolation`: PASSED
- `test_public_unsupported_locale_returns_404`: PASSED
- `test_admin_get_requires_auth`: PASSED
- `test_admin_get_returns_draft_state`: PASSED
- `test_admin_put_requires_if_match_and_checks_conflict`: PASSED
- `test_admin_put_validates_nav_links`: PASSED
- `test_admin_put_validates_audience_links`: PASSED
- `test_admin_put_validates_scene_presets`: PASSED
- `test_admin_publish_publishes_draft_snapshot_to_public`: PASSED

### 3.2 Code Quality & Formatting
```bash
uv run ruff check .
```
**Result**: `All checks passed!`

### 3.3 Database Migration Forward/Backward Rollback
```bash
uv run python manage.py makemigrations --check
uv run python manage.py migrate siteconfig 0004
uv run python manage.py migrate siteconfig 0005
```
**Result**:
- `makemigrations --check`: `No changes detected`
- Unapplying `0005_localized_product_settings`: `OK`
- Applying `0005_localized_product_settings`: `OK`

### 3.4 OpenAPI Export & Artifact Hashes
```bash
uv run python scripts/export_openapi.py
```
**Exported artifacts**:
- `public-openapi.json`:
  - Paths: 42
  - SHA256: `7715849ff1b29046063327df73cfc014d6587fd695529ef9941e9e36baab9f87`
- `admin-openapi.json`:
  - Paths: 51
  - SHA256: `9b69344db96dbaa8dd3e32cbe2805b50a1b62b64423c25a45a327637d6185acd`
- `endpoint-inventory.md`:
  - Operations: 111
  - SHA256: `05bf86513574c7fc397d6121a892422245e78b2b614da43e8a07547ea4a69e50`

---

## 4. UI Changes
None (backend API packet).

---

## 5. Security & Isolation Confirmation
- **Draft Isolation**: Public endpoint only serves the published snapshot (`published_payload`). Drafts and unapproved edits remain invisible to public consumers until explicitly published.
- **Fail-Closed & No Fallback**: Missing settings for a requested locale return HTTP 404 Not Found with `ErrorEnvelopeOut`. There is no cross-locale fallback.
- **Optimistic Locking**: Admin PUT enforces `If-Match` matching row `updated_at` millisecond precision; prevents concurrent overwrite hazards.
- **Input Sanitization**: Navigation links and audience links validate against registered site paths and enforce `https://` only for external links, rejecting `javascript:`, `data:`, `http:`, and unknown routes. Presets are restricted to renderer-supported enums (`atlas-v2`, `arch-v2`).

---

## 6. Dirty Status and Remaining Risks
- Repository status is explicitly uncommitted within the allowed scope.
- `test_openapi_hash_drift.py` expects milestone re-pinning in synchronization packets (PS-05 / ADR-0010); all 748 other existing backend tests pass.

---

## 7. 2026-09-08 — OpenAPI re-pin for featured/brand additive delta (PS-05)

- Backend CI on PR #3 failed only on the provenance gate: the branch adds
  `featuredRecords` (`LocalizedFeaturedRecordOut`) and `brandMedia` to the
  public `LocalizedSiteSettingsPublicOut` plus the admin featured/brand
  shapes. Fresh-export drift was confined to the two JSON snapshots
  (path counts 48/57, versions 0.4.0/0.1.0, inventory 124 ops — all
  unchanged); the schema diff is purely additive (zero removed lines).
- Re-exported via `scripts/export_openapi.py` (development settings) and
  re-pinned `ACCEPTANCE.json` + `test_openapi_hash_drift.py` (CRLF and LF
  hashes) to the new values. `PROVENANCE.json` is the regenerated
  generation evidence. `endpoint-inventory.md` has no content change and is
  left untouched.
- Local evidence: drift + openapi access tests **20/20**, branch suites
  (localized settings, managed-copy seed, seed safety, seed policy)
  **60/60**, `ruff check` clean, `verify_openapi_export.py` **3/3 MATCH**.
- Compatibility: additive and backward compatible — new public fields carry
  defaults (`[]`/`null`); the frontend reads `featuredRecords` through its
  own optional local type, so stale generated client types break nothing.
  Regenerated client-type adoption remains a separate PUBLIC step after this
  acceptance, per change control.
- The coordination-level `OPENAPI-ACCEPTANCE.md` addendum mirror is left for
  the coordinator (ROOT-owned doc, outside this packet's allowlist).

PU-03-settings_HANDOFF_READY
