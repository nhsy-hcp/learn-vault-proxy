#!/bin/bash
# Scenario 17: a pod that shuts down revokes its own Proxy token (auth/token/revoke-self), so its
# dynamic database user is dropped within seconds instead of living until max_ttl. The
# replacement pod gets a new user.
#
# Usage: scripts/demo-shutdown.sh [app] [timeout_seconds]
set -euo pipefail
# shellcheck source=scripts/lib.sh
source "$(dirname "$0")/lib.sh"

TARGET="${1:-db-app-payments-preview}"
TIMEOUT="${2:-45}"
mkdir -p .tmp
applog=".tmp/demo-shutdown-app.log"

pg_role_count() {
  kubectl exec deploy/postgres -n postgres -- psql -U postgres -Atc \
    "SELECT count(*) FROM pg_roles WHERE rolname = '$1'"
}

old_user="$(app_get "${TARGET}" /whoami | jq -r .current_user)"
pod="$(kubectl get pod -l app.kubernetes.io/name=db-app -n "${TARGET}" -o json \
  | jq -r '[.items[] | select(.metadata.deletionTimestamp == null)][0].metadata.name')"
[ "$(pg_role_count "${old_user}")" = 1 ] || { err "${old_user} not in pg_roles before the test"; exit 1; }
printf '  %s pod %s uses %s\n' "${TARGET}" "${pod}" "${old_user}"

# Follow the app log until the container exits, to show the revoke-self event
kubectl logs -f "${pod}" -n "${TARGET}" -c app --since=1s >"${applog}" 2>/dev/null &
logs_pid=$!

log "kubectl delete pod ${pod} (SIGTERM -> app closes its pool -> revoke-self through Proxy)"
start=$(date +%s)
kubectl delete pod "${pod}" -n "${TARGET}" --wait=false >/dev/null

gone=""
while [ $(( $(date +%s) - start )) -lt "${TIMEOUT}" ]; do
  if [ "$(pg_role_count "${old_user}")" = 0 ]; then
    gone=yes
    break
  fi
  sleep 1
done
elapsed=$(( $(date +%s) - start ))
wait "${logs_pid}" 2>/dev/null || true

status="$(jq -r 'select(.event == "token_revoked_self") | .status' "${applog}" 2>/dev/null | tail -1 || true)"
if [ "${status}" = 204 ]; then
  printf '  PASS  17 app logged token_revoked_self (status 204)\n'
else
  err "app did not log token_revoked_self with status 204 (got '${status:-none}'); see ${applog}"
  exit 1
fi
if [ -z "${gone}" ]; then
  err "${old_user} still in pg_roles ${TIMEOUT}s after the pod was deleted"
  exit 1
fi
printf '  PASS  17 %s dropped from pg_roles %ss after pod deletion\n' "${old_user}" "${elapsed}"

kubectl rollout status deploy/db-app -n "${TARGET}" --timeout=3m >/dev/null
new_user="$(app_get "${TARGET}" /whoami | jq -r .current_user)"
[ "${new_user}" != "${old_user}" ] || { err "replacement pod reuses ${old_user}"; exit 1; }
printf '  PASS  17 replacement pod uses %s\n' "${new_user}"
log "demo:shutdown passed"
