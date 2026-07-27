#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/runtime.sh
. "${SCRIPT_DIR}/lib/runtime.sh"

environment="${1:-}"
enterprise_slug="${2:-}"
source_key="${3:-}"
validate_environment_name "${environment}"
[ -n "${enterprise_slug}" ] || die "enterprise slug is required"
require_secret_files "${environment}"

SOURCE_ENTERPRISE_SLUG="${enterprise_slug}" SOURCE_KEY="${source_key}" \
  compose "${environment}" --profile source-admin run --rm run-data-sync
info "Immediate data synchronization was queued. Use status.sh and the data sync API to follow it."
