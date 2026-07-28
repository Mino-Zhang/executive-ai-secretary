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
source_database_name="$(manifest_value source_database_file)"
target_has_managed_source=false
if compose "${environment}" config --services | grep -qx source-postgres; then
  target_has_managed_source=true
fi

if [ -n "${source_database_name}" ] && [ "${target_has_managed_source}" != true ]; then
  die "backup contains a managed source database, but ${environment} has no source-postgres service"
fi

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
safety_backup="$("${SCRIPT_DIR}/backup.sh" "${environment}" pre-restore)"
[ -d "${safety_backup}" ] \
  || die "pre-restore safety backup did not return a valid directory"

database_file="${backup_dir}/$(manifest_value database_file)"
files_file="${backup_dir}/$(manifest_value files_file)"
source_database_file=""
if [ -n "${source_database_name}" ]; then
  source_database_file="${backup_dir}/${source_database_name}"
elif [ "${target_has_managed_source}" = true ]; then
  info "Legacy backup has no managed source-database artifact; the current source database will be retained."
fi
backup_key="$(runtime_dir_for "${environment}")/secrets/backup_encryption_key"
restore_log="$(runtime_dir_for "${environment}")/restore.log"
source_restore_status=not-managed
if [ -n "${source_database_file}" ]; then
  source_restore_status=restored
elif [ "${target_has_managed_source}" = true ]; then
  source_restore_status=retained-legacy
fi

restore_started=false
restore_completed=false
restore_stage=stop-services
restore_failure_guard() {
  local status="$?"
  trap - EXIT INT TERM
  if [ "${status}" -ne 0 ] && [ "${restore_started}" = true ] \
    && [ "${restore_completed}" != true ]; then
    compose "${environment}" stop \
      api mcp-hub hermes-runtime worker ingestion-worker file-worker scheduler web nginx \
      >/dev/null 2>&1 || true
    info "Restore failed after application services were stopped."
    info "The partially restored environment remains stopped; do not start it until it is reviewed."
    info "Rollback source: ${safety_backup}"
    printf '%s status=failed stage=%s environment=%s source=%s source_database=%s safety_backup=%s operator=%s\n' \
      "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "${restore_stage}" "${environment}" \
      "${backup_dir}" "${source_restore_status}" "${safety_backup}" "$(id -un)" \
      >> "${restore_log}" 2>/dev/null || true
    chmod 600 "${restore_log}" 2>/dev/null || true
  fi
  exit "${status}"
}
trap restore_failure_guard EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

restore_started=true
compose "${environment}" stop \
  api mcp-hub hermes-runtime worker ingestion-worker file-worker scheduler web nginx

restore_stage=product-database
info "Restoring PostgreSQL for ${environment}..."
openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 \
  -pass "file:${backup_key}" -in "${database_file}" \
  | compose "${environment}" exec -T postgres \
      pg_restore --username "${POSTGRES_USER}" --dbname "${POSTGRES_DB}" \
      --clean --if-exists --no-owner --exit-on-error --single-transaction

restore_stage=product-ownership
info "Restoring migrator ownership before applying forward migrations..."
compose "${environment}" up --no-deps --force-recreate \
  --abort-on-container-exit --exit-code-from db-role-init db-role-init

restore_stage=product-migration
info "Migrating the restored database from ${backup_revision} to ${supported_head}..."
compose "${environment}" up --no-deps --force-recreate \
  --abort-on-container-exit --exit-code-from migrate migrate

restore_stage=product-permissions
info "Replaying least-privilege grants after migrations..."
compose "${environment}" up --no-deps --force-recreate \
  --abort-on-container-exit --exit-code-from db-permissions db-permissions

restore_stage=product-revision-check
restored_revision="$(
  compose "${environment}" exec -T postgres \
    psql --username "${POSTGRES_USER}" --dbname "${POSTGRES_DB}" \
      --tuples-only --no-align --command 'SELECT version_num FROM alembic_version LIMIT 1' \
    | tail -n 1
)"
[ "${restored_revision}" = "${supported_head}" ] \
  || die "restored database revision ${restored_revision:-unset} does not match supported head ${supported_head}"

if [ -n "${source_database_file}" ]; then
  restore_stage=source-database
  info "Restoring the managed sanitized source database..."
  openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 \
    -pass "file:${backup_key}" -in "${source_database_file}" \
    | compose "${environment}" exec -T source-postgres /bin/sh -ec '
        export PGPASSWORD="$(cat /run/secrets/source_postgres_password)"
        exec pg_restore --host 127.0.0.1 --username executive_ai_source \
          --dbname "${POSTGRES_DB}" --clean --if-exists --no-owner \
          --exit-on-error --single-transaction
      '

  restore_stage=source-permissions
  info "Replaying managed source contracts and least-privilege grants..."
  compose "${environment}" exec -T source-postgres \
    /docker-entrypoint-initdb.d/01-init-source.sh
fi

restore_stage=private-files
info "Replacing the isolated private-file volume..."
files_staging_name=".restore-staging-$(date -u '+%Y%m%dT%H%M%SZ')-$$"
openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 \
  -pass "file:${backup_key}" -in "${files_file}" \
  | compose "${environment}" --profile tools run --rm -T file-tool \
      sh -ec '
        staging="/data/files/$1"
        mkdir -- "${staging}"
        tar -C "${staging}" -xf -
        find /data/files -mindepth 1 -maxdepth 1 ! -name "$1" \
          -exec rm -rf -- {} +
        find "${staging}" -mindepth 1 -maxdepth 1 \
          -exec mv -- {} /data/files/ \;
        rmdir -- "${staging}"
      ' restore-files "${files_staging_name}"

restore_stage=start-services
compose "${environment}" up --detach --wait --no-deps --no-build \
  api mcp-hub hermes-runtime worker ingestion-worker file-worker scheduler web nginx
restore_stage=smoke-test
"${SCRIPT_DIR}/smoke-test.sh" "${environment}"

restore_stage=write-log
printf '%s status=completed environment=%s source=%s source_database=%s safety_backup=%s operator=%s\n' \
  "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "${environment}" "${backup_dir}" \
  "${source_restore_status}" "${safety_backup}" "$(id -un)" \
  >> "${restore_log}"
chmod 600 "${restore_log}"
restore_completed=true
trap - EXIT INT TERM
info "Restore completed. Safety backup is retained under backups/${environment}."
