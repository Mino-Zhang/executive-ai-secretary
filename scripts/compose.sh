#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/runtime.sh
. "${SCRIPT_DIR}/lib/runtime.sh"

environment="${1:-}"
[ "$#" -ge 2 ] || die "usage: $0 <local-demo|customer-template> <docker compose arguments...>"
shift
validate_environment_name "${environment}"
require_command docker
require_secret_files "${environment}"
compose "${environment}" "$@"
