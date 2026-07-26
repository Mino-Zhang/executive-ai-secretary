#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/runtime.sh
. "${SCRIPT_DIR}/lib/runtime.sh"

environment="${1:-}"
validate_environment_name "${environment}"
load_runtime_environment "${environment}"
require_command curl

gateway_response="$(curl --fail --silent --show-error "${PUBLIC_BASE_URL}/_gateway/health")"
api_live_response="$(curl --fail --silent --show-error "${PUBLIC_BASE_URL}/health/live")"
api_ready_response="$(curl --fail --silent --show-error "${PUBLIC_BASE_URL}/health/ready")"

printf 'gateway: %s\n' "${gateway_response}"
printf 'api live: %s\n' "${api_live_response}"
printf 'api ready: %s\n' "${api_ready_response}"

if command -v lsof >/dev/null 2>&1; then
  listener="$(lsof -nP -iTCP:"${HTTP_PORT}" -sTCP:LISTEN 2>/dev/null || true)"
  printf '%s\n' "${listener}"
  printf '%s\n' "${listener}" | grep -q '127.0.0.1:' || die "gateway is not confirmed loopback-only"
fi

info "Smoke test passed for ${environment}."
