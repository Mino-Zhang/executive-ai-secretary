#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/runtime.sh
. "${SCRIPT_DIR}/lib/runtime.sh"

environment="${1:-}"
validate_environment_name "${environment}"
require_command docker
require_secret_files "${environment}"
load_runtime_environment "${environment}"

info "Starting ${environment}; the only host listener will be 127.0.0.1:${HTTP_PORT}."
# Build one image target at a time. `migrate` and `api` intentionally share the
# same image tag, and recent Docker Desktop releases can race while Compose
# submits duplicate BuildKit targets in one parallel bake session.
compose "${environment}" build api
compose "${environment}" build worker
compose "${environment}" build hermes-runtime
compose "${environment}" build web
if compose "${environment}" config --services | grep -qx source-postgres; then
  compose "${environment}" up --detach --wait postgres source-postgres
else
  compose "${environment}" up --detach --wait postgres
fi
for one_shot in db-role-init migrate db-permissions embedding-cache-init embedding-model-init; do
  compose "${environment}" up --no-deps --force-recreate \
    --abort-on-container-exit --exit-code-from "${one_shot}" "${one_shot}"
done
compose "${environment}" up --detach --wait --no-deps --no-build --remove-orphans \
  api mcp-hub hermes-runtime worker ingestion-worker file-worker scheduler web nginx
compose "${environment}" ps
info "Open ${PUBLIC_BASE_URL}"
