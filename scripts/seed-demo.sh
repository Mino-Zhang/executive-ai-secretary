#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/runtime.sh
. "${SCRIPT_DIR}/lib/runtime.sh"

environment="${1:-}"
enterprise_slug="${2:-}"
confirmation="${3:-}"
[ "${environment}" = "local-demo" ] || die "demo seed is permitted only for local-demo"
[ -n "${enterprise_slug}" ] || die "an existing enterprise slug is required"
printf '%s' "${enterprise_slug}" | grep -Eq '^[a-z0-9]+(-[a-z0-9]+)*$' \
  || die "enterprise slug is invalid"
[ "${confirmation}" = "SEED local-demo/${enterprise_slug}" ] \
  || die "confirm explicitly: $0 local-demo ${enterprise_slug} 'SEED local-demo/${enterprise_slug}'"
require_secret_files "${environment}"

DEMO_ENTERPRISE_SLUG="${enterprise_slug}" \
  compose "${environment}" --profile demo-seed run --rm seed-demo
info "Sanitized demo fixtures seeded. This command does not create a default password."
