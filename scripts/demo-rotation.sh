#!/bin/bash
# Scenario 6: watch payments-preview's database user change at or before max_ttl, then confirm
# the old role is gone. With a demo TTL (e.g. 2m) the db roles and the auth role are shortened
# for the run and restored afterwards.
#
# Usage: scripts/demo-rotation.sh <demo_ttl|""> <db_default_ttl> <db_max_ttl> <token_ttl> <token_max_ttl>
set -euo pipefail
# shellcheck source=scripts/lib.sh
source "$(dirname "$0")/lib.sh"

DEMO_TTL="${1:-}"
DB_DEFAULT_TTL="${2:-10m}"
DB_MAX_TTL="${3:-30m}"
TOKEN_TTL="${4:-15m}"
TOKEN_MAX_TTL="${5:-30m}"
TARGET="db-app-payments-preview"
DIR="$(dirname "$0")"

minutes() { local v="$1"; [[ "${v}" =~ ^([0-9]+)m$ ]] || { err "TTL must be like 2m, got ${v}"; exit 1; }; echo "${BASH_REMATCH[1]}"; }

restart_apps() {
  for app in $(app_names); do kubectl rollout restart deploy/db-app -n "${app}" >/dev/null; done
  for app in $(app_names); do kubectl rollout status deploy/db-app -n "${app}" --timeout=3m >/dev/null; done
  sleep 5
}

restore() {
  log "Restoring TTLs (db ${DB_DEFAULT_TTL}/${DB_MAX_TTL}, token ${TOKEN_TTL}/${TOKEN_MAX_TTL})"
  "${DIR}/config-db-secret.sh" "${DB_DEFAULT_TTL}" "${DB_MAX_TTL}" >/dev/null
  "${DIR}/config-k8s-auth.sh" "${TOKEN_TTL}" "${TOKEN_MAX_TTL}" >/dev/null
  restart_apps
}

max_min="$(minutes "${DB_MAX_TTL}")"
if [ -n "${DEMO_TTL}" ]; then
  max_min="$(minutes "${DEMO_TTL}")"
  half="$(( max_min > 1 ? max_min / 2 : 1 ))m"
  log "Demo TTLs: db ${half}/${DEMO_TTL}, token ${half}/${DEMO_TTL} (max_ttl never above token_max_ttl)"
  "${DIR}/config-db-secret.sh" "${half}" "${DEMO_TTL}" >/dev/null
  "${DIR}/config-k8s-auth.sh" "${half}" "${DEMO_TTL}" >/dev/null
  trap restore EXIT
  restart_apps
fi

timeout=$(( (max_min + 3) * 60 ))
first="$(app_get "${TARGET}" /whoami | jq -r .current_user)"
log "${TARGET} starts as ${first}; watching for up to ${timeout}s"
start=$(date +%s)
new=""
while [ $(( $(date +%s) - start )) -lt "${timeout}" ]; do
  sleep 15
  w="$(app_get "${TARGET}" /whoami 2>/dev/null || echo '{}')"
  user="$(jq -r '.current_user // "unavailable"' <<<"${w}")"
  printf '  %4ss  %s  (%ss left on lease)\n' "$(( $(date +%s) - start ))" "${user}" "$(jq -r '.lease_seconds_remaining // "?"' <<<"${w}")"
  if [ "${user}" != unavailable ] && [ "${user}" != "${first}" ]; then new="${user}"; break; fi
done

[ -n "${new}" ] || { err "username did not change within ${timeout}s"; exit 1; }
printf '  PASS  6  %s -> %s after %ss\n' "${first}" "${new}" "$(( $(date +%s) - start ))"

sleep 5
left="$(kubectl exec deploy/postgres -n postgres -- psql -U postgres -Atc "SELECT count(*) FROM pg_roles WHERE rolname = '${first}'")"
[ "${left}" = 0 ] || { err "old role ${first} still in pg_roles"; exit 1; }
printf '  PASS  6  old role %s gone from pg_roles\n' "${first}"
log "demo:rotation passed"
