#!/bin/bash
# Enable kubernetes-auth-mount with ServiceAccount annotations as alias metadata, write the
# templated db-app policy (accessor substituted) and the db-app role. Safe to re-run.
#
# Usage: scripts/config-k8s-auth.sh <token_ttl> <token_max_ttl>
set -euo pipefail
# shellcheck source=scripts/lib.sh
source "$(dirname "$0")/lib.sh"

TOKEN_TTL="${1:-15m}"
TOKEN_MAX_TTL="${2:-30m}"
AUTH_MOUNT="kubernetes-auth-mount"
POLICY_FILE="vault-ce/db-secrets/db-app.hcl"
ROLE_FILE="vault-ce/db-secrets/db-app-role.json"

VAULT_TOKEN="$(root_token)"
export VAULT_TOKEN

vault_cmd auth enable -path="${AUTH_MOUNT}" kubernetes 2>/dev/null || log "${AUTH_MOUNT} already enabled"

kube_host="$(kubectl exec "${VAULT_POD}" -n "${VAULT_NS}" -- printenv KUBERNETES_PORT_443_TCP_ADDR)"
# Vault reads ServiceAccounts with this token, so the SA also needs the vault-sa-reader ClusterRole
reviewer_b64="$(kubectl get secret vault-token-secret -n "${VAULT_NS}" -o jsonpath='{.data.token}')"
[ -n "${reviewer_b64}" ] || { err "vault-token-secret has no token yet - run task config:vault"; exit 1; }
# The JWT goes on stdin (token_reviewer_jwt=-), never on the command line
base64 --decode <<<"${reviewer_b64}" | vault_cmd_in write "auth/${AUTH_MOUNT}/config" \
  kubernetes_host="https://${kube_host}:443" \
  token_reviewer_jwt=- \
  use_annotations_as_alias_metadata=true >/dev/null
log "Configured auth/${AUTH_MOUNT} (host https://${kube_host}:443, annotations as alias metadata)"

accessor="$(vault_cmd auth list -format=json | jq -r --arg m "${AUTH_MOUNT}/" '.[$m].accessor // empty')"
[ -n "${accessor}" ] || { err "Auth mount ${AUTH_MOUNT} not found"; exit 1; }
log "Using ${AUTH_MOUNT} accessor ${accessor}"

# Alias metadata is addressed by mount accessor, which is only known after enabling the mount
sed "s/__ACCESSOR__/${accessor}/g" "${POLICY_FILE}" | vault_cmd_in policy write db-app - >/dev/null
log "Wrote policy db-app"

jq --arg ttl "${TOKEN_TTL}" --arg max "${TOKEN_MAX_TTL}" \
  '.token_ttl = $ttl | .token_max_ttl = $max' "${ROLE_FILE}" \
  | vault_cmd_in write "auth/${AUTH_MOUNT}/role/db-app" - >/dev/null
log "Wrote auth/${AUTH_MOUNT}/role/db-app (token_ttl=${TOKEN_TTL}, token_max_ttl=${TOKEN_MAX_TTL})"
