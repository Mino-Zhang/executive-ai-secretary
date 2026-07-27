#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/runtime.sh
. "${SCRIPT_DIR}/lib/runtime.sh"

environment="${1:-}"
enterprise_slug="${2:-}"
display_name="${3:-}"
validate_environment_name "${environment}"
[ -n "${enterprise_slug}" ] || die "enterprise slug is required"
[ -n "${display_name}" ] || die "data-source display name is required"
printf '%s' "${enterprise_slug}" | grep -Eq '^[a-z0-9]+(-[a-z0-9]+)*$' \
  || die "enterprise slug is invalid"
require_secret_files "${environment}"

SOURCE_ENTERPRISE_SLUG="${enterprise_slug}" \
  SOURCE_DISPLAY_NAME="${display_name}" \
  compose "${environment}" --profile source-admin run --rm configure-data-source
info "Sanitized source validated and registered. The scheduler may now create its daily sync task."
