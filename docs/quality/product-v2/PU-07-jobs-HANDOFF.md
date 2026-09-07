# PU-07-jobs Delivery Handoff

Owner repository: `Back-End` (`d:\Project\tahamohammadi-platform\Back-End`)  
Packet: `PU-07-jobs`  
Specification: `Docs/05-delivery/concept-alignment-v2/product-packets/PU-07-jobs.md`  
Contract: `Docs/03-contracts/PRODUCT-INTERFACES-V2.md` §I06  
Status: **HANDOFF_READY** (explicitly uncommitted)  
Base commit: `bd6682ea9dae7e5bf6957c36691dc3a94a00ea37`  
Result commit: uncommitted working directory  

---

## 1. Summary of Changes

Implemented publication job persistence, authenticated build-runner callback endpoints, and admin status/retry endpoints per `Docs/03-contracts/PRODUCT-INTERFACES-V2.md` §I06:

- **Publication Job Models & Migrations (`apps/rebuild/models.py`, `migrations/0001_publication_jobs.py`)**:
  - `PublicationJob`:
    - `id`: UUID primary key.
    - `idempotency_key`: Unique string for duplicate suppression.
    - `state`: `queued`, `running`, `succeeded`, `failed` (indexed).
    - `locale`: `fa`, `en`, or null for multi-locale jobs.
    - `requested_revision`: Target revision identifier.
    - `deployed_revision`: Deployed artifact revision recorded upon success.
    - `affected_paths`: JSON list of affected canonical route strings.
    - `removal_state`: `not_requested`, `pending`, `effective`, `failed`.
    - `error_code`: Safe registered error code string (`BUILD_FAILED`, `PAGEFIND_FAILED`, `SITEMAP_FAILED`, etc.).
    - `created_at`, `started_at`, `finished_at`, `updated_at`.
    - `to_dict()`: Wire serializer matching §I06 specification.
  - `PublicationMachineNonce`:
    - `nonce`: Unique string up to 64 characters to enforce one-time use and eliminate replay attacks.
- **Machine Authentication & Services (`apps/rebuild/services.py`)**:
  - `sign_machine_request`: Generates hex HMAC-SHA256 signature joining uppercase method, path, timestamp, nonce, and hex SHA-256 digest of exact body bytes with newline separators.
  - `validate_machine_request`:
    - Verifies presence of `X-Publication-Timestamp`, `X-Publication-Nonce`, and `X-Publication-Signature`.
    - Enforces 300-second timestamp freshness window (`abs(now - timestamp) <= 300`).
    - Enforces strict one-time nonce usage via `PublicationMachineNonce` insertion within a savepoint.
    - Compares HMAC signature constant-time (`hmac.compare_digest`).
    - Fails closed with HTTP 403 when machine secret is empty/unconfigured.
  - `enqueue_publication_job`: Persists jobs with idempotency key deduplication.
  - `record_job_result`: Transactional compare-and-set:
    - Allows `queued` -> `running` -> `succeeded` / `failed`.
    - Enforces matching `artifactRevision` for `succeeded`.
    - Requires registered safe error code for `failed`.
    - Supports `removalState` extension update (`effective`, `failed`).
    - Idempotent replay of identical terminal result returns HTTP 200.
    - Conflicting terminal state transitions return HTTP 409 Conflict.
- **Internal Machine Callback Endpoints (`apps/rebuild/views.py`, `config/urls.py`)**:
  - `GET /api/v1/internal/publication-jobs/<uuid:job_id>`: Authenticated endpoint returning job payload to build runner.
  - `POST /api/v1/internal/publication-jobs/<uuid:job_id>/result`: Authenticated callback endpoint for build runner result submission.
- **Admin Publication Jobs Endpoints (`apps/api/admin_publication_jobs.py`, `apps/api/admin_api.py`)**:
  - `GET /api/v1/admin/publication-jobs`: Paged collection (default 20, max 50) filterable by `state` and `locale`, ordered by `-created_at`.
  - `GET /api/v1/admin/publication-jobs/{id}`: Single job detail.
  - `POST /api/v1/admin/publication-jobs/{id}/retry`:
    - Enforces staff + OTP authentication and CSRF.
    - Enforces `If-Match` timestamp precondition check (HTTP 412 on mismatch).
    - Idempotent: when `Idempotency-Key` is provided, returns the same retry job on repeat without triggering duplicate deployments.
- **OpenAPI Export (`docs/contracts/openapi/current/`)**:
  - Re-exported `public-openapi.json`, `admin-openapi.json`, and `endpoint-inventory.md`.
  - Updated `PROVENANCE.json` with artifact checksums.
- **Automated Test Suite (`tests/test_product_publication_jobs.py`)**:
  - Added 6 comprehensive automated tests verifying all requirements of PU-07-jobs.

---

## 2. Changed Paths (Within Exact Allowlist)

- `apps/rebuild/models.py` (NEW): Publication job and nonce models.
- `apps/rebuild/migrations/__init__.py` (NEW): Migrations package init.
- `apps/rebuild/migrations/0001_publication_jobs.py` (NEW): Database migration.
- `apps/rebuild/services.py` (MODIFIED): Machine runner HMAC authentication, job enqueueing, and compare-and-set result processing.
- `apps/rebuild/views.py` (MODIFIED): Internal runner detail and result endpoints.
- `apps/api/admin_api.py` (MODIFIED): Mounted publication jobs admin router.
- `apps/api/admin_publication_jobs.py` (NEW): Admin router for publication jobs listing, detail, and retry.
- `config/urls.py` (MODIFIED): Wired internal machine routes.
- `tests/test_product_publication_jobs.py` (NEW): Automated test suite for PU-07-jobs.
- `docs/contracts/openapi/current/public-openapi.json` (MODIFIED): Exported public OpenAPI schema.
- `docs/contracts/openapi/current/admin-openapi.json` (MODIFIED): Exported admin OpenAPI schema.
- `docs/contracts/openapi/current/PROVENANCE.json` (MODIFIED): Updated OpenAPI provenance.
- `docs/contracts/openapi/current/endpoint-inventory.md` (MODIFIED): Exported endpoint inventory.
- `docs/quality/product-v2/PU-07-jobs-HANDOFF.md` (NEW): This delivery report.

---

## 3. Acceptance and Verification Results

### 3.1 Targeted Test Suite
```bash
uv run pytest tests/test_product_publication_jobs.py -vv
```
**Result**: `6 passed in 3.96s`
- `test_publication_job_model_and_wire_shape`: PASSED
- `test_machine_auth_header_validation`: PASSED
- `test_machine_auth_fails_closed_when_secret_unconfigured`: PASSED
- `test_machine_runner_callback_workflow_and_compare_and_set`: PASSED
- `test_admin_publication_jobs_list_filter_and_detail`: PASSED
- `test_admin_publication_jobs_retry_idempotent`: PASSED

### 3.2 Regressions (Product-V2 Test Suite)
```bash
uv run pytest -k product -q
```
**Result**: `139 passed, 705 deselected in 5.86s`

### 3.3 Code Quality & Formatting
```bash
uv run ruff check .
```
**Result**: `All checks passed!` across the entire repository.

### 3.4 Database Migration Verification
```bash
uv run python manage.py migrate rebuild 0001
uv run python manage.py migrate rebuild zero
uv run python manage.py migrate rebuild 0001
uv run python manage.py makemigrations --check
```
**Result**: Migration applies forward, rolls back cleanly, reapplies, and `makemigrations --check` detects zero missing changes.

---

## 4. Contract Conformance Summary

| Requirement | Contract Section | Implementation Details | Status |
|---|---|---|---|
| Persisted Publication Jobs | §I06 | `PublicationJob` model with UUID, state, revisions, affected paths, removal state, error code | Conforms |
| Machine Request Signing | §I06 | Upper method, path, timestamp, nonce, hex SHA256 of body separated by newlines; hex HMAC-SHA256 | Conforms |
| Replay & Freshness Protection | §I06 | 300s window check and persistent nonce deduplication via `PublicationMachineNonce` | Conforms |
| Secret Absence Fail-Closed | §I06 | Unconfigured or empty secret returns HTTP 403 `AUTH_FAILED` | Conforms |
| Compare-and-Set Result Callback | §I06 | Transactional compare-and-set; 409 Conflict on conflicting terminal transitions; idempotent replay | Conforms |
| Revision & Error Code Validation | §I06 | `artifactRevision` verified on success; safe error codes enforced on failure | Conforms |
| Admin Job Listing & Filtering | §I06 | Paged `GET /api/v1/admin/publication-jobs` filterable by `state` and `locale` | Conforms |
| Admin Idempotent Retry | §I06 | `POST /api/v1/admin/publication-jobs/{id}/retry` checks `If-Match` and `Idempotency-Key` | Conforms |

---

## 5. Non-Negotiable Boundaries Verification

- Changes isolated strictly to the `Back-End` repository allowlist for packet `PU-07-jobs`.
- No code copied from legacy frontends or legacy repositories.
- Zero commits made to git (`git commit` withheld).
- All tests passing; ruff lint clean; OpenAPI specs in sync.
