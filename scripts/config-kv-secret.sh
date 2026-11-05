#!/bin/bash
# Enable KV v2 at kvv2/ and seed one random API key per apps.json entry at
# kvv2/<environment>/<application_name>/api-key. Production keys start CANARY-PROD-. Safe to re-run:
# an existing key is kept (use task demo:kv-rotate to write a new version).
#
# Usage: scripts/config-kv-secret.sh
set -euo pipefail
# shellcheck source=scripts/lib.sh
source "$(dirname "$0")/lib.sh"

VAULT_TOKEN="$(root_token)"
export VAULT_TOKEN

vault_cmd secrets enable -path=kvv2 kv-v2 2>/dev/null || log "kvv2/ already enabled"
vault_cmd write kvv2/config max_versions=5 >/dev/null

while read -r row <&3; do
  app="$(jq -r .application_name <<<"${row}")"
  env="$(jq -r .environment <<<"${row}")"
  path="kvv2/${env}/${app}/api-key"
  if vault_cmd kv get "${path}" >/dev/null 2>&1; then
    log "${path} exists - kept"
    continue
  fi
  prefix=""
  [ "${env}" = production ] && prefix="CANARY-PROD-"
  vault_cmd kv put "${path}" \
    api_key="${prefix}$(openssl rand -hex 24)" \
    issuer=demo-partner-api \
    rotated_at="$(date -u +%FT%TZ)" >/dev/null
  log "Seeded ${path}"
done 3< <(jq -c '.[]' "${APPS_FILE}")
