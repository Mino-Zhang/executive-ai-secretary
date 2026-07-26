#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/runtime.sh
. "${SCRIPT_DIR}/lib/runtime.sh"

environment="${1:-}"
validate_environment_name "${environment}"
require_command docker
load_runtime_environment "${environment}"
compose "${environment}" ps
printf '\nGateway: %s\n' "${PUBLIC_BASE_URL}"
