# Backend Testing

The copied suite contains API, admin workflow, authentication/MFA, security, content lifecycle, composition, graph, timeline, media, preview, rebuild, research, contact, production proxy/logging, and seed coverage.

Baseline commands are `uv run ruff check .` and `uv run pytest`. Add targeted tests for every fix and run the full suite before release. Schema migrations require migration-plan checks and production-like rehearsal; operational scripts require isolated smoke tests.
