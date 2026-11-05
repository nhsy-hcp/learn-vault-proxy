#!/bin/bash
# Shared helpers, sourced by other scripts (not run directly).
#
# Secrets (root token, unseal keys, passwords, JWTs) are always passed on stdin, never as
# command-line arguments, so they do not show up in `ps` or in the kubectl exec request URL.
#
# Usage: source "$(dirname "$0")/lib.sh"
set -euo pipefail

VAULT_NS="${VAULT_NS:-vault}"
VAULT_POD="${VAULT_POD:-vault-0}"
INIT_FILE="${INIT_FILE:-vault-init.json}"
APPS_FILE="${APPS_FILE:-vault-ce/apps.json}"

log() { printf '==> %s\n' "$*"; }
err() { printf 'ERROR: %s\n' "$*" >&2; }

# Root token from vault-init.json (demo only; production revokes the root token).
root_token() {
  [ -f "${INIT_FILE}" ] || { err "${INIT_FILE} not found - run task init:vault"; return 1; }
  jq -r '.root_token' "${INIT_FILE}"
}

# Inside vault-0: read the token from the first stdin line, then exec vault with the rest of stdin.
# shellcheck disable=SC2016 # expanded by the pod's sh, not here
VAULT_EXEC_WRAPPER='IFS= read -r VAULT_TOKEN; export VAULT_TOKEN; exec vault "$@"'

# Run a vault CLI command inside vault-0 with VAULT_TOKEN (sent on stdin). No other stdin.
vault_cmd() {
  printf '%s\n' "${VAULT_TOKEN:?VAULT_TOKEN must be set}" \
    | kubectl exec -i "${VAULT_POD}" -n "${VAULT_NS}" -- sh -c "${VAULT_EXEC_WRAPPER}" sh "$@"
}

# Like vault_cmd, but forwards the caller's stdin after the token, for `vault ... -` or key=- inputs.
vault_cmd_in() {
  { printf '%s\n' "${VAULT_TOKEN:?VAULT_TOKEN must be set}"; cat; } \
    | kubectl exec -i "${VAULT_POD}" -n "${VAULT_NS}" -- sh -c "${VAULT_EXEC_WRAPPER}" sh "$@"
}

# Run a vault CLI command inside vault-0 without a token (status, init, unseal). Stdin is passed through.
vault_cmd_notoken() {
  kubectl exec -i "${VAULT_POD}" -n "${VAULT_NS}" -- vault "$@"
}

# Run psql inside the postgres pod as <user> with <password> sent on stdin.
# Usage: psql_as <user> <password> <psql args...>
psql_as() {
  local user="$1" password="$2"
  shift 2
  # shellcheck disable=SC2016 # expanded by the pod's sh, not here
  printf '%s\n' "${password}" | kubectl exec -i deploy/postgres -n postgres -- \
    sh -c 'IFS= read -r PGPASSWORD; export PGPASSWORD; exec psql "$@"' sh -h 127.0.0.1 -U "${user}" "$@"
}

# Names of every app instance in apps.json (one per line)
app_names() { jq -r '.[].name' "${APPS_FILE}"; }

# GET an app endpoint through the API server's service proxy (no port-forward needed).
# Usage: app_get <app-name> <path-with-query>
app_get() {
  kubectl get --raw "/api/v1/namespaces/${1}/services/db-app:http/proxy/${2#/}"
}
