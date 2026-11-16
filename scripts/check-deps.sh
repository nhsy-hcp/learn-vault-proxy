#!/bin/bash
# Check that every CLI tool the lab needs is installed. Safe to re-run.
# Lint tools are optional unless --lint is given (task lint passes it).
#
# Usage: scripts/check-deps.sh [--lint]
set -euo pipefail

lint_required=false
case "${1:-}" in
  "") ;;
  --lint) lint_required=true ;;
  *) echo "Usage: $0 [--lint]" >&2; exit 2 ;;
esac

REQUIRED=(kubectl helm k3d jq task uv envsubst openssl)
LINT=(pre-commit shellcheck gitleaks yamllint hadolint ruff)
missing=()
optional_missing=()

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

for bin in "${LINT[@]}"; do
  if command -v "${bin}" >/dev/null 2>&1; then
    printf '  ok       %s\n' "${bin}"
  elif [ "${lint_required}" = true ]; then
    printf '  MISSING  %s\n' "${bin}"
    missing+=("${bin}")
  else
    printf '  optional  %s\n' "${bin}"
    optional_missing+=("${bin}")
  fi
done

if [ "${#missing[@]}" -gt 0 ]; then
  echo "ERROR: missing tools: ${missing[*]}" >&2
  echo "Install with Homebrew, e.g. brew install ${missing[*]}" >&2
  exit 1
fi

if [ "${#optional_missing[@]}" -gt 0 ]; then
  echo "Note: ${optional_missing[*]} needed only for \`task lint\`."
fi

echo "All required dependencies present ($(uname -s) $(uname -m))."
