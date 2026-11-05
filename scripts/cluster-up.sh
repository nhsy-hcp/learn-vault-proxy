#!/bin/bash
# Create (or start) the k3d cluster and switch kubectl context to it. Safe to re-run.
#
# Usage: scripts/cluster-up.sh <cluster-name> <k3s-image>
set -euo pipefail

CLUSTER_NAME="${1:?Usage: cluster-up.sh <cluster-name> <k3s-image>}"
K3S_IMAGE="${2:?Usage: cluster-up.sh <cluster-name> <k3s-image>}"

cluster_exists() {
  k3d cluster list --no-headers 2>/dev/null | awk '{print $1}' | grep -qx "${CLUSTER_NAME}"
}

if cluster_exists; then
  echo "Cluster '${CLUSTER_NAME}' already exists - starting if stopped."
  k3d cluster start "${CLUSTER_NAME}" 2>/dev/null || true
else
  echo "Creating cluster '${CLUSTER_NAME}' (${K3S_IMAGE})..."
  # --wait=false: Podman's journald log driver causes log-read failures during wait;
  # cluster-ready.sh is the readiness gate instead.
  # Vault is exposed on localhost:8200 via NodePort 30820.
  # Unlike the reference repo, anonymous-auth stays off: Kubernetes auth uses TokenReview.
  # Podman occasionally fails k3d's log read during create; k3d rolls back, so retry once.
  for attempt in 1 2; do
    if k3d cluster create "${CLUSTER_NAME}" \
      --image "${K3S_IMAGE}" \
      --wait=false \
      --port 8200:30820@server:0 \
      --k3s-arg "--disable=traefik,servicelb@server:0"; then
      break
    fi
    [ "${attempt}" -eq 2 ] && { echo "ERROR: k3d cluster create failed twice" >&2; exit 1; }
    echo "k3d cluster create failed - retrying in 5s..."
    sleep 5
  done
fi

k3d kubeconfig merge "${CLUSTER_NAME}" --kubeconfig-merge-default --kubeconfig-switch-context
echo "cluster-up complete."
