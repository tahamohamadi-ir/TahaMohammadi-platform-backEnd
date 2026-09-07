# PU-03-resolver Delivery Handoff

Owner repository: `Back-End` (`d:\Project\tahamohammadi-platform\Back-End`)  
Packet: `PU-03-resolver`  
Specification: `Docs/05-delivery/concept-alignment-v2/product-packets/PU-03-resolver.md`  
Contract: `Docs/03-contracts/PRODUCT-INTERFACES-V2.md` §I04 & §I03  
Status: **HANDOFF_READY** (explicitly uncommitted)  
Base commit: `bd6682ea9dae7e5bf6957c36691dc3a94a00ea37`  
Result commit: uncommitted working directory  

---

## 1. Summary of Changes

Implemented canonical record resolver endpoint `GET /api/v1/records/{locale}/resolve` per §I04 and §I03:
- Supports resolving batch published record IDs across 13 content families: `landing`, `profile`, `article`, `series`, `researchtopic`, `researchstatement`, `project`, `publication`, `book`, `talk`, `download`, `course`, `creativework`.
- Maps models to canonical `routeFamily` values (`home`, `about`, `blog`, `blog/series`, `research`, `research/statements`, `projects`, `publications`, `books`, `talks`, `resources`, `education`, `gallery`).
- Computes canonical `summary` with fallback to `title` if excerpt/abstract/description/summary is blank.
- Preserves request order and deduplicates requested references.
- Enforces strict security & privacy boundaries: unpublished/draft/archived records and cross-locale records are placed in `unresolved` with opaque `{ family, id }` matching non-existent IDs, preventing private-record enumeration.
- Enforces input bounds: maximum 50 unique references; malformed references return HTTP 400 Bad Request; invalid locales return HTTP 404 Not Found.

---

## 2. Changed Paths (Within Exact Allowlist)

- `apps/api/record_resolver.py` (NEW): Implementation of schemas (`WorkRefOut`, `UnresolvedRefOut`, `RecordResolveOut`), router, validation, family queries, and order-preserving response assembly.
- `apps/api/api.py` (MODIFIED): Mounted `record_resolver_router` onto the public NinjaAPI.
- `tests/test_product_record_resolver.py` (NEW): 25 comprehensive unit and integration tests.
- `docs/contracts/openapi/current/public-openapi.json` (MODIFIED): Regenerated OpenAPI schema with `/api/v1/records/{locale}/resolve`.
- `docs/contracts/openapi/current/admin-openapi.json` (RE-EXPORTED): Re-exported; schema unchanged.
- `docs/contracts/openapi/current/PROVENANCE.json` (MODIFIED): Updated export provenance and artifact hashes.
- `docs/contracts/openapi/current/endpoint-inventory.md` (MODIFIED): Added operation row; total operations 106 -> 107.
- `docs/quality/product-v2/PU-03-resolver-HANDOFF.md` (NEW): This delivery report.

---

## 3. Verification and Test Results

### 3.1 Unit & Integration Tests
```bash
uv run pytest tests/test_product_record_resolver.py
```
**Result**: `25 passed in 1.73s`
Coverage includes:
- Endpoint registration and route resolution
- Request order preservation and deduplication
- All 13 supported families resolving to valid `WorkRefOut` descriptors with expected `routeFamily` and `summary`
- Summary fallback to `title` when content summary field is empty
- Exclusion of drafts, archived records, future-published records, and soft-deleted records from `items` (placed into `unresolved`)
- Cross-locale isolation (Persian vs English records isolated to requested locale)
- Input validation: malformed refs return HTTP 400 Bad Request (various malformed patterns tested)
- Batch limit enforcement: > 50 references returns HTTP 400 Bad Request
- Empty refs query handling (returns empty `items` and empty `unresolved` with HTTP 200)
- Locale validation: unsupported locales return HTTP 404 Not Found
- Public OpenAPI schema integration check

### 3.2 Linter & Formatter
```bash
uv run ruff check .
```
**Result**: `All checks passed!`

### 3.3 Contract Schema Export
```bash
uv run python scripts/export_openapi.py
```
**Result**: Schema exported cleanly.
- `public-openapi.json`: paths count 40 -> 41
- `public-openapi.json` SHA256: `19bf9d94bdf942e8aee3ec9fb9b734c535f4a969acf8e62212c02f3ce1c7c44c`
- `admin-openapi.json` SHA256: `c6c39f018e698871f76cb33b379eb46d03d3ceba395f87b8f9e612ebf0c37746`
- `endpoint-inventory.md` SHA256: `7f52e6f65b3b29b4c14ab2700656f9659bc39f1c21ff0bea0b58b7b2f3ad8379`

---

## 4. UI Changes
None (pure backend API packet).

---

## 5. Security & Privacy Confirmation
- **No private-record enumeration**: Non-existent, draft, scheduled, or archived records produce identical opaque `{ family, id }` in the `unresolved` array. No internal status, metadata, or presence leaks.
- **Strict locale partition**: Query filters strictly on `.public().filter(locale=locale)`.
- **DoS protection**: Capped at 50 unique refs per request.

---

## 6. Dirty Status and Remaining Risks
- Repository has uncommitted changes matching the exact write allowlist.
- Note: Historical hash check `tests/test_openapi_hash_drift.py` locks the BACKEND-140 historical snapshot (40 paths) and will be updated in the milestone acceptance packet per ADR-0010.

---

## 7. Coordinator Review R1 Revision (2026-09-05)

### 7.1 Defect and Scope Analysis
- **Defects addressed**:
  1. Direct calls to resolver with non-ASCII numeric characters (e.g. U+00B2 superscript two) or unbounded length numbers (e.g. 5000 ASCII digits) previously triggered unhandled `ValueError` / `500 Internal Server Error` due to permissive `str.isdigit()` and Python integer string conversion limits.
  2. Permissive parsing accepted leading zeros (e.g. `article:0001`), which were then mismatched against `obj.pk` string representation (`"1"`), incorrectly reporting existing records as unresolved.
  3. Error responses returned legacy Ninja `{"detail": ...}` instead of the normalized error envelope from PRODUCT-INTERFACES-V2 §I08 / ERROR-CONTRACT (`{code, message, field_errors, request_id}`).
  4. Error messages echoed full attacker-supplied raw tokens.
  5. Missing explicit 400 and 404 response schemas on the endpoint.

### 7.2 Implemented Fixes
- **Strict canonical ASCII decimal ID validation**:
  - Validates IDs against `_ID_RE = re.compile(r"^[1-9][0-9]{0,18}$", re.ASCII)`.
  - Rejects non-ASCII digits, U+00B2, negative signs, and leading zeros without `int()` conversion.
  - Rejects string IDs longer than 19 characters before conversion, completely avoiding Python's integer-string length limit exception.
  - Enforces `MAX_ID = 9223372036854775807` (signed 64-bit BigAutoField maximum), rejecting out-of-range integer values.
- **Normalized Error Envelope**:
  - Implemented `ErrorEnvelopeOut(code, message, field_errors, request_id)` conforming to PRODUCT-INTERFACES-V2 §I08 and ERROR-CONTRACT.
  - Uses `request.request_id` or generates a server-side UUID (`uuid.uuid4()`).
  - Error messages are safe and generic (e.g. `"Invalid reference ID..."`, `"Too many references requested..."`); attacker-supplied payloads are never echoed into messages.
  - Declared `response={200: RecordResolveOut, 400: ErrorEnvelopeOut, 404: ErrorEnvelopeOut}` on the resolver router, exporting explicit 400 and 404 OpenAPI schemas without globally altering legacy endpoints.
  - Used `ninja.responses.Status` for status code handling.

### 7.3 Failing-Before vs. Passing-After Evidence
- **Before Fix**:
  - `article:\u00b2`: `ValueError: invalid literal for int() with base 10: '²'` -> 500 Internal Server Error.
  - `article:` + 5000 nines: `ValueError: Exceeds the limit (4300 digits) for integer string conversion` -> 500 Internal Server Error.
  - `article:0001`: Mismatched pk, reported unresolved for existing record 1.
  - 400/404 responses: Returned `{"detail": "..."}` without `code`, `field_errors`, or `request_id`.
  - OpenAPI responses: Only documented 200, missing 400 and 404 schemas.
  - Pytest result: `23 failed, 10 passed`.
- **After Fix**:
  - All regression tests passing: 33 passed in 2.94s (`uv run pytest tests/test_product_record_resolver.py`).
  - Linter: `All checks passed!` (`uv run ruff check .`).
  - Formatter: Validated.

### 7.4 Updated Contract Artifacts and Schema Hashes
- Re-exported schemas via `scripts/export_openapi.py`.
- `public-openapi.json`:
  - Paths: 41
  - Documented schemas: `RecordResolveOut`, `WorkRefOut`, `UnresolvedRefOut`, `ErrorEnvelopeOut`
  - Responses for `/api/v1/records/{locale}/resolve`: `200`, `400`, `404`
  - SHA256: `151d736eaf8302dad852c56f6737244bae6e6684ca84aabc75242e8ee27ab37e`
- `admin-openapi.json`:
  - Paths: 66
  - SHA256: `c6c39f018e698871f76cb33b379eb46d03d3ceba395f87b8f9e612ebf0c37746`
- `endpoint-inventory.md`:
  - Operations: 107
  - SHA256: `7f52e6f65b3b29b4c14ab2700656f9659bc39f1c21ff0bea0b58b7b2f3ad8379`

PU-03-resolver_R1_HANDOFF_READY
