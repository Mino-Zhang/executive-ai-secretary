#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/runtime.sh
. "${SCRIPT_DIR}/lib/runtime.sh"

environment="${1:-}"
service="${2:-}"
validate_environment_name "${environment}"
if [ -n "${service}" ]; then
  compose "${environment}" logs --follow --tail 200 "${service}"
else
  compose "${environment}" logs --follow --tail 200
fi
