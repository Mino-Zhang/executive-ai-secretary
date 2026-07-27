#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/runtime.sh
. "${SCRIPT_DIR}/lib/runtime.sh"

environment="${1:-}"
enterprise_slug="${2:-}"
email="${3:-}"
display_name="${4:-}"
scope="${5:-}"
validate_environment_name "${environment}"
if [ -z "${enterprise_slug}" ] || [ -z "${email}" ] \
  || [ -z "${display_name}" ] || [ -z "${scope}" ]; then
  die "usage: $0 <environment> <enterprise-slug> <email> <display-name> <enterprise|unit-code[,unit-code]>"
fi
printf '%s' "${enterprise_slug}" | grep -Eq '^[a-z0-9]+(-[a-z0-9]+)*$' \
  || die "enterprise slug is invalid"
case "${email}" in
  *@*.*) ;;
  *) die "executive email is not valid" ;;
esac

if [ "${scope}" = "enterprise" ]; then
  scope_mode=enterprise
  organization_unit_codes=""
else
  printf '%s' "${scope}" | grep -Eq '^[a-z0-9]+(-[a-z0-9]+)*(,[a-z0-9]+(-[a-z0-9]+)*)*$' \
    || die "organization-unit codes must be comma-separated lowercase slugs"
  scope_mode=organization-units
  organization_unit_codes="${scope}"
fi
require_secret_files "${environment}"

printf 'Enter a one-time executive password (input is hidden): ' >&2
IFS= read -r -s password
printf '\n' >&2
[ "${#password}" -ge 14 ] || die "password must be at least 14 characters"

printf '%s\n' "${password}" | \
  EXECUTIVE_EMAIL="${email}" \
  EXECUTIVE_DISPLAY_NAME="${display_name}" \
  EXECUTIVE_ENTERPRISE_SLUG="${enterprise_slug}" \
  EXECUTIVE_SCOPE_MODE="${scope_mode}" \
  EXECUTIVE_ORGANIZATION_UNIT_CODES="${organization_unit_codes}" \
  compose "${environment}" --profile bootstrap run --rm -T create-executive
unset password
info "Executive created with ${scope_mode} scope and mandatory first-login password change."
