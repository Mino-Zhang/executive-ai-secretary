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
for secret_name in postgres_password postgres_migrator_password postgres_runtime_password postgres_backup_password session_secret csrf_secret file_encryption_key file_encryption_key_ring audit_hmac_key audit_hmac_key_ring backup_encryption_key backup_signing_key backup_signing_public_key; do
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
generate_hex_secret session_secret
generate_hex_secret csrf_secret
generate_base64_key file_encryption_key
printf '{}\n' > "${secrets_dir}/file_encryption_key_ring"
chmod 600 "${secrets_dir}/file_encryption_key_ring"
generate_hex_secret audit_hmac_key
printf '{}\n' > "${secrets_dir}/audit_hmac_key_ring"
chmod 600 "${secrets_dir}/audit_hmac_key_ring"
generate_base64_key backup_encryption_key
openssl genpkey -algorithm ED25519 -out "${secrets_dir}/backup_signing_key" >/dev/null 2>&1
openssl pkey -in "${secrets_dir}/backup_signing_key" -pubout \
  -out "${secrets_dir}/backup_signing_public_key" >/dev/null 2>&1
chmod 600 "${secrets_dir}/backup_signing_key" "${secrets_dir}/backup_signing_public_key"

load_runtime_environment "${environment}"
require_secret_files "${environment}"
docker compose \
  --project-name "${COMPOSE_PROJECT_NAME}" \
  --env-file "${env_file}" \
  --file "${COMPOSE_FILE}" \
  config --quiet

info "Prepared ${environment} in ${runtime_dir}."
info "No administrator credential was created. Start the stack, then run scripts/bootstrap-admin.sh."
info "Gateway: ${PUBLIC_BASE_URL} (loopback only)"
