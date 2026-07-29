#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/runtime.sh
. "${SCRIPT_DIR}/lib/runtime.sh"

environment="${1:-}"
validate_environment_name "${environment}"
load_runtime_environment "${environment}"
require_command curl
require_command docker

gateway_response="$(curl --fail --silent --show-error "${PUBLIC_BASE_URL}/_gateway/health")"
api_live_response="$(curl --fail --silent --show-error "${PUBLIC_BASE_URL}/health/live")"
api_ready_response="$(curl --fail --silent --show-error "${PUBLIC_BASE_URL}/health/ready")"

printf 'gateway: %s\n' "${gateway_response}"
printf 'api live: %s\n' "${api_live_response}"
printf 'api ready: %s\n' "${api_ready_response}"

published_endpoint="$(compose "${environment}" port nginx 8080)"
printf 'gateway listener: %s\n' "${published_endpoint}"
[ "${published_endpoint}" = "127.0.0.1:${HTTP_PORT}" ] \
  || die "gateway is not confirmed loopback-only"

# Keep the host process view as optional diagnostics. Linux engines can publish
# ports directly through nftables/iptables without a docker-proxy process, so
# an empty lsof result is not evidence that the declared listener is absent.
if command -v lsof >/dev/null 2>&1; then
  listener="$(lsof -nP -iTCP:"${HTTP_PORT}" -sTCP:LISTEN 2>/dev/null || true)"
  printf '%s\n' "${listener}"
fi

info "Smoke test passed for ${environment}."
