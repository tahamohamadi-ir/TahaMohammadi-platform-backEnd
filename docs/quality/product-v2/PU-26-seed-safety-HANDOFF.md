# PU-26-seed-safety Handoff

- Packet: `PU-26-seed-safety`
- Repository: `Back-End`
- Status: `PU-26-seed-safety_HANDOFF_READY`
- Implementation: Safe seed import preventing overwrite of owner edits.

## Changed Paths

- `apps/content/management/commands/import_content_seed.py`
- `apps/content/management/commands/import_profile_seed.py`
- `apps/content/services/content_seed_import.py`
- `tests/test_seed_safety.py`

## Test Evidence

```powershell
uv run pytest tests/test_seed_safety.py
```
Output: 5 passed in tests/test_seed_safety.py (seed initial -> edit -> seed re-run -> owner edits preserved).

## 2026-09-07 — Re-verification (CM-06)

```powershell
uv run pytest tests/test_seed_safety.py tests/test_managed_copy_seed.py tests/test_admin_seed_policy.py tests/test_seed_admin_only_records.py tests/test_product_localized_settings.py -q
```
Output: **60 passed**. `import_content_seed` / `seed_managed_copy` default to
`create-missing-only` with explicit `--overwrite-id` for selective refresh;
`_map_availability` and profile mappers guard on
`context.overwrite or pk not in existing_ids`. Managed-copy seed only fills
absent keys and validates the whole source before writing (dry-run report,
no persisted changes on failure).

## Summary

`import_content_seed` now defaults to preserving existing records and relations, supporting selective `--overwrite-id` only when explicitly specified, with itemized action logging.
