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
mcp_response="$(compose "${environment}" exec -T mcp-hub python -c \
  "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8010/health', timeout=3).read().decode())")"
hermes_response="$(compose "${environment}" exec -T hermes-runtime python -c \
  "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8020/health', timeout=3).read().decode())")"

printf 'gateway: %s\n' "${gateway_response}"
printf 'api live: %s\n' "${api_live_response}"
printf 'api ready: %s\n' "${api_ready_response}"
printf 'mcp hub: %s\n' "${mcp_response}"
printf 'hermes: %s\n' "${hermes_response}"

running_services="$(compose "${environment}" ps --services --filter status=running)"
for service in postgres api worker ingestion-worker file-worker scheduler mcp-hub hermes-runtime web nginx; do
  printf '%s\n' "${running_services}" | grep -qx "${service}" \
    || die "required service is not running: ${service}"
done
if compose "${environment}" config --services | grep -qx source-postgres; then
  printf '%s\n' "${running_services}" | grep -qx source-postgres \
    || die "managed sanitized source database is not running"
fi

compose "${environment}" exec -T file-worker /bin/sh -ec \
  'test "$(id -u)" = 999 && test "$(id -g)" = 999 && test -r /opt/models && test ! -w /opt/models && test -s /opt/models/fast-bge-small-zh-v1.5/model_optimized.onnx'
printf 'file worker cache: verified model read-only for uid/gid 999\n'

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
