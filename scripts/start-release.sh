#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/runtime.sh
. "${SCRIPT_DIR}/lib/runtime.sh"

environment="${1:-}"
[ "${environment}" = "customer-template" ] \
  || die "reviewed release images may only start the customer-template environment"
require_command docker
require_command cosign
require_command jq
require_secret_files "${environment}"
load_runtime_environment "${environment}"
[ "${APP_MODE}" = "production" ] || die "release startup requires APP_MODE=production"

require_digest_image() {
  local name="$1"
  local value="$2"
  case "${value}" in
    *@sha256:[0-9a-f][0-9a-f]*) ;;
    *) die "${name} must be pinned by immutable sha256 digest, not a tag: ${value}" ;;
  esac
  digest="${value##*@sha256:}"
  if [ "${#digest}" -ne 64 ] \
    || ! printf '%s' "${digest}" | grep -Eq '^[0-9a-f]{64}$'; then
    die "${name} has an invalid sha256 digest"
  fi
}

require_digest_image WEB_IMAGE "${WEB_IMAGE:-}"
require_digest_image API_IMAGE "${API_IMAGE:-}"
require_digest_image WORKER_IMAGE "${WORKER_IMAGE:-}"
require_digest_image POSTGRES_IMAGE "${POSTGRES_IMAGE:-}"
require_digest_image NGINX_IMAGE "${NGINX_IMAGE:-}"
require_digest_image FILE_TOOL_IMAGE "${FILE_TOOL_IMAGE:-}"

runtime_dir="$(runtime_dir_for "${environment}")"
release_bundle_file="${RELEASE_BUNDLE_FILE:-${runtime_dir}/release/release-bundle.json}"
release_signature_bundle_file="${RELEASE_BUNDLE_SIGSTORE_FILE:-${runtime_dir}/release/release-bundle.sigstore.json}"
"${SCRIPT_DIR}/verify-release-bundle.sh" \
  "${release_bundle_file}" \
  "${release_signature_bundle_file}"

info "Pulling immutable reviewed images; no local source build is allowed..."
compose "${environment}" pull postgres db-role-init migrate db-permissions api worker web nginx
compose "${environment}" --profile tools pull db-backup-tool file-tool
compose "${environment}" up --detach --wait --no-build postgres
for one_shot in db-role-init migrate db-permissions; do
  compose "${environment}" up --no-deps --force-recreate --no-build \
    --abort-on-container-exit --exit-code-from "${one_shot}" "${one_shot}"
done
compose "${environment}" up --detach --no-build --remove-orphans api worker web nginx
compose "${environment}" ps
"${SCRIPT_DIR}/smoke-test.sh" "${environment}"
info "Release environment started from signed digest-pinned images at ${PUBLIC_BASE_URL}."
