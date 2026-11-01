# Production deploy (`deploy-production.yml`)

Rebuilds and redeploys **only** the `cms` container (the Django/Ninja API)
in the shared `taha-cms-prod` Docker Compose stack on the production host.
`web`, `admin`, `db`, `mailpit` and the shared `edge-caddy` are never
restarted — the existing `/home/deploy/prod/docker-compose.prod.yml` and
Caddy routing are reused as-is. It is modelled on the public site's
workflow of the same name and uses the same secrets.

- Every push to `main` runs the `verify` job (uv sync, Ruff,
  `manage.py check`, pytest, OpenAPI provenance) — nothing is deployed.
- Deploying requires a manual trigger: **Actions → Deploy production →
  Run workflow**.

## One-time setup (done by the repository owner, not by an agent)

Use the **same values** you already added to the public-site repo. Add
them here under **Settings → Secrets and variables → Actions → New
repository secret** — paste values directly into the GitHub UI, never into
a chat or a file in this repo:

| Secret                 | Value                                                        |
| ---------------------- | ------------------------------------------------------------ |
| `PROD_SSH_PRIVATE_KEY` | Same as the public site (contents of the deploy private key) |
| `PROD_SSH_KNOWN_HOSTS` | Same as the public site (`ssh-keyscan` output)                |
| `PROD_SSH_HOST`        | Same as the public site                                      |
| `PROD_SSH_PORT`        | Same as the public site                                      |
| `PROD_SSH_USER`        | Same as the public site (`deploy`)                           |
| `PROD_APP_DIR`         | Same as the public site is fine — this workflow works in its own `backend/` subfolder of it |

Optional but recommended: **Settings → Environments → production →
Required reviewers**, so the deploy pauses for your approval.

## What the workflow does

See `.github/workflows/deploy-production.yml` for the exact steps. In
order, on the server:

1. Checks out the deployed commit into `$PROD_APP_DIR/backend/src`.
2. Builds `taha-cms-prod:prod-<short-sha>` from this repo's `Dockerfile`.
3. Confirms the compose file still resolves `cms` to
   `taha-cms-prod:${RELEASE_ID}`, and stops before changing anything if not.
4. Prints the current `RELEASE_ID` (the rollback target) and tags the
   images `cms`, `web` and `admin` are running right now with it, plus
   `web`/`admin`'s with the new release id. `RELEASE_ID` is shared by all
   three app images, so this keeps every service resolvable both after the
   bump and after a rollback (tags only, no content changes).
5. Dumps the database to
   `$PROD_APP_DIR/backend/backups/pre-<release>-<timestamp>.sql.gz`, then
   runs `manage.py migrate --plan` and `manage.py migrate` in a one-off
   container from the **new** image while the old `cms` keeps serving.
   `.env.prod` is not touched until the migration has succeeded.
6. Sets the single `RELEASE_ID=` line in `/home/deploy/.env.prod` (file
   permissions kept at 0600).
7. `docker compose -p taha-cms-prod ... up -d --no-deps --force-recreate cms`.
8. Waits for `/health/` inside the new container to report `status: ok`.

The admin-panel deploy uses the same lock file
(`/home/deploy/.deploy-prod.lock`), so the two never edit `.env.prod` at
the same time.

## Rollback

The previous image stays on the host. Set `RELEASE_ID=` in
`/home/deploy/.env.prod` back to the "rollback target" value printed in
the run's log and run
`docker compose -p taha-cms-prod --env-file /home/deploy/.env.prod -f /home/deploy/prod/docker-compose.prod.yml up -d --no-deps --force-recreate cms`.
Migrations are not reversed by this; the pre-migration dump from step 5 is
the data rollback.
