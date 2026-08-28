# Whitespace Baseline

`git diff --cached --check` reports eight `new blank line at EOF` findings in files copied byte-for-byte from the source repository:

- `Infra/legacy-monorepo/deploy/update-cms.sh`
- `apps/api/admin_timeline.py`
- `apps/api/api.py`
- `tests/test_admin_content_api.py`
- `tests/test_admin_content_write.py`
- `tests/test_admin_media_api.py`
- `tests/test_admin_siteconfig_api.py`
- `tests/test_admin_workflow_api.py`

They are preserved in the migration commit so the 198-file backend transfer remains hash-identical. New documentation and repository-control files have no whitespace findings. Normalize these source files only in a separate mechanical commit after the migration baseline is established.
