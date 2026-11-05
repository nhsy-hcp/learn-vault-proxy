#!/bin/bash
# Run one vault CLI command inside vault-0 with the root token from vault-init.json.
# The token travels on stdin, never as an argument.
#
# Usage: scripts/vault.sh <vault args...>    e.g. scripts/vault.sh lease revoke -prefix db/creds/
set -euo pipefail
# shellcheck source=scripts/lib.sh
source "$(dirname "$0")/lib.sh"

VAULT_TOKEN="$(root_token)"
export VAULT_TOKEN
vault_cmd "$@"
