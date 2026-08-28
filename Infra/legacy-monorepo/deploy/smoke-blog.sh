#!/usr/bin/env bash
# Blog + research smoke after static deploy (P4-05 / P5 prod evidence).
# Usage: bash infra/deploy/smoke-blog.sh https://tahamohamadi.ir

set -euo pipefail

BASE_URL="${1:-https://tahamohamadi.ir}"
BASE_URL="${BASE_URL%/}"
fail=0
BODY="$(mktemp)"
trap 'rm -f "$BODY"' EXIT

check() {
  local path="$1"
  local expect="$2"
  local code
  code="$(curl -sS -o "$BODY" -w "%{http_code}" "${BASE_URL}${path}")"
  if [[ "$code" != "$expect" ]]; then
    echo "FAIL ${path} expected ${expect} got ${code}" >&2
    fail=1
  else
    echo "PASS ${path} ${code}"
  fi
}

for locale in en fa; do
  # /{locale}/blog/ permanently redirects to the writing tree (IA
  # writing-canonical). Static serving emits a 200 meta-refresh stub, so assert
  # the redirect target instead of a bare 200.
  check "/${locale}/writing/" "200"
  check "/${locale}/research/" "200"
  body_code="$(curl -sS -o "$BODY" -w "%{http_code}" "${BASE_URL}/${locale}/blog/")"
  if [[ "$body_code" != "200" && "$body_code" != "301" && "$body_code" != "308" ]]; then
    echo "FAIL /${locale}/blog/ expected 200/301/308 got ${body_code}" >&2
    fail=1
  elif ! grep -q "/${locale}/writing/" "$BODY"; then
    echo "FAIL /${locale}/blog/ does not point at /${locale}/writing/" >&2
    fail=1
  else
    echo "PASS /${locale}/blog/ -> /${locale}/writing/ (${body_code})"
  fi
done

# Public /api/ is live (DEFER-0017 CLOSED): published-only Ninja JSON.
check "/api/articles/en" "200"

if [[ "$fail" -ne 0 ]]; then
  exit 1
fi
echo "blog smoke PASS"
