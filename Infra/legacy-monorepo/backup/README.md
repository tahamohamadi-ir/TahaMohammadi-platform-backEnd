# infra/backup — encrypted restic/rclone production backup (owner-executed)

## Role

Daily systemd job streams PostgreSQL dumps and copies media/config into the
owner's encrypted restic repository on Google Drive (via rclone). Credentials
never live in Git.

Canonical policy/runbook: `docs/governance/BACKUP_POLICY.md`,
`docs/governance/BACKUP_RUNBOOK.md`.

## What is backed up (after CMS runtime)

| Source | Tag(s) | Required? |
|---|---|---|
| `taha-cms-db-1` `pg_dumpall` → `cms-postgres-all.sql` | `production`, `cms`, `postgres` | **Yes** |
| `taha-prod-postgres-1` dump → `legacy-postgres-all.sql` | `production`, `legacy`, `postgres` | Only if container still running |
| `taha-cms_cms_media` volume | `production`, `media`, `config` | If path exists (warn-only if absent; empty CMS media is normal until uploads) |
| Legacy `taha_prod_media_data` volume | same | If path exists |
| `/etc/caddy/Caddyfile` + CMS compose (+ legacy compose if present) | same | Caddyfile required |

## Install / refresh on the VPS (owner only)

```bash
# Review diff, then install the script (mode 0750)
sudo install -m 0750 infra/backup/taha-platform-backup.sh /usr/local/sbin/taha-platform-backup
sudo install -m 0644 infra/backup/taha-platform-backup.service /etc/systemd/system/
sudo install -m 0644 infra/backup/taha-platform-backup.timer /etc/systemd/system/
# Ensure /etc/taha-backup.env exists (from taha-backup.env.example) with 0600
sudo systemctl daemon-reload
sudo systemctl enable --now taha-platform-backup.timer
```

Readiness without touching restic secrets in chat:

```bash
sudo /usr/local/sbin/taha-platform-backup --dry-run
```

Manual backup (when timer is idle):

```bash
sudo systemctl start taha-platform-backup.service
sudo journalctl -u taha-platform-backup.service -n 80 --no-pager
```

## Closing RISK-0003

Repo updates alone do **not** close the risk. Owner must:

1. Install the refreshed script and run a successful job (journal evidence).
2. Confirm a `cms`/`postgres` snapshot exists (`restic snapshots` — do not paste secrets).
3. Perform an **isolated** restore + import of `cms-postgres-all.sql` into a
   throwaway postgres (not production). Steps:
   `docs/plan/P3-cms-backup-restore-task-spec.md`.

## Explicit non-goals

- Never commit `/etc/taha-backup.env`, rclone.conf, or restic password.
- Never restore into the live `taha-cms` project as a “test”.
- Do not back up `infra/cms/.env` (contains runtime secrets).
