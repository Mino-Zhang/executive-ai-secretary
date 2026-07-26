#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/runtime.sh
. "${SCRIPT_DIR}/lib/runtime.sh"

environment="${1:-}"
validate_environment_name "${environment}"
require_command openssl
load_runtime_environment "${environment}"

secrets_dir="$(runtime_dir_for "${environment}")/secrets"
umask 077
mkdir -p "${secrets_dir}"
chmod 700 "${secrets_dir}"

added=0
for name in postgres_migrator_password postgres_runtime_password postgres_backup_password; do
  target="${secrets_dir}/${name}"
  if [ ! -e "${target}" ]; then
    openssl rand -hex 32 > "${target}"
    chmod 600 "${target}"
    info "Added missing ${name} for ${environment}."
    added=1
  else
    [ -s "${target}" ] || die "refusing to overwrite empty existing secret: ${target}"
    [ "$(stat -f '%Lp' "${target}" 2>/dev/null || stat -c '%a' "${target}")" = "600" ] \
      || chmod 600 "${target}"
  fi
done

for name in file_encryption_key_ring audit_hmac_key_ring; do
  target="${secrets_dir}/${name}"
  if [ ! -e "${target}" ]; then
    printf '{}\n' > "${target}"
    chmod 600 "${target}"
    info "Added missing empty ${name} for ${environment}."
    added=1
  else
    [ -s "${target}" ] || die "refusing to overwrite empty existing secret: ${target}"
    [ "$(stat -f '%Lp' "${target}" 2>/dev/null || stat -c '%a' "${target}")" = "600" ] \
      || chmod 600 "${target}"
  fi
done

if [ "${added}" -eq 0 ]; then
  info "Additive role and key-ring secrets already exist for ${environment}; nothing changed."
else
  info "Only missing role or key-ring secrets were added; no existing secret was rotated."
fi
