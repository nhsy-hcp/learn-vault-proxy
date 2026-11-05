#!/bin/bash
# Initialise Vault once (Shamir 5 shares / threshold 3), save keys to vault-init.json and the
# root token to .env. Asks before re-initialising.
#
# Usage: scripts/init-vault.sh
set -euo pipefail
# shellcheck source=scripts/lib.sh
source "$(dirname "$0")/lib.sh"

if [ ! -f .env ]; then
  cp .env.template .env
  log ".env created from .env.template"
fi

initialized=$(vault_cmd_notoken status -format=json 2>/dev/null | jq -r '.initialized' || true)

if [ -f "${INIT_FILE}" ] || [ "${initialized}" = "true" ]; then
  echo "Vault is already initialised (initialized=${initialized:-unknown}, ${INIT_FILE} present: $([ -f "${INIT_FILE}" ] && echo yes || echo no))."
  if [ "${initialized}" = "true" ] && [ -f "${INIT_FILE}" ]; then
    echo "Nothing to do. To start over, delete the cluster with 'task clean'."
    exit 0
  fi
  if [ "${initialized}" = "true" ]; then
    err "Vault is initialised but ${INIT_FILE} is missing; the keys are lost. Run 'task clean' to start over."
    exit 1
  fi
  read -r -p "Overwrite ${INIT_FILE} and initialise again? (y/N): " reply
  if ! echo "${reply}" | grep -qi '^y$'; then
    echo "Initialisation cancelled."
    exit 1
  fi
fi

log "Initialising Vault"
tmp_file="$(mktemp "${TMPDIR:-/tmp}/vault-init.XXXXXX")"
trap 'rm -f "${tmp_file}"' EXIT
vault_cmd_notoken operator init -key-shares=5 -key-threshold=3 -format=json > "${tmp_file}"
mv "${tmp_file}" "${INIT_FILE}"
chmod 600 "${INIT_FILE}"
log "Keys saved to ${INIT_FILE} (git-ignored)"

token="$(jq -r '.root_token' "${INIT_FILE}")"
sed -i.bak "s|^export VAULT_TOKEN=.*|export VAULT_TOKEN=${token}|" .env
rm -f .env.bak
log "VAULT_TOKEN written to .env (git-ignored)"
