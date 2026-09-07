# PU-04-project-evidence Delivery Handoff

Owner repository: `Back-End` (`d:\Project\tahamohammadi-platform\Back-End`)  
Packet: `PU-04-project-evidence`  
Specification: `Docs/05-delivery/concept-alignment-v2/product-packets/PU-04-project-evidence.md`  
Contract: `Docs/03-contracts/PRODUCT-INTERFACES-V2.md` §I03 / §I01  
Status: **HANDOFF_READY** (explicitly uncommitted)  
Base commit: `bd6682ea9dae7e5bf6957c36691dc3a94a00ea37`  
Result commit: uncommitted working directory  

---

## 1. Summary of Changes

Implemented atomic admin editing and public projection integrity for project case-study details, evidence, collaborators, and funding per `Docs/03-contracts/PRODUCT-INTERFACES-V2.md` §I03:

- **Admin Project Evidence Endpoints (`apps/api/admin_project_evidence.py`)**:
  - Implemented `GET /api/v1/admin/content/project/{id}/case-study`:
    - Returns full aggregate for project:
      - `details`: `summary`, `problem`, `solution`, `impact`, `architectureNotes`, `depth`.
      - `evidence`: list of evidence items (`id`, `kind`, `title`, `url`, `doi`, `evidenceType`, `visibility`, `weight`, `sourceReference`).
      - `collaborators`: list of collaborators (`id`, `name`, `role`, `affiliation`, `url`, `isApproved`, `position`).
      - `funding`: list of funding entries (`id`, `grantName`, `funderName`, `awardNumber`, `url`, `isApproved`, `position`).
    - Handled missing case study cleanly with sensible defaults (`depth="standard"`).
    - Returns 404 with error code `NOT_FOUND` if project does not exist.
  - Implemented `PUT /api/v1/admin/content/project/{id}/case-study`:
    - Requires and validates parent `If-Match` HTTP header matching project's updated_at timestamp; returns 428 `PRECONDITION_REQUIRED` if header is missing, or 409 `STALE_REVISION` if mismatched.
    - Locks the parent `Project` row via `select_for_update()` under `transaction.atomic()`.
    - Validates that every provided evidence, collaborator, or funding ID belongs to this specific project (`parent.evidence_items`, `parent.collaborators`, `parent.funding_entries`); rejects foreign IDs with 400 `VALIDATION`.
    - Validates `visibility` against `EvidenceVisibility.values` (`public`, `restricted`, `internal`) and `depth` against `CaseStudyDepth.values` (`summary`, `standard`, `deep_dive`).
    - Supports both camelCase and snake_case payload field naming via Pydantic model validators.
    - Full-aggregate atomic mutation:
      - Updates or creates `CaseStudyDetail`.
      - Updates existing or creates new `ProjectEvidence`, `ProjectCollaborator`, and `ProjectFunding` records.
      - Deletes any existing child record omitted from the request payload (full replacement semantics).
      - Updates parent `Project.updated_at`.
      - Creates a `ContentRevision` snapshot capturing `{details, evidence, collaborators, funding}`.
      - Records an `AuditLog` entry tracking the mutation.
- **Router Registration (`apps/api/admin_content.py`)**:
  - Registered project case study endpoints with `content_router` via `register_project_case_study_endpoints(content_router)`.
- **Public Projection Preservation (`apps/api/api.py` & `apps/content/models.py`)**:
  - Verified that public project detail projections preserve strict visibility and privacy boundaries:
    - Only `visibility == "public"` evidence is exposed with source links.
    - `restricted` and `internal` evidence is omitted from public responses.
    - Collaborators and funding entries with `is_approved=False` are omitted from public responses.
- **OpenAPI Schema Export (`docs/contracts/openapi/current/`)**:
  - Exported updated schemas including `GET` and `PUT` `/api/v1/admin/content/project/{id}/case-study`.
  - Regenerated `public-openapi.json`, `admin-openapi.json`, `endpoint-inventory.md`, and updated `PROVENANCE.json`.
- **Automated Test Suite (`tests/test_product_project_evidence.py`)**:
  - Added 4 comprehensive tests verifying admin GET defaults and populated data, admin PUT concurrency and atomic replacement, foreign ID rejection and enum validation, and public visibility/privacy preservation.

---

## 2. Changed Paths (Within Exact Allowlist)

- `apps/api/admin_project_evidence.py` (NEW): Schemas, validators, and endpoints for project case study, evidence, collaborators, and funding.
- `apps/api/admin_content.py` (MODIFIED): Registered project case-study endpoints.
- `tests/test_product_project_evidence.py` (NEW): Automated test suite for PU-04-project-evidence.
- `docs/contracts/openapi/current/public-openapi.json` (MODIFIED): Exported public OpenAPI schema.
- `docs/contracts/openapi/current/admin-openapi.json` (MODIFIED): Exported admin OpenAPI schema.
- `docs/contracts/openapi/current/PROVENANCE.json` (MODIFIED): Updated OpenAPI provenance with regenerated artifact hashes.
- `docs/contracts/openapi/current/endpoint-inventory.md` (MODIFIED): Exported endpoint inventory.
- `docs/quality/product-v2/PU-04-project-evidence-HANDOFF.md` (NEW): This delivery report.

---

## 3. Acceptance and Verification Results

### 3.1 Initial Failing Gap Evidence
Before endpoint implementation:
```bash
uv run pytest tests/test_product_project_evidence.py
```
**Output**: `4 failed in 3.65s`
- `AssertionError: assert 404 == 200` on `/api/v1/admin/content/project/{id}/case-study`

### 3.2 Targeted Test Suite
```bash
uv run pytest tests/test_product_project_evidence.py -vv
```
**Result**: `4 passed in 3.53s`
- `test_admin_get_project_case_study`: PASSED
- `test_admin_put_project_case_study_if_match_and_atomic_update`: PASSED
- `test_admin_put_project_case_study_rejects_foreign_ids_and_invalid_enums`: PASSED
- `test_public_project_visibility_preserves_privacy`: PASSED

### 3.3 Regressions (Product-V2 Test Suite)
```bash
uv run pytest -k product -q
```
**Result**: `123 passed, 705 deselected in 5.16s`

### 3.4 Code Quality & Formatting
```bash
uv run ruff check .
```
**Result**: `All checks passed!` across the entire repository.

### 3.5 Database Migration Check
```bash
uv run python manage.py makemigrations --check
```
**Result**: `No changes detected`

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
  - Paths: 52
  - SHA256: `fabebaa28c39aa1b4f784a6c7e3bd95c512f7639c2e79ab24736a5274ceefedc`
  - Version: `0.1.0`
- `endpoint-inventory.md`:
  - Operations: 118
  - SHA256: `bdbc59655fa967fa8d0ee4cc8c878cedc23409bd42294b6c2a65508bdb67760a`

---

## 4. UI Changes
None (backend data models, admin API endpoints, and OpenAPI contract packet).

---

## 5. Security & Isolation Confirmation
- **Parent Concurrency Guard (`If-Match`)**:
  - Missing `If-Match` returns 428 `PRECONDITION_REQUIRED`.
  - Mismatched `If-Match` returns 409 `STALE_REVISION`.
  - Prevents lost updates when multiple staff members edit project evidence.
- **Foreign Key & Ownership Isolation**:
  - Rejects any sub-entity ID belonging to a different project or nonexistent record with 400 `VALIDATION`.
- **Public Visibility & Privacy Preservation**:
  - Restricted and internal evidence entries are strictly excluded from public project projections.
  - Unapproved collaborators and funding entries (`is_approved=False`) are excluded from public project projections.
- **Audit & History Tracking**:
  - Successful mutations produce both a `ContentRevision` snapshot and an `AuditLog` entry with the acting admin user.

---

## 6. Dirty Status and Remaining Risks

1. **Central Queue & Dispatch Status**:
   - In `Docs/05-delivery/concept-alignment-v2/execution-tasks.json`, status remains `NOT_STARTED`.
   - Written handoff report does **not** constitute acceptance; coordinator review, acceptance, and commit/merge remain required.
2. **Shared File Coupling**:
   - Working directory contains dirty uncommitted changes across predecessor packets (`PU-03-resolver`, `PU-03-settings`, `PU-04-catalog`, `PU-04-metadata`, `PU-04-publication`, `PU-04-course`, `PU-04-creative`, `PU-05-lessons`, `PU-06-book`, `PU-06-talk`, `PU-06-resource`, `PU-06-collection`, `PU-06-series`).
3. **OpenAPI & Fixture Drift**:
   - Baseline hash tests in `test_openapi_hash_drift.py` and `test_contract_fixtures.py` reflect expected drifts pending coordinator acceptance and synchronization packets (`PU-SYNC-public`, `PU-SYNC-admin`).

---

PU-04-project-evidence_HANDOFF_READY
