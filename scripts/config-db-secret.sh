#!/bin/bash
# Enable the database secrets engine at db/, write the pg-preview and pg-production connections
# (each with its own admin login), one role per app, then rotate each admin password so only Vault knows it.
# Safe to re-run: an existing connection is left alone (its root password is already rotated).
#
# Usage: scripts/config-db-secret.sh <default_ttl> <max_ttl>
set -euo pipefail
# shellcheck source=scripts/lib.sh
source "$(dirname "$0")/lib.sh"

DEFAULT_TTL="${1:-10m}"
MAX_TTL="${2:-30m}"
PG_HOST="postgres.postgres.svc.cluster.local:5432"

VAULT_TOKEN="$(root_token)"
export VAULT_TOKEN

vault_cmd secrets enable -path=db database 2>/dev/null || log "db/ already enabled"

# connection name | database | admin login | allowed roles
CONNECTIONS=(
  "pg-preview|preview|vault_admin_preview|preview-payments,preview-checkout"
  "pg-production|production|vault_admin_production|production-payments"
)
# role | connection | group role
ROLES=(
  "preview-payments|pg-preview|preview_payments_rw"
  "preview-checkout|pg-preview|preview_checkout_rw"
  "production-payments|pg-production|production_payments_rw"
)

# Bootstrap password for an admin login, from the postgres-credentials Secret (never printed)
bootstrap_pw() {
  local key="${1//_/-}-password"
  kubectl get secret postgres-credentials -n postgres -o json | jq -r --arg k "${key}" '.data[$k]' | base64 --decode
}

for row in "${CONNECTIONS[@]}"; do
  IFS='|' read -r conn db admin allowed <<<"${row}"
  if vault_cmd read "db/config/${conn}" >/dev/null 2>&1; then
    vault_cmd write "db/config/${conn}" allowed_roles="${allowed}" >/dev/null
    log "db/config/${conn} exists - updated allowed_roles only"
    continue
  fi
  # The bootstrap password goes on stdin (password=-), never on the command line.
  # shellcheck disable=SC2016 # {{username}}/{{password}} are Vault templates, not shell
  bootstrap_pw "${admin}" | vault_cmd_in write "db/config/${conn}" \
    plugin_name=postgresql-database-plugin \
    connection_url="postgresql://{{username}}:{{password}}@${PG_HOST}/${db}?sslmode=disable" \
    allowed_roles="${allowed}" \
    username="${admin}" \
    password=- \
    password_authentication=scram-sha-256 >/dev/null
  vault_cmd write -f "db/rotate-root/${conn}" >/dev/null
  log "Wrote db/config/${conn} (database ${db}, ${admin}) and rotated its password"
done

for row in "${ROLES[@]}"; do
  IFS='|' read -r role conn group <<<"${row}"
  # Revocation ends the user's sessions, then drops it. VALID UNTIL is a second guard.
  vault_cmd write "db/roles/${role}" \
    db_name="${conn}" \
    default_ttl="${DEFAULT_TTL}" \
    max_ttl="${MAX_TTL}" \
    creation_statements="CREATE ROLE \"{{name}}\" WITH LOGIN PASSWORD '{{password}}' VALID UNTIL '{{expiration}}' IN ROLE ${group};" \
    revocation_statements="SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename = '{{name}}'; DROP ROLE IF EXISTS \"{{name}}\";" >/dev/null
  log "Wrote db/roles/${role} (${conn}, ${group}, ttl ${DEFAULT_TTL}/${MAX_TTL})"
done
