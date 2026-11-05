#!/bin/bash
# Deploy PostgreSQL 16 into namespace postgres: random credentials Secret (created once),
# bootstrap ConfigMap, PVC, Service and Deployment. Safe to re-run.
#
# Usage: scripts/install-postgres.sh
set -euo pipefail
# shellcheck source=scripts/lib.sh
source "$(dirname "$0")/lib.sh"

NS="postgres"
DIR="vault-ce/postgres"

kubectl create namespace "${NS}" --dry-run=client -o yaml | kubectl apply -f - >/dev/null

# Generated once and never printed. The vault_admin_* passwords are rotated by Vault (rotate-root) later.
if kubectl get secret postgres-credentials -n "${NS}" >/dev/null 2>&1; then
  log "Secret postgres-credentials already exists"
else
  kubectl create secret generic postgres-credentials -n "${NS}" \
    --from-literal=postgres-password="$(openssl rand -hex 24)" \
    --from-literal=vault-admin-preview-password="$(openssl rand -hex 24)" \
    --from-literal=vault-admin-production-password="$(openssl rand -hex 24)" >/dev/null
  log "Created Secret postgres-credentials (random passwords)"
fi

kubectl create configmap postgres-bootstrap -n "${NS}" \
  --from-file="${DIR}/00-bootstrap.sh" \
  --from-file="${DIR}/postgres-bootstrap.sql" \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null

kubectl apply -f "${DIR}/postgres.yaml"
kubectl rollout status deploy/postgres -n "${NS}" --timeout=5m

log "Databases:"
kubectl exec deploy/postgres -n "${NS}" -- psql -U postgres -Atc \
  "SELECT datname FROM pg_database WHERE datname IN ('preview','production') ORDER BY 1"
