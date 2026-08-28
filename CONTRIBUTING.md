# Contributing

Start from an approved task. For behavior changes, write a failing test first or demonstrate the regression with an existing test. Keep migrations deterministic and review generated operations before commit.

Before review, run `uv run ruff check .` and `uv run pytest`. Also run targeted migration, API contract, security, and deployment checks when affected. Never use production data or credentials in tests.

Changes to public/admin responses, errors, authentication, locale behavior, media URLs, or publication state must update the canonical shared contract and name both frontend consumers.
