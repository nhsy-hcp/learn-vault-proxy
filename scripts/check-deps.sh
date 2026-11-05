#!/bin/bash
# Check that every CLI tool the lab needs is installed. Safe to re-run.
#
# Usage: scripts/check-deps.sh
set -euo pipefail

REQUIRED=(kubectl helm k3d jq task pre-commit shellcheck gitleaks yamllint hadolint ruff uv envsubst openssl)
missing=()

for bin in "${REQUIRED[@]}"; do
  if command -v "${bin}" >/dev/null 2>&1; then
    printf '  ok       %s\n' "${bin}"
  else
    printf '  MISSING  %s\n' "${bin}"
    missing+=("${bin}")
  fi
done

if command -v docker >/dev/null 2>&1 || command -v podman >/dev/null 2>&1; then
  printf '  ok       %s\n' "docker/podman"
else
  printf '  MISSING  %s\n' "docker or podman"
  missing+=("docker/podman")
fi

if [ "${#missing[@]}" -gt 0 ]; then
  echo "ERROR: missing tools: ${missing[*]}" >&2
  echo "Install with Homebrew, e.g. brew install ${missing[*]}" >&2
  exit 1
fi

echo "All dependencies present ($(uname -s) $(uname -m))."
