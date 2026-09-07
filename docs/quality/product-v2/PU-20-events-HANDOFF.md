# PU-20-events Delivery Handoff

Owner repository: `Back-End` (`d:\Project\tahamohammadi-platform\Back-End`)  
Packet: `PU-20-events`  
Specification: `Docs/05-delivery/concept-alignment-v2/product-packets/PU-20-events.md`  
Contract: `Docs/03-contracts/PRODUCT-INTERFACES-V2.md` §I07  
Status: **HANDOFF_READY** (explicitly uncommitted)  
Base commit: `bd6682ea9dae7e5bf6957c36691dc3a94a00ea37`  
Result commit: uncommitted working directory  

---

## 1. Summary of Changes

Implemented first-party aggregate analytics ingest and reporting with 13-month rolling retention and strict visitor-privacy boundaries per `Docs/03-contracts/PRODUCT-INTERFACES-V2.md` §I07:

- **First-Party Aggregate Models (`apps/analytics/models.py`, `apps/analytics/migrations/0001_aggregate_events.py`)**:
  - `AggregateEvent`:
    - `date`: Calendar date in UTC (DateField, indexed).
    - `locale`: Exact canonical locale `fa`/`en` (CharField, indexed).
    - `page_path`: Canonical path without query/fragment (CharField max 512, indexed).
    - `event`: Registered action type (CharField max 64, indexed).
    - `target`: Registered action identifier (CharField max 64, blank, indexed).
    - `count`: Aggregated event count (PositiveIntegerField, default 1).
    - Unique constraint: `(date, locale, page_path, event, target)`.
    - `record_event`: Atomic counter incrementation via `select_for_update` and `F("count") + 1`.
  - Privacy boundary: Stores daily aggregates only; zero visitor identifiers, cookies, IP addresses, full referrers, or emails are persisted.
- **Public Ingest Endpoint (`apps/analytics/api.py`, mounted in `apps/api/api.py`)**:
  - `POST /api/v1/analytics/events`:
    - Accepts `{event, pagePath, locale, target?}`.
    - Supported events: `page_view`, `cv_download`, `research_profile_download`, `demo_click`, `contact_click`, `contact_submit_success`.
    - Strict path validation: requires leading slash, rejects query parameters (`?`), hash fragments (`#`), whitespace, and length > 512.
    - Target validation: validates registered action ID pattern (`^[a-zA-Z0-9_\-]{1,64}$`), rejects arbitrary text or URLs.
    - Privacy enforcement: `model_config = {"extra": "forbid"}` rejects any payload containing unknown or visitor-tracking fields (`email`, `visitor_id`, `cookie`, etc.) with HTTP 422.
    - Security: Enforces same-origin verification and per-client IP cache sliding rate limiting (300 requests/minute).
    - Response: HTTP 200/202 with `{"status": "accepted"}` without blocking navigation.
- **Admin Reporting Endpoint (`apps/analytics/api.py`, mounted in `apps/api/admin_api.py`)**:
  - `GET /api/v1/admin/analytics`:
    - Parameters: `from` (ISO YYYY-MM-DD), `to` (ISO YYYY-MM-DD), optional `locale` (`fa`/`en`).
    - Enforces maximum date window of 366 days and rejects inverted dates (`from > to`).
    - Authentication: Enforces staff session and verified OTP device (`_require_admin_otp`).
    - Output wire shape:
      ```json
      {
        "from": "YYYY-MM-DD",
        "to": "YYYY-MM-DD",
        "timezone": "UTC",
        "updatedAt": "...",
        "metric": "received_events",
        "rows": [
          {
            "date": "YYYY-MM-DD",
            "pagePath": "/en/blog/",
            "locale": "en",
            "event": "page_view",
            "target": "",
            "count": 42
          }
        ]
      }
      ```
    - Explicitly labels metrics as **`received_events`** per contract §I07, avoiding claims of unique humans or verified visitors.
- **Pruning & Rolling Retention Command (`apps/analytics/management/commands/prune_analytics.py`)**:
  - `python manage.py prune_analytics`:
    - Enforces 13-month rolling retention (default 396 days).
    - Supports `--dry-run` to preview deletions and `--days <N>` to configure retention threshold.
    - Atomically deletes stale aggregate records while preserving recent records.
- **OpenAPI Export (`docs/contracts/openapi/current/`)**:
  - Re-exported `public-openapi.json` (exposing `POST /api/v1/analytics/events`) and `admin-openapi.json` (exposing `GET /api/v1/admin/analytics`).
  - Updated `endpoint-inventory.md` and `PROVENANCE.json`.

---

## 2. Verification Evidence

### Automated Tests
- `tests/test_product_analytics.py`: 9 passed in 4.22s:
  - `test_analytics_app_installed_and_routes_exist`: Verifies model and router registration.
  - `test_public_event_ingest_success_and_daily_aggregation`: Verifies atomic counter increments for matching calendar days and separate rows for target IDs.
  - `test_public_event_ingest_validation_rules`: Validates rejection of unknown events, unsupported locales, paths with query strings/fragments, non-slash paths, and URL/space targets.
  - `test_public_event_ingest_privacy_extra_fields_rejected`: Verifies strict rejection (422) of visitor identifiers, emails, and cookies.
  - `test_public_event_ingest_cross_origin_rejected`: Verifies foreign cross-origin rejections (403).
  - `test_admin_analytics_requires_staff_and_otp`: Verifies unauthenticated and non-OTP staff access are rejected (401/403/404).
  - `test_admin_analytics_report_success_and_wire_shape`: Verifies report response wire format, `received_events` metric, UTC timezone, and locale filtering.
  - `test_admin_analytics_validation_limits`: Verifies 366-day limit, inverted date validation, and invalid format rejections.
  - `test_prune_analytics_command`: Verifies `--dry-run` and actual deletion of records older than 13 months (~396 days).
- Full product suite: 157 passed (`uv run pytest -k product -q`).
- Code quality: `uv run ruff check .` passed with 0 errors.
- Database & Migrations:
  - `uv run python manage.py makemigrations --check` passed clean (no uncommitted migrations).
  - Disposable migration test: `migrate analytics zero` followed by `migrate analytics` tested and validated forward and backward repeatability.

---

## 3. Work Left Uncommitted

Per workspace instructions, changes are left unstaged and uncommitted in git.
Handoff marker: `PU-20-events_HANDOFF_READY`.
