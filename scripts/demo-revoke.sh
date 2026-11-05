#!/bin/bash
# Scenario 7: break-glass revoke of every payments-preview database user. The app sees its
# sessions terminated, clears its Proxy cache entry and gets a new user; other apps are unaffected.
#
# Usage: scripts/demo-revoke.sh [timeout_seconds]
set -euo pipefail
# shellcheck source=scripts/lib.sh
source "$(dirname "$0")/lib.sh"

TIMEOUT="${1:-60}"
TARGET="db-app-payments-preview"
VAULT_TOKEN="$(root_token)"
export VAULT_TOKEN

# app -> current_user, as a JSON object (bash 3.2 on macOS has no associative arrays)
before='{}'
for app in $(app_names); do
  u="$(app_get "${app}" /whoami | jq -r .current_user)"
  before="$(jq -c --arg a "${app}" --arg u "${u}" '.[$a] = $u' <<<"${before}")"
  printf '  %-28s %s\n' "${app}" "${u}"
done
user_before() { jq -r --arg a "$1" '.[$a]' <<<"${before}"; }
old_user="$(user_before "${TARGET}")"

log "vault lease revoke -prefix db/creds/preview-payments"
start=$(date +%s)
vault_cmd lease revoke -sync -prefix db/creds/preview-payments

new_user=""
while [ $(( $(date +%s) - start )) -lt "${TIMEOUT}" ]; do
  user="$(app_get "${TARGET}" /whoami 2>/dev/null | jq -r '.current_user // empty' || true)"
  if [ -n "${user}" ] && [ "${user}" != "${old_user}" ]; then
    new_user="${user}"
    break
  fi
  sleep 3
done
elapsed=$(( $(date +%s) - start ))

if [ -z "${new_user}" ]; then
  err "${TARGET} did not get a new user within ${TIMEOUT}s"
  exit 1
fi
printf '  PASS  7  %s: %s -> %s in %ss\n' "${TARGET}" "${old_user}" "${new_user}" "${elapsed}"

gone="$(kubectl exec deploy/postgres -n postgres -- psql -U postgres -Atc \
  "SELECT count(*) FROM pg_roles WHERE rolname = '${old_user}'")"
[ "${gone}" = 0 ] || { err "old role ${old_user} still in pg_roles"; exit 1; }
printf '  PASS  7  old role dropped from pg_roles\n'

for app in $(app_names); do
  [ "${app}" = "${TARGET}" ] && continue
  now="$(app_get "${app}" /whoami | jq -r .current_user)"
  [ "${now}" = "$(user_before "${app}")" ] || { err "${app} changed user ($(user_before "${app}") -> ${now})"; exit 1; }
  printf '  PASS  7  %s unaffected (%s)\n' "${app}" "${now}"
done

log "App log events (kubectl logs -n ${TARGET} deploy/db-app -c app):"
kubectl logs -n "${TARGET}" deploy/db-app -c app --since=2m \
  | jq -rc 'select(.event | test("lost|failed|cache_cleared|acquired|rotated|swapped|retry")) | [.ts, .event, (.sqlstate // .username // "")] | @tsv' \
  | tail -10
log "demo:revoke passed"
