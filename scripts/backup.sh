#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/runtime.sh
. "${SCRIPT_DIR}/lib/runtime.sh"

environment="${1:-}"
label="${2:-manual}"
validate_environment_name "${environment}"
case "${label}" in
  *[!A-Za-z0-9._-]*) die "backup label may contain only letters, numbers, dot, underscore and dash" ;;
esac
require_command docker
require_command openssl
require_secret_files "${environment}"
load_runtime_environment "${environment}"

compose "${environment}" ps --services --filter status=running | grep -qx postgres \
  || die "postgres is not running for ${environment}"

running_services="$(compose "${environment}" ps --services --filter status=running)"
mutation_services=(api worker ingestion-worker file-worker scheduler)
running_mutation_services=()
for service in "${mutation_services[@]}"; do
  if printf '%s\n' "${running_services}" | grep -qx "${service}"; then
    running_mutation_services+=("${service}")
  fi
done
services_quiesced=false

resume_application() {
  if [ "${services_quiesced}" = true ]; then
    if [ "${#running_mutation_services[@]}" -gt 0 ]; then
      compose "${environment}" up --detach --no-deps --no-build \
        "${running_mutation_services[@]}" >/dev/null
    fi
    services_quiesced=false
  fi
}
trap resume_application EXIT INT TERM

if [ "${#running_mutation_services[@]}" -gt 0 ]; then
  info "Quiescing API, job workers and scheduler so database and private files share one consistency point..."
  compose "${environment}" stop "${running_mutation_services[@]}" >/dev/null
  services_quiesced=true
fi

timestamp="$(date -u '+%Y%m%dT%H%M%SZ')"
backup_dir="${REPO_ROOT}/backups/${environment}/${timestamp}-${label}"
database_file="${backup_dir}/database.dump.enc"
source_database_file="${backup_dir}/source-database.dump.enc"
files_file="${backup_dir}/files.tar.enc"
manifest_file="${backup_dir}/manifest.env"
signature_file="${backup_dir}/manifest.sig"
backup_key="$(runtime_dir_for "${environment}")/secrets/backup_encryption_key"
backup_db_user="${POSTGRES_BACKUP_USER:-executive_ai_backup}"

umask 077
mkdir -p "${backup_dir}"
chmod 700 "${backup_dir}"

info "Creating encrypted database backup for ${environment}..."
compose "${environment}" --profile tools run --rm -T db-backup-tool \
  pg_dump --host postgres --username "${backup_db_user}" --dbname "${POSTGRES_DB}" \
    --format=custom --no-owner --no-acl \
  | openssl enc -aes-256-cbc -salt -pbkdf2 -iter 200000 \
      -pass "file:${backup_key}" -out "${database_file}"

source_database_sha256=""
if printf '%s\n' "${running_services}" | grep -qx source-postgres; then
  info "Creating encrypted source-database backup for ${environment}..."
  compose "${environment}" exec -T source-postgres /bin/sh -ec '
    export PGPASSWORD="$(cat /run/secrets/source_postgres_password)"
    exec pg_dump --host 127.0.0.1 --username executive_ai_source \
      --dbname "${POSTGRES_DB}" --format=custom --no-owner --no-acl
  ' | openssl enc -aes-256-cbc -salt -pbkdf2 -iter 200000 \
      -pass "file:${backup_key}" -out "${source_database_file}"
  source_database_sha256="$(sha256_file "${source_database_file}")"
fi

info "Creating encrypted private-file backup for ${environment}..."
compose "${environment}" --profile tools run --rm -T file-tool \
  tar -C /data/files -cf - . \
  | openssl enc -aes-256-cbc -salt -pbkdf2 -iter 200000 \
      -pass "file:${backup_key}" -out "${files_file}"

database_sha256="$(sha256_file "${database_file}")"
files_sha256="$(sha256_file "${files_file}")"
git_revision="$(git -C "${REPO_ROOT}" rev-parse --verify HEAD 2>/dev/null || printf unknown)"
alembic_revision="$(compose "${environment}" --profile tools run --rm -T db-backup-tool \
  psql --host postgres --username "${backup_db_user}" --dbname "${POSTGRES_DB}" --tuples-only --no-align \
  --command 'SELECT version_num FROM alembic_version LIMIT 1' 2>/dev/null | tail -n 1 || true)"
[ -n "${alembic_revision}" ] || alembic_revision=unknown
enterprise_slugs="$(compose "${environment}" --profile tools run --rm -T db-backup-tool \
  psql --host postgres --username "${backup_db_user}" --dbname "${POSTGRES_DB}" --tuples-only --no-align \
  --command "SELECT COALESCE(string_agg(slug, ',' ORDER BY slug), '') FROM enterprises" \
  2>/dev/null | tail -n 1 || true)"
enterprise_count="$(compose "${environment}" --profile tools run --rm -T db-backup-tool \
  psql --host postgres --username "${backup_db_user}" --dbname "${POSTGRES_DB}" --tuples-only --no-align \
  --command 'SELECT count(*) FROM enterprises' 2>/dev/null | tail -n 1 || true)"
[ -n "${enterprise_count}" ] || enterprise_count=unknown

{
  printf 'format_version=1\n'
  printf 'environment=%s\n' "${environment}"
  printf 'app_mode=%s\n' "${APP_MODE}"
  printf 'compose_project=%s\n' "${COMPOSE_PROJECT_NAME}"
  printf 'postgres_database=%s\n' "${POSTGRES_DB}"
  printf 'created_at_utc=%s\n' "${timestamp}"
  printf 'git_revision=%s\n' "${git_revision}"
  printf 'alembic_revision=%s\n' "${alembic_revision}"
  printf 'enterprise_count=%s\n' "${enterprise_count}"
  printf 'enterprise_slugs=%s\n' "${enterprise_slugs}"
  printf 'consistency=application-quiesced\n'
  printf 'database_file=database.dump.enc\n'
  printf 'database_sha256=%s\n' "${database_sha256}"
  if [ -n "${source_database_sha256}" ]; then
    printf 'source_database_file=source-database.dump.enc\n'
    printf 'source_database_sha256=%s\n' "${source_database_sha256}"
  fi
  printf 'files_file=files.tar.enc\n'
  printf 'files_sha256=%s\n' "${files_sha256}"
  printf 'encryption=aes-256-cbc-pbkdf2-iter200000\n'
} > "${manifest_file}"
openssl pkeyutl -sign -rawin \
  -inkey "$(runtime_dir_for "${environment}")/secrets/backup_signing_key" \
  -in "${manifest_file}" -out "${signature_file}"
chmod 600 "${manifest_file}" "${signature_file}" "${database_file}" "${files_file}"
if [ -s "${source_database_file}" ]; then
  chmod 600 "${source_database_file}"
fi

"${SCRIPT_DIR}/verify-backup.sh" "${environment}" "${backup_dir}"
resume_application
trap - EXIT INT TERM
info "Backup completed and verified: ${backup_dir}"
printf '%s\n' "${backup_dir}"
