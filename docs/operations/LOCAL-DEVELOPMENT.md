# Local Development

Install Python 3.12 and `uv`, then run `uv sync --frozen`. Use `config.settings.local` for local development and `config.settings.test` through pytest. The local profile must use disposable data.

Run migrations before the server. Confirm `/health/`, public OpenAPI/routes, staff login/MFA as applicable, and admin OpenAPI/routes. Email may intentionally report degraded health when not configured; record that as environment state rather than hiding it.

Do not copy `.venv`, SQLite files, media, caches, or secrets from the old monorepo.
