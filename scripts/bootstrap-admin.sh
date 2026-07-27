#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/runtime.sh
. "${SCRIPT_DIR}/lib/runtime.sh"

environment="${1:-}"
email="${2:-}"
display_name="${3:-}"
enterprise_name="${4:-}"
enterprise_slug="${5:-}"
validate_environment_name "${environment}"
if [ -z "${email}" ] || [ -z "${display_name}" ] \
  || [ -z "${enterprise_name}" ] || [ -z "${enterprise_slug}" ]; then
  die "usage: $0 <environment> <email> <display-name> <enterprise-name> <enterprise-slug>"
fi
case "${email}" in
  *@*.*) ;;
  *) die "administrator email is not valid" ;;
esac
printf '%s' "${enterprise_slug}" | grep -Eq '^[a-z0-9]+(-[a-z0-9]+)*$' \
  || die "enterprise slug must use lowercase letters, numbers and single hyphens"
require_secret_files "${environment}"

printf 'Enter a one-time administrator password (input is hidden): ' >&2
IFS= read -r -s password
printf '\n' >&2
[ "${#password}" -ge 14 ] || die "password must be at least 14 characters"

printf '%s\n' "${password}" | \
  BOOTSTRAP_ADMIN_EMAIL="${email}" \
  BOOTSTRAP_ADMIN_DISPLAY_NAME="${display_name}" \
  BOOTSTRAP_ENTERPRISE_NAME="${enterprise_name}" \
  BOOTSTRAP_ENTERPRISE_SLUG="${enterprise_slug}" \
  compose "${environment}" --profile bootstrap run --rm -T bootstrap-admin
unset password
info "Administrator created. The account must change its password at first login."
