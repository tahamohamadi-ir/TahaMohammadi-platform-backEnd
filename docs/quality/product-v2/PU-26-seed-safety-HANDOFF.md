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

## Summary

`import_content_seed` now defaults to preserving existing records and relations, supporting selective `--overwrite-id` only when explicitly specified, with itemized action logging.
