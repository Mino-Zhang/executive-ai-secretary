#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/runtime.sh
. "${SCRIPT_DIR}/lib/runtime.sh"

environment="${1:-}"
[ "$#" -eq 1 ] || die "usage: $0 <local-demo|customer-template>"
validate_environment_name "${environment}"
require_command openssl
require_command docker

template="${REPO_ROOT}/deploy/environments/${environment}.env.example"
runtime_dir="$(runtime_dir_for "${environment}")"
secrets_dir="${runtime_dir}/secrets"
env_file="${runtime_dir}/.env"

[ -f "${template}" ] || die "template not found: ${template}"
[ ! -e "${env_file}" ] || die "${env_file} already exists; prepare-env never rotates keys"
for secret_name in postgres_password postgres_migrator_password postgres_runtime_password postgres_backup_password postgres_api_password postgres_assistant_worker_password postgres_file_worker_password postgres_ingestion_password postgres_scheduler_password postgres_mcp_password session_secret csrf_secret file_encryption_key file_encryption_key_ring audit_hmac_key audit_hmac_key_ring capability_hmac_key integration_encryption_key integration_encryption_key_ring hermes_runtime_hmac_key source_database_url source_postgres_password source_reader_password source_writer_password feishu_runtime_secret feishu_provisioning_secret backup_encryption_key backup_signing_key backup_signing_public_key; do
  [ ! -e "${secrets_dir}/${secret_name}" ] \
    || die "${secrets_dir}/${secret_name} already exists; use the reviewed rotation and re-encryption runbook"
done

umask 077
mkdir -p "${secrets_dir}"
chmod 700 "${runtime_dir}" "${secrets_dir}"
cp "${template}" "${env_file}"
chmod 600 "${env_file}"

generate_hex_secret() {
  openssl rand -hex 32 > "${secrets_dir}/$1"
  chmod 600 "${secrets_dir}/$1"
}

generate_base64_key() {
  openssl rand -base64 32 | tr -d '\n' > "${secrets_dir}/$1"
  printf '\n' >> "${secrets_dir}/$1"
  chmod 600 "${secrets_dir}/$1"
}

generate_hex_secret postgres_password
generate_hex_secret postgres_migrator_password
generate_hex_secret postgres_runtime_password
generate_hex_secret postgres_backup_password
generate_hex_secret postgres_api_password
generate_hex_secret postgres_assistant_worker_password
generate_hex_secret postgres_file_worker_password
generate_hex_secret postgres_ingestion_password
generate_hex_secret postgres_scheduler_password
generate_hex_secret postgres_mcp_password
generate_hex_secret session_secret
generate_hex_secret csrf_secret
generate_base64_key file_encryption_key
printf '{}\n' > "${secrets_dir}/file_encryption_key_ring"
chmod 600 "${secrets_dir}/file_encryption_key_ring"
generate_hex_secret audit_hmac_key
printf '{}\n' > "${secrets_dir}/audit_hmac_key_ring"
chmod 600 "${secrets_dir}/audit_hmac_key_ring"
generate_hex_secret capability_hmac_key
generate_base64_key integration_encryption_key
printf '{}\n' > "${secrets_dir}/integration_encryption_key_ring"
chmod 600 "${secrets_dir}/integration_encryption_key_ring"
generate_hex_secret hermes_runtime_hmac_key
generate_hex_secret source_postgres_password
generate_hex_secret source_reader_password
generate_hex_secret source_writer_password
for optional_secret in source_database_url feishu_runtime_secret feishu_provisioning_secret; do
  : > "${secrets_dir}/${optional_secret}"
  chmod 600 "${secrets_dir}/${optional_secret}"
done
generate_base64_key backup_encryption_key
openssl genpkey -algorithm ED25519 -out "${secrets_dir}/backup_signing_key" >/dev/null 2>&1
openssl pkey -in "${secrets_dir}/backup_signing_key" -pubout \
  -out "${secrets_dir}/backup_signing_public_key" >/dev/null 2>&1
chmod 600 "${secrets_dir}/backup_signing_key" "${secrets_dir}/backup_signing_public_key"

load_runtime_environment "${environment}"
require_secret_files "${environment}"
compose "${environment}" config --quiet

info "Prepared ${environment} in ${runtime_dir}."
info "No administrator credential was created. Start the stack, then run scripts/bootstrap-admin.sh."
info "Gateway: ${PUBLIC_BASE_URL} (loopback only)"
