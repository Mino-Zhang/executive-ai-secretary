#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/runtime.sh
. "${SCRIPT_DIR}/lib/runtime.sh"

environment="${1:-}"
mode_or_backup="${2:-}"

[ "${environment}" = "local-demo" ] || die "this cutover reset is local-demo only"
require_command docker
load_runtime_environment "${environment}"

compose "${environment}" ps --services --filter status=running | grep -qx api \
  || die "api is not running for ${environment}"

run_reset_cli() {
  compose "${environment}" exec -T api sh -ec '
    DB_PASSWORD="$(cat /run/secrets/postgres_api_password)"
    export DATABASE_URL="postgresql+psycopg://${POSTGRES_API_USER}:${DB_PASSWORD}@postgres:5432/${POSTGRES_DB}"
    exec python -m executive_ai_api.cli reset-local-demo-operating-data-v3 "$@"
  ' reset-local-demo-operating-data-v3 "$@"
}

if [ "${mode_or_backup}" = "--dry-run" ]; then
  enterprise_slug="${3:-}"
  [ -n "${enterprise_slug}" ] || die "enterprise slug is required"
  run_reset_cli --enterprise-slug "${enterprise_slug}"
  exit 0
fi

backup_dir="${mode_or_backup}"
confirmation="${3:-}"
enterprise_slug="${4:-}"

[ -d "${backup_dir}" ] || die "backup directory not found: ${backup_dir:-unset}"
[ "${confirmation}" = "CLEAR local-demo operating-data-v3" ] \
  || die "destructive operation; exact confirmation is required"
[ -n "${enterprise_slug}" ] || die "enterprise slug is required"
require_command openssl

# The verifier checks the signed manifest, environment, checksums, encrypted
# Product/Source dumps and files archive before a destructive command can run.
"${SCRIPT_DIR}/verify-backup.sh" "${environment}" "${backup_dir}"
manifest_sha256="$(sha256_file "${backup_dir}/manifest.env")"
backup_reference="verified-manifest-sha256:${manifest_sha256}"

run_reset_cli \
  --enterprise-slug "${enterprise_slug}" \
  --execute \
  --confirmation "${confirmation}" \
  --backup-reference "${backup_reference}"

info "One-time operating-data V3 reset completed against verified backup ${manifest_sha256}."
