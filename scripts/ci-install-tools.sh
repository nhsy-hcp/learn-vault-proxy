#!/bin/bash
# Install the lint and build tools the Taskfile needs on an Ubuntu CI runner (GitHub Actions or act).
# Local machines use Homebrew instead (see task deps).
#
# Usage: scripts/ci-install-tools.sh [--e2e]
set -euo pipefail

GITLEAKS_VERSION="8.30.1"
HADOLINT_VERSION="v2.14.0"
K3D_VERSION="v5.9.0"
BIN_DIR="/usr/local/bin"
SUDO=""
[ "$(id -u)" -eq 0 ] || SUDO="sudo"

# Release asset names per CPU (GitHub runners are x86_64; act on Apple silicon runs aarch64)
case "$(uname -m)" in
  x86_64) GITLEAKS_ARCH="x64"; HADOLINT_ARCH="x86_64"; K3D_ARCH="amd64" ;;
  aarch64 | arm64) GITLEAKS_ARCH="arm64"; HADOLINT_ARCH="arm64"; K3D_ARCH="arm64" ;;
  *) echo "ERROR: unsupported architecture $(uname -m)" >&2; exit 1 ;;
esac

echo "==> apt: shellcheck, jq, gettext-base (envsubst)"
${SUDO} apt-get update -qq
${SUDO} apt-get install -y -qq shellcheck jq gettext-base >/dev/null

echo "==> pipx: pre-commit, yamllint, ruff, uv"
# Ubuntu 24.04 marks the system Python as externally managed, so use pipx (preinstalled on runners)
command -v pipx >/dev/null 2>&1 || ${SUDO} apt-get install -y -qq pipx >/dev/null
for pkg in pre-commit yamllint ruff uv; do
  pipx install --quiet "${pkg}"
done
echo "${HOME}/.local/bin" >> "${GITHUB_PATH:-/dev/null}"
export PATH="${HOME}/.local/bin:${PATH}"

echo "==> gitleaks ${GITLEAKS_VERSION}"
curl -sSfLo /tmp/gitleaks.tar.gz \
  "https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_linux_${GITLEAKS_ARCH}.tar.gz"
tar -xzf /tmp/gitleaks.tar.gz -C /tmp gitleaks
${SUDO} install /tmp/gitleaks "${BIN_DIR}/gitleaks"

echo "==> hadolint ${HADOLINT_VERSION}"
curl -sSfLo /tmp/hadolint \
  "https://github.com/hadolint/hadolint/releases/download/${HADOLINT_VERSION}/hadolint-Linux-${HADOLINT_ARCH}"
${SUDO} install /tmp/hadolint "${BIN_DIR}/hadolint"

if [ "${1:-}" = "--e2e" ]; then
  echo "==> k3d ${K3D_VERSION}"
  # Pinned release binary rather than piping the unpinned install script into bash
  curl -sSfLo /tmp/k3d "https://github.com/k3d-io/k3d/releases/download/${K3D_VERSION}/k3d-linux-${K3D_ARCH}"
  ${SUDO} install /tmp/k3d "${BIN_DIR}/k3d"
  k3d version
  echo "==> helm repo"
  helm repo add hashicorp https://helm.releases.hashicorp.com
  helm repo update hashicorp
fi

echo "==> tools installed"
