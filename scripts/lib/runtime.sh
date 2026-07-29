#!/usr/bin/env bash
set -euo pipefail

SCRIPT_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_LIB_DIR}/../.." && pwd)"
COMPOSE_FILE="${REPO_ROOT}/compose.yml"

die() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

info() {
  printf '%s\n' "$*" >&2
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

validate_environment_name() {
  case "${1:-}" in
    local-demo|customer-template) ;;
    *) die "environment must be local-demo or customer-template" ;;
  esac
}

runtime_dir_for() {
  printf '%s/runtime/%s' "${REPO_ROOT}" "$1"
}

env_file_for() {
  printf '%s/.env' "$(runtime_dir_for "$1")"
}

load_runtime_environment() {
  local environment="$1"
  local env_file
  validate_environment_name "${environment}"
  env_file="$(env_file_for "${environment}")"
  [ -f "${env_file}" ] || die "${env_file} is missing; run ./scripts/prepare-env.sh ${environment} first"

  set -a
  # The generated file contains non-secret deployment settings only.
  # shellcheck disable=SC1090
  . "${env_file}"
  set +a

  [ "${APP_ENV:-}" = "${environment}" ] || die "APP_ENV in ${env_file} does not match ${environment}"
  [ "${HOST_BIND:-}" = "127.0.0.1" ] || die "phase 1 permits only HOST_BIND=127.0.0.1"
  case "${COMPOSE_PROJECT_NAME:-}" in
    executive-ai-local-demo|executive-ai-customer-template) ;;
    *) die "unexpected COMPOSE_PROJECT_NAME: ${COMPOSE_PROJECT_NAME:-unset}" ;;
  esac

  if [ "${environment}" = "customer-template" ]; then
    [ "${APP_MODE:-}" = "production" ] || die "customer-template requires APP_MODE=production"
    [ "${SEED_DEMO_DATA:-}" = "false" ] || die "customer-template refuses SEED_DEMO_DATA=${SEED_DEMO_DATA:-unset}"
  fi

  export RUNTIME_DIR="./runtime/${environment}"
}

compose() {
  local environment="$1"
  shift
  load_runtime_environment "${environment}"
  docker compose \
    --project-name "${COMPOSE_PROJECT_NAME}" \
    --env-file "$(env_file_for "${environment}")" \
    --file "${COMPOSE_FILE}" \
    "$@"
}

require_secret_files() {
  local environment="$1"
  local secrets_dir
  local name
  secrets_dir="$(runtime_dir_for "${environment}")/secrets"
  for name in postgres_password postgres_migrator_password postgres_runtime_password postgres_backup_password session_secret csrf_secret file_encryption_key file_encryption_key_ring audit_hmac_key audit_hmac_key_ring backup_encryption_key backup_signing_key backup_signing_public_key; do
    [ -s "${secrets_dir}/${name}" ] || die "missing secret: ${secrets_dir}/${name}; rerun prepare-env"
  done
}

sha256_file() {
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  else
    sha256sum "$1" | awk '{print $1}'
  fi
}
