#!/bin/bash
# Isolation scenarios 3, 4, 5, 8 and 11 from docs/solution-design.md. Exits non-zero on any
# unexpected result.
#
# Usage: scripts/demo-isolation.sh
set -euo pipefail
# shellcheck source=scripts/lib.sh
source "$(dirname "$0")/lib.sh"

APP="db-app-payments-preview"
failures=0
pass() { printf '  PASS  %s\n' "$*"; }
fail() { printf '  FAIL  %s\n' "$*"; failures=$((failures + 1)); }

# Status Vault returns for <path> when the app (through its Proxy) tries to read it
try_status() {
  local encoded
  encoded="$(jq -rn --arg p "$1" '$p|@uri')"
  app_get "${APP}" "try?path=${encoded}" | jq -r .status
}

expect_status() {
  local scenario="$1" path="$2" want="$3" got
  got="$(try_status "${path}")"
  if [ "${got}" = "${want}" ]; then pass "${scenario} ${APP} -> ${path}: ${got}"; else fail "${scenario} ${APP} -> ${path}: got ${got}, want ${want}"; fi
}

VAULT_TOKEN="$(root_token)"
export VAULT_TOKEN
since="$(date -u +%FT%TZ)"

log "Scenarios 3, 4, 11: the templated policy, through the app's own Proxy"
expect_status "ctl" "db/creds/preview-payments" 200
expect_status "3 " "db/creds/preview-checkout" 403
expect_status "4 " "db/creds/production-payments" 403
expect_status "ctl" "kvv2/data/preview/payments/api-key" 200
expect_status "11" "kvv2/data/preview/checkout/api-key" 403
expect_status "11" "kvv2/data/production/payments/api-key" 403

log "Audit log (vault-0 stdout): denied requests since ${since}"
# vault-0's log mixes audit JSON with plain server lines; keep only the JSON so jq never aborts
audit="$(kubectl logs "${VAULT_POD}" -n "${VAULT_NS}" --since-time="${since}" 2>/dev/null | grep '^{' || true)"
for path in db/creds/preview-checkout db/creds/production-payments kvv2/data/production/payments/api-key; do
  if jq -c --arg p "${path}" 'select(.type=="response" and .request.path==$p and (.error // "" | test("permission denied")))' \
      2>/dev/null <<<"${audit}" | grep -q .; then
    pass "audit shows permission denied for ${path}"
  else
    fail "audit has no permission-denied entry for ${path}"
  fi
done

log "Scenario 5: payments-preview credentials cannot even connect to the production database"
creds="$(vault_cmd read -format=json db/creds/preview-payments)"
user="$(jq -r .data.username <<<"${creds}")"
lease="$(jq -r .lease_id <<<"${creds}")"
out="$(psql_as "${user}" "$(jq -r .data.password <<<"${creds}")" -d production -Atc 'SELECT 1' 2>&1 || true)"
unset creds
vault_cmd lease revoke -sync "${lease}" >/dev/null
if grep -q 'permission denied for database' <<<"${out}"; then
  pass "5  psql -d production as ${user}: permission denied for database"
else
  fail "5  psql -d production as ${user}: ${out}"
fi

log "Scenario 8: the app team cannot change the annotations that pick the credentials"
sa="${APP}-sa"
if [ "$(kubectl auth can-i get pods -n "${APP}" --as=app-team)" = yes ]; then pass "8  app-team can get pods"; else fail "8  app-team cannot get pods"; fi
out="$(kubectl annotate sa "${sa}" -n "${APP}" --as=app-team --overwrite \
  vault.hashicorp.com/alias-metadata-environment=production 2>&1 || true)"
if grep -qi forbidden <<<"${out}"; then
  pass "8  app-team annotate ${sa} environment=production: forbidden"
else
  fail "8  app-team annotate was not forbidden: ${out}"
fi
current="$(kubectl get sa "${sa}" -n "${APP}" -o jsonpath='{.metadata.annotations.vault\.hashicorp\.com/alias-metadata-environment}')"
if [ "${current}" = preview ]; then pass "8  annotation unchanged (environment=${current})"; else fail "8  annotation is now ${current}"; fi

if [ "${failures}" -gt 0 ]; then
  err "demo:isolation: ${failures} check(s) failed"
  exit 1
fi
log "demo:isolation passed"
