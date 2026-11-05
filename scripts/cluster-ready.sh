#!/bin/bash
# Wait for the k3d node and core add-ons to be Ready. Safe to re-run.
#
# Usage: scripts/cluster-ready.sh <cluster-name>
set -euo pipefail

CLUSTER_NAME="${1:?Usage: cluster-ready.sh <cluster-name>}"
ATTEMPTS=30
DELAY=5

echo "Waiting for k3d cluster '${CLUSTER_NAME}' to be reachable..."
node_ready=false
for i in $(seq 1 "${ATTEMPTS}"); do
  if k3d cluster list --no-headers 2>/dev/null | awk '{print $1}' | grep -qx "${CLUSTER_NAME}"; then
    echo "Cluster found. Waiting for node to be Ready..."
    if kubectl wait node --all --for=condition=Ready --timeout=90s 2>/dev/null; then
      echo "Node is Ready."
      node_ready=true
      break
    fi
  fi
  echo "  Attempt ${i}/${ATTEMPTS} - retrying in ${DELAY}s..."
  sleep "${DELAY}"
done

if [ "${node_ready}" != "true" ]; then
  echo "ERROR: Cluster '${CLUSTER_NAME}' did not become Ready in time." >&2
  exit 1
fi

# Wait for core k3s add-ons (deployments may not exist yet right after start)
for deploy in coredns local-path-provisioner; do
  echo "Waiting for kube-system/${deploy} to be available..."
  ready=false
  for i in $(seq 1 "${ATTEMPTS}"); do
    if kubectl -n kube-system rollout status "deploy/${deploy}" --timeout=60s 2>/dev/null; then
      ready=true
      break
    fi
    echo "  Attempt ${i}/${ATTEMPTS} - retrying in ${DELAY}s..."
    sleep "${DELAY}"
  done
  if [ "${ready}" != "true" ]; then
    echo "ERROR: kube-system/${deploy} did not become available in time." >&2
    exit 1
  fi
done

echo "Cluster '${CLUSTER_NAME}' is Ready."
