#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/runtime.sh
. "${SCRIPT_DIR}/lib/runtime.sh"

environment="${1:-}"
validate_environment_name "${environment}"
require_command docker
require_secret_files "${environment}"

compose "${environment}" exec -T api /bin/sh -ec '
  DB_PASSWORD="$(cat /run/secrets/postgres_api_password)"
  export DATABASE_URL="postgresql+psycopg://${POSTGRES_API_USER}:${DB_PASSWORD}@postgres:5432/${POSTGRES_DB}"
  export SESSION_SECRET="$(cat /run/secrets/session_secret)"
  export CSRF_SECRET="$(cat /run/secrets/csrf_secret)"
  export FILE_ENCRYPTION_KEY="$(cat /run/secrets/file_encryption_key)"
  export AUDIT_HMAC_KEY="$(cat /run/secrets/audit_hmac_key)"
  export INTEGRATION_ENCRYPTION_KEY="$(cat /run/secrets/integration_encryption_key)"
  export HERMES_RUNTIME_HMAC_KEY="$(cat /run/secrets/hermes_runtime_hmac_key)"
  exec python -m executive_ai_api.personal_data_migration
'
