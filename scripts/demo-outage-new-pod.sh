#!/bin/bash
# Scenario 14: while Vault is sealed, a new or restarted pod cannot start because its Proxy
# cannot log in (the startup probe never passes). Unseals afterwards and waits for recovery.
#
# Usage: scripts/demo-outage-new-pod.sh [app-name]
set -euo pipefail
# shellcheck source=scripts/lib.sh
source "$(dirname "$0")/lib.sh"

APP="${1:-db-app-payments-preview}"
DIR="$(dirname "$0")"
VAULT_TOKEN="$(root_token)"
export VAULT_TOKEN

log "Sealing Vault and deleting the ${APP} pod"
trap '"${DIR}/unseal-vault.sh" >/dev/null 2>&1 || true' EXIT
vault_cmd operator seal
kubectl delete pod -n "${APP}" -l app.kubernetes.io/name=db-app --wait=false >/dev/null

sleep 45
kubectl get pods -n "${APP}" -l app.kubernetes.io/name=db-app
# Ready app containers among pods that are not being deleted
ready="$(kubectl get pods -n "${APP}" -l app.kubernetes.io/name=db-app -o json \
  | jq '[.items[] | select(.metadata.deletionTimestamp == null)
         | .status.containerStatuses[]? | select(.name == "app" and .ready)] | length')"
if [ "${ready}" -gt 0 ]; then
  err "14: a pod became Ready while Vault was sealed"
  exit 1
fi
printf '  PASS  14 new pod not Ready while sealed (Proxy cannot log in)\n'
kubectl logs -n "${APP}" -l app.kubernetes.io/name=db-app -c vault-proxy --tail=3 2>/dev/null || true

log "Unsealing"
"${DIR}/unseal-vault.sh" >/dev/null
trap - EXIT
kubectl rollout status deploy/db-app -n "${APP}" --timeout=3m
kubectl wait --for=condition=Ready pod -l app.kubernetes.io/name=db-app -n "${APP}" --timeout=3m >/dev/null
printf '  PASS  14 pod recovered after unseal: %s\n' "$(app_get "${APP}" /whoami | jq -r .current_user)"
