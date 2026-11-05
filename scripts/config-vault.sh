#!/bin/bash
# Enable the stdout file audit device (once), apply the token reviewer SA and SA-reader RBAC, and
# wait until the token controller has filled the reviewer token Secret. Safe to re-run.
#
# Usage: scripts/config-vault.sh
set -euo pipefail
# shellcheck source=scripts/lib.sh
source "$(dirname "$0")/lib.sh"

VAULT_TOKEN="$(root_token)"
export VAULT_TOKEN

if vault_cmd audit list -format=json | jq -e 'has("audit_stdout/")' >/dev/null; then
  log "Audit device audit_stdout/ already enabled"
else
  vault_cmd audit enable -path=audit_stdout file file_path=stdout
fi

kubectl apply -f vault-ce/vault-token-reviewer.yaml
kubectl apply -f vault-ce/vault-sa-reader-rbac.yaml

# The token controller fills .data.token asynchronously; config:k8s-auth needs it.
for i in $(seq 1 30); do
  if [ -n "$(kubectl get secret vault-token-secret -n "${VAULT_NS}" -o jsonpath='{.data.token}')" ]; then
    log "Reviewer token Secret is populated"
    exit 0
  fi
  echo "  Attempt ${i}/30 - waiting for vault-token-secret to be populated..."
  sleep 2
done
err "vault-token-secret was not populated"
exit 1
