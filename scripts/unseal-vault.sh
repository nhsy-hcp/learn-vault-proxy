#!/bin/bash
# Unseal vault-0 with 3 of the 5 keys in vault-init.json and wait for it to be Ready. Safe to re-run.
#
# Usage: scripts/unseal-vault.sh
set -euo pipefail
# shellcheck source=scripts/lib.sh
source "$(dirname "$0")/lib.sh"

[ -f "${INIT_FILE}" ] || { err "${INIT_FILE} not found - run task init:vault"; exit 1; }

kubectl wait --for=jsonpath='{.status.phase}'=Running pod/"${VAULT_POD}" -n "${VAULT_NS}" --timeout=5m

if [ "$(vault_cmd_notoken status -format=json 2>/dev/null | jq -r '.sealed')" = "false" ]; then
  log "Vault is already unsealed"
else
  # Keys go on stdin (key=-); `vault operator unseal <key>` would put them on the command line
  for i in 0 1 2; do
    jq -j ".unseal_keys_b64[${i}]" "${INIT_FILE}" | vault_cmd_notoken write sys/unseal key=- >/dev/null
  done
  log "Vault unsealed"
fi

kubectl wait --for=condition=Ready pod/"${VAULT_POD}" -n "${VAULT_NS}" --timeout=2m
vault_cmd_notoken status | grep -E 'Sealed|HA Mode|Version|Storage Type'
