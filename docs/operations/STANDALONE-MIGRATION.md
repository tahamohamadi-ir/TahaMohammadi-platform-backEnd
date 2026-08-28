# Standalone Backend Migration

The application code is now rooted at the repository root. Files under `Infra/legacy-monorepo/` were copied from the old `infra/cms`, `infra/backup`, and `infra/deploy` paths for evidence. They may reference old locations, service names, compose layouts, static builds, or frontend directories.

Before any script is used outside inspection:

1. inventory every absolute and monorepo-relative path;
2. define the new process/container and network topology;
3. parameterize secrets and environment;
4. replace frontend build coupling with explicit deployment contracts;
5. validate migrations, static/media, scheduled publishing, health, logs, backup, restore, deployment, smoke, rollback, and decommission steps in staging;
6. archive superseded scripts only after replacement evidence exists.
