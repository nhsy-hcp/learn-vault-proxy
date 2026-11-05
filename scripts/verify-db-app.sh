#!/bin/bash
# Acceptance checks for scenarios 1 (zero-secret bootstrap), 2 (own data) and 10 (own API key)
# on every app in apps.json. Exits non-zero on the first unexpected result.
#
# Usage: scripts/verify-db-app.sh
set -euo pipefail
# shellcheck source=scripts/lib.sh
source "$(dirname "$0")/lib.sh"

fail() { err "$*"; exit 1; }
pass() { printf '  PASS  %s\n' "$*"; }

for app in $(app_names); do
  env="$(jq -r --arg n "${app}" '.[] | select(.name==$n) | .environment' "${APPS_FILE}")"
  application="$(jq -r --arg n "${app}" '.[] | select(.name==$n) | .application_name' "${APPS_FILE}")"
  log "${app}"
  # Wait on the Deployment, not a pod label: a label selector can also match a terminating pod
  kubectl rollout status deploy/db-app -n "${app}" --timeout=3m >/dev/null

  # Scenario 1: no Vault token, password or role ID anywhere in the pod spec; no SA token mounted
  spec="$(kubectl get pod -l app.kubernetes.io/name=db-app -n "${app}" -o json \
    | jq '[.items[] | select(.metadata.deletionTimestamp == null)][0]')"
  if jq -e '[.spec | .. | objects | select(has("name") and has("value"))
            | select(.name | test("TOKEN|PASSWORD|SECRET|ROLE_ID"; "i"))] | length > 0' <<<"${spec}" >/dev/null; then
    fail "${app}: pod spec has a secret-looking env var"
  fi
  [ "$(jq -r '.spec.automountServiceAccountToken' <<<"${spec}")" = "false" ] \
    || fail "${app}: default ServiceAccount token is mounted"
  pass "1  pod spec holds no Vault token, password or role ID; SA token not automounted"

  whoami="$(app_get "${app}" /whoami)"
  user="$(jq -r .current_user <<<"${whoami}")"
  [[ "${user}" == v-* ]] || fail "${app}: unexpected current_user '${user}'"
  jq -e 'has("password") | not' <<<"${whoami}" >/dev/null || fail "${app}: /whoami exposes a password"
  pass "1  /whoami -> ${user} (lease ...$(jq -r .lease_id_suffix <<<"${whoami}"), ~$(jq -r .lease_seconds_remaining <<<"${whoami}")s left)"

  # Scenario 2: rows from its own schema only; the production canary never reaches preview
  data="$(app_get "${app}" /data)"
  rows="$(jq '.count' <<<"${data}")"
  [ "${rows}" -gt 0 ] || fail "${app}: /data returned no rows"
  if [ "${env}" != production ] && grep -q CANARY_PROD <<<"${data}"; then
    fail "${app}: production canary visible from ${env}"
  fi
  grep -q "${env}-" <<<"${data}" || fail "${app}: /data rows are not from ${env}"
  pass "2  /data -> ${rows} rows from ${env}.${application}"

  # Scenario 10: own API key, version >= 1, fingerprint only
  apikey="$(app_get "${app}" /apikey)"
  version="$(jq -r .version <<<"${apikey}")"
  [ "${version}" != null ] && [ "${version}" -ge 1 ] || fail "${app}: /apikey has no version"
  jq -e 'has("api_key") | not' <<<"${apikey}" >/dev/null || fail "${app}: /apikey exposes the key"
  pass "10 /apikey -> version ${version}, fingerprint $(jq -r .fingerprint <<<"${apikey}"), source $(jq -r .source <<<"${apikey}")"
done

log "verify:db-app passed"
