#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/runtime.sh
. "${SCRIPT_DIR}/lib/runtime.sh"

environment="${1:-}"
backup_dir="${2:-}"
confirmation="${3:-}"
cross_environment="${4:-}"
validate_environment_name "${environment}"
[ -d "${backup_dir}" ] || die "backup directory not found: ${backup_dir:-unset}"
[ "${confirmation}" = "RESTORE ${environment}" ] \
  || die "destructive operation; confirm with: '$0 ${environment} ${backup_dir} RESTORE ${environment}'"
require_command docker
require_command openssl
require_secret_files "${environment}"
load_runtime_environment "${environment}"

manifest_file="${backup_dir}/manifest.env"
[ -f "${manifest_file}" ] || die "manifest missing: ${manifest_file}"
manifest_value() {
  awk -F= -v key="$1" '$1 == key {sub(/^[^=]*=/, ""); print; exit}' "${manifest_file}"
}
source_environment="$(manifest_value environment)"

if [ "${source_environment}" = "local-demo" ] && [ "${environment}" = "customer-template" ]; then
  die "a local-demo backup can never be restored into customer-template"
fi
if [ "${source_environment}" != "${environment}" ] && [ "${cross_environment}" != "--allow-cross-environment" ]; then
  die "backup belongs to ${source_environment}; add --allow-cross-environment only after a reviewed migration"
fi

if [ "${source_environment}" = "${environment}" ]; then
  "${SCRIPT_DIR}/verify-backup.sh" "${environment}" "${backup_dir}"
else
  die "cross-environment restore requires re-encryption with the target key; use the reviewed migration runbook"
fi

backup_revision="$(manifest_value alembic_revision)"
info "Checking that backup revision ${backup_revision} can upgrade on this release..."
supported_head="$(
  compose "${environment}" run --rm --no-deps -T migrate \
    python -m executive_ai_api.migration_compatibility -- "${backup_revision}"
)"
[ -n "${supported_head}" ] || die "migration compatibility check returned no supported head"

info "Creating a pre-restore safety backup..."
"${SCRIPT_DIR}/backup.sh" "${environment}" pre-restore >/dev/null

database_file="${backup_dir}/$(manifest_value database_file)"
files_file="${backup_dir}/$(manifest_value files_file)"
backup_key="$(runtime_dir_for "${environment}")/secrets/backup_encryption_key"

compose "${environment}" stop api worker

info "Restoring PostgreSQL for ${environment}..."
openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 \
  -pass "file:${backup_key}" -in "${database_file}" \
  | compose "${environment}" exec -T postgres \
      pg_restore --username "${POSTGRES_USER}" --dbname "${POSTGRES_DB}" \
      --clean --if-exists --no-owner --exit-on-error

info "Restoring migrator ownership before applying forward migrations..."
compose "${environment}" up --no-deps --force-recreate \
  --abort-on-container-exit --exit-code-from db-role-init db-role-init

info "Migrating the restored database from ${backup_revision} to ${supported_head}..."
compose "${environment}" up --no-deps --force-recreate \
  --abort-on-container-exit --exit-code-from migrate migrate

info "Replaying least-privilege grants after migrations..."
compose "${environment}" up --no-deps --force-recreate \
  --abort-on-container-exit --exit-code-from db-permissions db-permissions

restored_revision="$(
  compose "${environment}" exec -T postgres \
    psql --username "${POSTGRES_USER}" --dbname "${POSTGRES_DB}" \
      --tuples-only --no-align --command 'SELECT version_num FROM alembic_version LIMIT 1' \
    | tail -n 1
)"
[ "${restored_revision}" = "${supported_head}" ] \
  || die "restored database revision ${restored_revision:-unset} does not match supported head ${supported_head}"

info "Replacing the isolated private-file volume..."
compose "${environment}" --profile tools run --rm file-tool \
  sh -ec 'find /data/files -mindepth 1 -depth -delete'
openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 \
  -pass "file:${backup_key}" -in "${files_file}" \
  | compose "${environment}" --profile tools run --rm -T file-tool \
      tar -C /data/files -xf -

compose "${environment}" up --detach --no-deps api worker nginx
"${SCRIPT_DIR}/smoke-test.sh" "${environment}"

restore_log="$(runtime_dir_for "${environment}")/restore.log"
printf '%s environment=%s source=%s operator=%s\n' \
  "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "${environment}" "${backup_dir}" "$(id -un)" \
  >> "${restore_log}"
chmod 600 "${restore_log}"
info "Restore completed. Safety backup is retained under backups/${environment}."
