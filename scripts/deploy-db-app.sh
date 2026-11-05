#!/bin/bash
# Render and apply one namespace, annotated ServiceAccount, app-team RBAC, Proxy ConfigMap and
# Deployment/Service per entry in vault-ce/apps.json, then wait for the rollouts. Safe to re-run.
#
# Usage: scripts/deploy-db-app.sh <app-image> <proxy-image> <kv-refresh-seconds>
set -euo pipefail
# shellcheck source=scripts/lib.sh
source "$(dirname "$0")/lib.sh"

APP_IMAGE="${1:?Usage: deploy-db-app.sh <app-image> <proxy-image> <kv-refresh-seconds>}"
PROXY_IMAGE="${2:?Usage: deploy-db-app.sh <app-image> <proxy-image> <kv-refresh-seconds>}"
KV_REFRESH_SECONDS="${3:-30}"
OUT_DIR=".tmp/rendered"
TEMPLATES=(namespace service-account app-team-rbac proxy-config deployment)
# Only these variables are substituted; anything else in the templates is left untouched
# shellcheck disable=SC2016 # literal ${VAR} names for envsubst, not expansions
SUBST_VARS='${APP_NAME} ${APPLICATION} ${ENVIRONMENT} ${TEAM} ${APP_IMAGE} ${PROXY_IMAGE} ${KV_REFRESH_SECONDS}'

export APP_IMAGE PROXY_IMAGE KV_REFRESH_SECONDS

while read -r row <&3; do
  APP_NAME="$(jq -r .name <<<"${row}")"
  APPLICATION="$(jq -r .application_name <<<"${row}")"
  ENVIRONMENT="$(jq -r .environment <<<"${row}")"
  TEAM="$(jq -r .team <<<"${row}")"
  export APP_NAME APPLICATION ENVIRONMENT TEAM

  existed=false
  kubectl get deploy/db-app -n "${APP_NAME}" >/dev/null 2>&1 && existed=true

  mkdir -p "${OUT_DIR}/${APP_NAME}"
  for t in "${TEMPLATES[@]}"; do
    envsubst "${SUBST_VARS}" < "templates/${t}.yaml.tpl" > "${OUT_DIR}/${APP_NAME}/${t}.yaml"
  done
  # Namespace first so the namespaced objects can land
  kubectl apply -f "${OUT_DIR}/${APP_NAME}/namespace.yaml" >/dev/null
  for t in "${TEMPLATES[@]:1}"; do
    kubectl apply -f "${OUT_DIR}/${APP_NAME}/${t}.yaml" >/dev/null
  done
  # Re-deploys pick up a rebuilt image (same tag) or changed Proxy config. A first deploy needs no
  # restart: restarting it would start a second ReplicaSet while the first is still coming up.
  if [ "${existed}" = true ]; then
    kubectl rollout restart deploy/db-app -n "${APP_NAME}" >/dev/null
  fi
  log "Applied ${APP_NAME} (${APPLICATION} / ${ENVIRONMENT} / ${TEAM})"
done 3< <(jq -c '.[]' "${APPS_FILE}")

for app in $(app_names); do
  kubectl rollout status deploy/db-app -n "${app}" --timeout=3m
done
