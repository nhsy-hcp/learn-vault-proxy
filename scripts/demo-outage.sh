#!/bin/bash
# Scenarios 13 and 15: seal Vault for N minutes and watch the static (KV) and dynamic (DB)
# secrets on every app, then unseal and confirm recovery. CE expectations: /apikey keeps
# answering from app-memory with stale=true; /whoami keeps working on existing sessions.
#
# Usage: scripts/demo-outage.sh <outage_minutes> <kv_refresh_seconds>
set -euo pipefail
# shellcheck source=scripts/lib.sh
source "$(dirname "$0")/lib.sh"

MINUTES="${1:-12}"
REFRESH="${2:-30}"
INTERVAL=15
DIR="$(dirname "$0")"
VAULT_TOKEN="$(root_token)"
export VAULT_TOKEN

snapshot() {
  for app in $(app_names); do
    k="$(app_get "${app}" /apikey 2>/dev/null || echo '{}')"
    w="$(app_get "${app}" /whoami 2>/dev/null || echo '{}')"
    printf '  %-28s apikey v%s %-11s stale=%-5s | db %s\n' "${app}" \
      "$(jq -r '.version // "?"' <<<"${k}")" "$(jq -r '.source // "error"' <<<"${k}")" \
      "$(jq -r 'if has("stale") then .stale else "?" end' <<<"${k}")" "$(jq -r '.current_user // "unavailable"' <<<"${w}")"
  done
}

log "Before the outage"
snapshot

log "Sealing Vault (vault operator seal) for ${MINUTES} min"
trap '"${DIR}/unseal-vault.sh" >/dev/null 2>&1 || true' EXIT
vault_cmd operator seal
start=$(date +%s)
saw_memory=false
while [ $(( $(date +%s) - start )) -lt $(( MINUTES * 60 )) ]; do
  sleep "${INTERVAL}"
  log "t+$(( $(date +%s) - start ))s (sealed)"
  out="$(snapshot)"
  echo "${out}"
  grep -q 'app-memory  stale=true' <<<"${out}" && saw_memory=true
done

log "Unsealing"
"${DIR}/unseal-vault.sh" >/dev/null
trap - EXIT
recover_start=$(date +%s)
recovered=false
while [ $(( $(date +%s) - recover_start )) -lt 120 ]; do
  sleep 10
  out="$(snapshot)"
  if ! grep -qE 'stale=true|unavailable|error' <<<"${out}"; then recovered=true; break; fi
done
log "After unsealing (+$(( $(date +%s) - recover_start ))s)"
snapshot

if [ "${MINUTES}" -ge 1 ] && [ $(( MINUTES * 60 )) -gt "${REFRESH}" ]; then
  [ "${saw_memory}" = true ] || { err "13: /apikey never served from app-memory with stale=true"; exit 1; }
  printf '  PASS  13 /apikey served from app-memory (stale) while sealed\n'
fi
[ "${recovered}" = true ] || { err "15: apps did not recover within 120s of unsealing"; exit 1; }
printf '  PASS  15 KV refresh and DB access recovered after unseal\n'
log "demo:outage passed"
