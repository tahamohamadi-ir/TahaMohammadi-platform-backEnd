#!/usr/bin/env bash
# =============================================================================
# tahamohamadi.ir — Owner-attended VPS maintenance (2026-08-22 revised)
#
# Scope:
#   Phase 1 — apt upgrade (interactive sudo)
#   Phase 2 — Caddy cutover ONLY (host systemd → Compose profile edge)
#
# Explicitly OUT OF SCOPE:
#   - SSH port closure (port 22 retained for VPN; 2222 canonical deploy)
#   - CMS migrate, rebuild-web, PREVIEW_SHARE_SECRET, CMS_CD_AUTO_MIGRATE
#
# Repo / host:
#   /home/deploy/cms-repo
#   deploy@85.192.29.196:2222
#
# Refs: infra/caddy/HOST-CADDY-DISABLE.md
#       docs/governance/DEPLOY_RUNBOOK.md § Caddy-in-Compose cutover
#       infra/cms/docker-compose.cms.yml (service caddy, profile edge)
#
# KEEP TWO SSH SESSIONS ON PORT 2222 OPEN BEFORE STARTING PHASE 2.
# =============================================================================
set -euo pipefail

REPO="/home/deploy/cms-repo"
COMPOSE_FILE="${REPO}/infra/cms/docker-compose.cms.yml"
SITE="https://tahamohamadi.ir"
CADDY_BAK=""
ROLLBACK_HINT=""

info()  { printf '\n== %s ==\n' "$*"; }
warn()  { printf 'WARN: %s\n' "$*" >&2; }
die()   { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
pause() { read -r -p "$* [Enter to continue, Ctrl+C to abort] " _; }

# ---------------------------------------------------------------------------
# Pre-flight (matches owner audit checks 2026-08-22)
# ---------------------------------------------------------------------------
preflight() {
  info "Pre-flight verification"
  echo "hostname: $(hostname -f 2>/dev/null || hostname)"
  echo "repo:     ${REPO}"
  [ -d "$REPO" ] || die "repo missing: $REPO"
  [ -f "$COMPOSE_FILE" ] || die "compose file missing: $COMPOSE_FILE"
  [ -f "${REPO}/infra/caddy/Caddyfile.compose" ] || die "Caddyfile.compose missing"

  echo "--- host Caddy (expect: active before cutover) ---"
  systemctl is-active caddy 2>/dev/null || true

  echo "--- Compose caddy (expect: empty before cutover) ---"
  docker ps --filter name=caddy --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' || true

  echo "--- loopback web/cms (expect: healthy) ---"
  curl -sf "http://127.0.0.1:13080/health.json" | head -c 120 || warn "web loopback not OK"
  echo
  curl -sf "http://127.0.0.1:18000/health/" | head -c 120 || warn "cms loopback not OK"
  echo

  echo "--- apt upgradable count (expect: ~14 before Phase 1) ---"
  apt list --upgradable 2>/dev/null | grep -vc '^Listing' || true

  echo "--- SSH listeners (expect: BOTH 22 and 2222 — intentional dual-port) ---"
  sudo ss -lntp | grep -E ':22\b|:2222\b' || warn "expected sshd on 22 and 2222"

  echo "--- gh CLI ---"
  if command -v gh >/dev/null 2>&1; then
    gh --version
  else
    echo "gh: NOT INSTALLED (use GitHub web UI for CADDY_EDGE, or install in post-cutover step)"
  fi

  echo "--- sudo ---"
  if sudo -n true 2>/dev/null; then
    echo "sudo -n: passwordless OK"
  else
    echo "sudo -n: password required (expected — interactive sudo needed for Phase 1/2)"
  fi
}

# ---------------------------------------------------------------------------
# Phase 1 — apt upgrade
# ---------------------------------------------------------------------------
phase1_apt_upgrade() {
  info "Phase 1 — apt upgrade (interactive sudo)"
  warn "May restart services; keep SSH session stable."
  pause "Apply apt upgrade now?"

  sudo apt update
  sudo apt list --upgradable || true
  sudo apt upgrade
  # Optional hardening (uncomment if desired):
  # sudo apt autoremove -y

  info "Phase 1 post-check"
  UPGRADABLE="$(apt list --upgradable 2>/dev/null | grep -vc '^Listing' || true)"
  echo "apt upgradable remaining: ${UPGRADABLE}"
  curl -sf "${SITE}/health.json" >/dev/null && echo "public /health.json: OK" || warn "public health check failed (may be transient)"
}

# ---------------------------------------------------------------------------
# Phase 2 — Caddy cutover (host → Compose edge)
# ---------------------------------------------------------------------------
phase2_caddy_cutover() {
  info "Phase 2 — Caddy cutover (host systemd → Compose edge)"
  warn "Brief TLS gap possible while ACME re-issues on Compose caddy."
  pause "Proceed with Caddy cutover? (two SSH sessions on 2222 recommended)"

  cd "$REPO"
  git pull --ff-only origin main

  info "2.1 — Timestamped backup of live host Caddyfile"
  CADDY_BAK="/etc/caddy/Caddyfile.bak-$(date +%Y%m%d%H%M%S)"
  sudo cp -a /etc/caddy/Caddyfile "$CADDY_BAK"
  echo "Backup: $CADDY_BAK"
  ROLLBACK_HINT="$CADDY_BAK"

  info "2.2 — Stop and disable host Caddy (free 80/443)"
  sudo systemctl disable --now caddy
  systemctl is-active caddy 2>/dev/null && die "host caddy still active" || echo "host caddy: stopped"

  info "2.3 — Seed Compose ACME volume from host /var/lib/caddy (avoids Cloudflare 525 on first cutover)"
  # Host systemd Caddy stores issued certificates under /var/lib/caddy (default).
  # Compose `caddy` mounts Docker volume `taha-cms_caddy_data` at /data. Starting
  # edge caddy with an empty volume leaves origin TLS broken (525) until ACME
  # re-issues. Copy host data after host caddy stops and before Compose binds 443.
  CADDY_VOL="${COMPOSE_PROJECT_NAME:-taha-cms}_caddy_data"
  if [ -d /var/lib/caddy ] && sudo find /var/lib/caddy -mindepth 1 -print -quit 2>/dev/null | grep -q .; then
    docker volume inspect "$CADDY_VOL" >/dev/null 2>&1 || docker volume create "$CADDY_VOL"
    docker run --rm \
      -v /var/lib/caddy:/host:ro \
      -v "${CADDY_VOL}:/data" \
      alpine sh -c 'rm -rf /data/* /data/.[!.]* 2>/dev/null; cp -a /host/. /data/; find /data -name "*.crt" | head -3'
    echo "Seeded ${CADDY_VOL} from /var/lib/caddy"
  else
    warn "/var/lib/caddy empty or missing — Compose will obtain fresh certs (525/TLS gap possible)"
  fi

  info "2.4 — Confirm 80/443 free (or bound only by upcoming compose caddy)"
  sudo ss -lntp | grep -E ':80\b|:443\b' || echo "80/443: no listeners yet (OK)"

  info "2.5 — Start Compose edge caddy + reload"
  docker compose -f "$COMPOSE_FILE" --profile edge up -d caddy
  bash "${REPO}/infra/deploy/caddy-compose-reload.sh"

  info "2.6 — Smoke (must PASS before CADDY_EDGE=compose)"
  bash "${REPO}/infra/deploy/smoke-cms.sh" "$SITE"
  bash "${REPO}/infra/deploy/smoke.sh" "$SITE"
}

# ---------------------------------------------------------------------------
# Post-cutover verification (matches owner audit template)
# ---------------------------------------------------------------------------
post_verify() {
  info "Post-cutover verification"
  echo "hostname: $(hostname -f 2>/dev/null || hostname)"

  echo "--- host Caddy (expect: inactive) ---"
  systemctl is-active caddy 2>/dev/null || echo "inactive/failed (expected)"

  echo "--- Compose caddy (expect: running, 80/443 published) ---"
  docker ps --filter name=caddy --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'

  echo "--- apt upgradable ---"
  apt list --upgradable 2>/dev/null | grep -vc '^Listing' || true

  echo "--- SSH listeners (expect: BOTH 22 and 2222 — no closure) ---"
  sudo ss -lntp | grep -E ':22\b|:2222\b'

  echo "--- public probes ---"
  curl -sI "${SITE}/health.json" | head -5
  curl -sI "${SITE}/staff/login/" | head -5

  if command -v gh >/dev/null 2>&1; then
    echo "--- gh ---"
    gh --version
    gh variable list 2>/dev/null | grep -E '^CADDY_EDGE' || echo "CADDY_EDGE not set yet"
  else
    echo "gh: NOT INSTALLED — set CADDY_EDGE via GitHub web UI (see below)"
  fi
}

# ---------------------------------------------------------------------------
# CADDY_EDGE=compose — ONLY after smoke PASS
# ---------------------------------------------------------------------------
set_caddy_edge_compose() {
  info "Set GitHub repository variable CADDY_EDGE=compose (after smoke PASS only)"
  cat <<'EOF'

Choose ONE path:

── Path A: GitHub web UI (no gh on VPS) ──
  1. https://github.com/tahamohamadi-ir/Taha-personal-platform/settings/variables/actions
  2. New repository variable
     Name:  CADDY_EDGE
     Value: compose
  3. Save. Next CD run reloads Compose caddy (not caddy-sync.sh).

── Path B: Install gh on VPS ──
  sudo apt install gh -y
  gh auth login
    → GitHub.com → HTTPS → authenticate (browser or token)
    → scopes: repo (and admin:repo_hook if variable set fails)

  cd /home/deploy/cms-repo
  gh variable set CADDY_EDGE --body compose \
    --repo tahamohamadi-ir/Taha-personal-platform

  gh variable list --repo tahamohamadi-ir/Taha-personal-platform | grep CADDY_EDGE

Do NOT set CADDY_EDGE before smoke PASS.
Do NOT enable CMS_CD_AUTO_MIGRATE.
EOF
  pause "After you set CADDY_EDGE, press Enter to re-check (optional)"
  post_verify
}

# ---------------------------------------------------------------------------
# Rollback — Caddy ONLY (host edge restore)
# Usage: bash infra/deploy/owner-vps-maintenance.sh rollback /etc/caddy/Caddyfile.bak-YYYYMMDDHHMMSS
# ---------------------------------------------------------------------------
rollback_caddy() {
  info "ROLLBACK — restore host systemd Caddy (Caddy only; apt changes NOT reverted)"
  local bak="${1:-}"
  if [ -z "$bak" ]; then
    echo "Available backups:"
    ls -1 /etc/caddy/Caddyfile.bak-* 2>/dev/null || true
    read -r -p "Enter full backup path: " bak
  fi
  [ -f "$bak" ] || die "backup not found: $bak"

  cd "$REPO"
  docker compose -f "$COMPOSE_FILE" --profile edge stop caddy || true

  sudo cp -a "$bak" /etc/caddy/Caddyfile
  sudo caddy validate --config /etc/caddy/Caddyfile
  sudo systemctl enable --now caddy

  echo "If CADDY_EDGE was set, remove/unset it in GitHub → Settings → Variables"
  bash "${REPO}/infra/deploy/smoke-cms.sh" "$SITE"

  info "Rollback complete — host Caddy edge restored"
  systemctl is-active caddy
  docker ps --filter name=caddy
}

# ---------------------------------------------------------------------------
# RISK-0006 — documented acceptance (NO SSH changes in this script)
# ---------------------------------------------------------------------------
document_risk_0006() {
  cat <<'EOF'

RISK-0006 resolution (owner decision — no UFW/sshd changes in this run):
  • Canonical deploy: deploy@85.192.29.196:2222 (key-only)
  • Port 22 retained intentionally for VPN tunnel
  • Both ports 22 and 2222 remain open in UFW and sshd
  • Residual attack surface accepted with key-only policy

Record in WORK_LOG as LOG-0210 and close RISK-0006 in RISK_REGISTER.md.
EOF
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
  preflight
  pause "Start Phase 1 (apt upgrade)?"
  phase1_apt_upgrade
  pause "Start Phase 2 (Caddy cutover)?"
  phase2_caddy_cutover
  post_verify
  document_risk_0006
  set_caddy_edge_compose

  info "DONE"
  echo "Caddy backup for rollback: ${ROLLBACK_HINT:-/etc/caddy/Caddyfile.bak-*}"
  echo "Rollback command example:"
  echo "  bash infra/deploy/owner-vps-maintenance.sh rollback ${ROLLBACK_HINT:-/etc/caddy/Caddyfile.bak-YYYYMMDDHHMMSS}"
}

if [[ "${1:-}" == "rollback" ]]; then
  rollback_caddy "${2:-}"
  exit 0
fi

main "$@"
