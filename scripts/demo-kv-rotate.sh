#!/bin/bash
# Scenario 12: write a new API key version for one app and poll its /apikey until it shows.
#
# Usage: scripts/demo-kv-rotate.sh <app-name> <kv_refresh_seconds>
set -euo pipefail
# shellcheck source=scripts/lib.sh
source "$(dirname "$0")/lib.sh"

APP="${1:?Usage: demo-kv-rotate.sh <app-name> <kv_refresh_seconds> (app names: $(app_names | xargs))}"
REFRESH="${2:-30}"
row="$(jq -c --arg n "${APP}" '.[] | select(.name==$n)' "${APPS_FILE}")"
[ -n "${row}" ] || { err "unknown app ${APP}; choose one of: $(app_names | xargs)"; exit 1; }
env="$(jq -r .environment <<<"${row}")"
application="$(jq -r .application_name <<<"${row}")"
path="kvv2/${env}/${application}/api-key"

VAULT_TOKEN="$(root_token)"
export VAULT_TOKEN

old="$(app_get "${APP}" /apikey | jq -r .version)"
prefix=""
[ "${env}" = production ] && prefix="CANARY-PROD-"
vault_cmd kv put "${path}" api_key="${prefix}$(openssl rand -hex 24)" issuer=demo-partner-api \
  rotated_at="$(date -u +%FT%TZ)" >/dev/null
want="$(vault_cmd kv metadata get -format=json "${path}" | jq -r .data.current_version)"
log "Wrote ${path} version ${want} (app currently on version ${old}); polling /apikey"

start=$(date +%s)
while [ $(( $(date +%s) - start )) -lt $(( REFRESH + 30 )) ]; do
  s="$(app_get "${APP}" /apikey)"
  printf '  %3ss  version %s  source %s  x_cache %s\n' "$(( $(date +%s) - start ))" \
    "$(jq -r .version <<<"${s}")" "$(jq -r .source <<<"${s}")" "$(jq -r '.x_cache // "-"' <<<"${s}")"
  if [ "$(jq -r .version <<<"${s}")" = "${want}" ]; then
    printf '  PASS  12 %s picked up version %s in %ss\n' "${APP}" "${want}" "$(( $(date +%s) - start ))"
    exit 0
  fi
  sleep 5
done
err "${APP} did not pick up version ${want} within $(( REFRESH + 30 ))s"
exit 1
