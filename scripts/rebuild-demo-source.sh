#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/runtime.sh
. "${SCRIPT_DIR}/lib/runtime.sh"

environment="${1:-}"
enterprise_slug="${2:-}"
confirmation="${3:-}"
[ "${environment}" = "local-demo" ] || die "demo source rebuild is permitted only for local-demo"
[ -n "${enterprise_slug}" ] || die "enterprise slug is required"
[ "${confirmation}" = "REBUILD local-demo/${enterprise_slug}" ] \
  || die "confirm explicitly: $0 local-demo ${enterprise_slug} 'REBUILD local-demo/${enterprise_slug}'"
require_secret_files "${environment}"

DEMO_ENTERPRISE_SLUG="${enterprise_slug}" \
  compose "${environment}" --profile demo-seed run --rm seed-source-demo
info "Deterministic demo source rebuilt. Run configure-source.sh once, then sync-now.sh."
