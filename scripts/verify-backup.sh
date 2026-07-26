#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/runtime.sh
. "${SCRIPT_DIR}/lib/runtime.sh"

environment="${1:-}"
backup_dir="${2:-}"
validate_environment_name "${environment}"
[ -d "${backup_dir}" ] || die "backup directory not found: ${backup_dir:-unset}"
require_command openssl
require_secret_files "${environment}"
load_runtime_environment "${environment}"

manifest_file="${backup_dir}/manifest.env"
[ -f "${manifest_file}" ] || die "manifest missing: ${manifest_file}"
signature_file="${backup_dir}/manifest.sig"
[ -s "${signature_file}" ] || die "manifest signature missing: ${signature_file}"
openssl pkeyutl -verify -pubin -rawin \
  -inkey "$(runtime_dir_for "${environment}")/secrets/backup_signing_public_key" \
  -in "${manifest_file}" -sigfile "${signature_file}" >/dev/null \
  || die "manifest signature verification failed"

manifest_value() {
  awk -F= -v key="$1" '$1 == key {sub(/^[^=]*=/, ""); print; exit}' "${manifest_file}"
}

manifest_environment="$(manifest_value environment)"
[ "${manifest_environment}" = "${environment}" ] \
  || die "backup belongs to ${manifest_environment}, not ${environment}"
[ "$(manifest_value format_version)" = "1" ] || die "unsupported backup format"
[ "$(manifest_value consistency)" = "application-quiesced" ] \
  || die "backup was not captured from a quiesced application"
[ -n "$(manifest_value alembic_revision)" ] \
  && [ "$(manifest_value alembic_revision)" != "unknown" ] \
  || die "backup does not identify its Alembic revision"
printf '%s' "$(manifest_value enterprise_count)" | grep -Eq '^[0-9]+$' \
  || die "backup does not identify its enterprise count"

database_file="${backup_dir}/$(manifest_value database_file)"
files_file="${backup_dir}/$(manifest_value files_file)"
[ -s "${database_file}" ] || die "encrypted database artifact is missing"
[ -s "${files_file}" ] || die "encrypted files artifact is missing"
[ "$(sha256_file "${database_file}")" = "$(manifest_value database_sha256)" ] \
  || die "database backup checksum mismatch"
[ "$(sha256_file "${files_file}")" = "$(manifest_value files_sha256)" ] \
  || die "files backup checksum mismatch"

backup_key="$(runtime_dir_for "${environment}")/secrets/backup_encryption_key"
openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 \
  -pass "file:${backup_key}" -in "${database_file}" \
  | compose "${environment}" exec -T postgres pg_restore --list >/dev/null

openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 \
  -pass "file:${backup_key}" -in "${files_file}" \
  | tar -tf - >/dev/null

info "Backup verification passed: ${backup_dir}"
