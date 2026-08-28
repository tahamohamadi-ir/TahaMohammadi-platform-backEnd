#!/usr/bin/env bash
# Minimal CMS smoke after Caddy proxy is applied.
# Usage: bash infra/deploy/smoke-cms.sh https://tahamohamadi.ir

set -euo pipefail

BASE_URL="${1:-https://tahamohamadi.ir}"
BASE_URL="${BASE_URL%/}"

fail=0

WEB_LOOPBACK="${WEB_LOOPBACK:-http://127.0.0.1:13080}"

# Use mktemp (not fixed /tmp names) — stale root-owned files cause curl exit 23.
LOOPBACK_BODY="$(mktemp -t cms-smoke-loopback.XXXXXX)"
PUBLIC_BODY="$(mktemp -t cms-smoke-public.XXXXXX)"
cleanup() { rm -f "$LOOPBACK_BODY" "$PUBLIC_BODY"; }
trap cleanup EXIT

curl_check() {
  local label="$1"
  local url="$2"
  local expect="$3"
  local body_file="$4"
  local code
  if ! code="$(curl -sS -o "$body_file" -w "%{http_code}" "$url")"; then
    echo "FAIL ${label} curl error (url=${url})" >&2
    fail=1
    return
  fi
  if [[ "$code" != "$expect" ]]; then
    echo "FAIL ${label} expected ${expect} got ${code} (url=${url})" >&2
    fail=1
  else
    echo "PASS ${label} ${code}"
  fi
}

check_loopback() {
  curl_check "loopback ${1}" "${WEB_LOOPBACK}${1}" "$2" "$LOOPBACK_BODY"
}

check() {
  curl_check "${1}" "${BASE_URL}${1}" "$2" "$PUBLIC_BODY"
}

# ADR-0027 Slice 1: nginx web container on loopback (before public edge checks).
check_loopback "/" "200"
check_loopback "/health.json" "200"
if ! grep -q '"service":"static"' "$LOOPBACK_BODY" && ! grep -q '"service": "static"' "$LOOPBACK_BODY"; then
  echo "FAIL loopback /health.json is not the static-site payload" >&2
  fail=1
fi

# Custom React admin SPA (ADM-1 cutover) — should return the SPA shell.
check "/admin/" "200"
# SPA login (primary) + Django staff login (LOGIN_URL / preview / HTML MFA).
# /admin/login/ is a React route: SSR HTML is often only the #root shell (no
# password field until JS). Accept the SPA mount; require real form markers on
# /staff/login/ (server-rendered Django).
check "/admin/login/" "200"
if ! grep -qiE 'id=["'\'']root["'\'']' "$PUBLIC_BODY" \
  && ! grep -qiE "password|ورود|sign.in|login|email" "$PUBLIC_BODY"; then
  echo "FAIL /admin/login/ is neither SPA shell (#root) nor a sign-in page" >&2
  fail=1
fi
check "/staff/login/" "200"
if ! grep -qiE "password|ورود|sign.in|login|Staff" "$PUBLIC_BODY"; then
  echo "FAIL /staff/login/ is not a sign-in page" >&2
  fail=1
fi
check "/health/" "200"
if ! grep -q '"db"' "$PUBLIC_BODY"; then
  echo "FAIL /health/ body is not CMS JSON" >&2
  fail=1
fi
# Must remain the static artifact — /health* glob would steal this path.
check "/health.json" "200"
if ! grep -q '"service":"static"' "$PUBLIC_BODY" && ! grep -q '"service": "static"' "$PUBLIC_BODY"; then
  echo "FAIL /health.json was not the static-site payload (Caddy must not proxy /health*)" >&2
  fail=1
fi
# Bare /admin must redirect (308) to /admin/ or return 200 (SPA served directly).
code="$(curl -sS -o /dev/null -w "%{http_code}" "${BASE_URL}/admin")"
if [[ "$code" != "200" && "$code" != "308" ]]; then
  echo "FAIL /admin expected 200 or 308 got ${code}" >&2
  fail=1
else
  echo "PASS /admin ${code}"
fi

check "/" "200"

if [[ "$fail" -ne 0 ]]; then
  exit 1
fi
echo "CMS smoke PASS"
