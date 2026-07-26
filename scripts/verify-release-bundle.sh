#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/runtime.sh
. "${SCRIPT_DIR}/lib/runtime.sh"

[ "$#" -eq 2 ] || die "usage: $0 <release-bundle.json> <release-bundle.sigstore.json>"
bundle_file="$1"
signature_bundle_file="$2"

require_command jq
require_command cosign
[ -f "${bundle_file}" ] && [ ! -L "${bundle_file}" ] && [ -s "${bundle_file}" ] \
  || die "release bundle must be a non-empty regular file: ${bundle_file}"
[ -f "${signature_bundle_file}" ] && [ ! -L "${signature_bundle_file}" ] && [ -s "${signature_bundle_file}" ] \
  || die "Sigstore bundle must be a non-empty regular file: ${signature_bundle_file}"

jq -e '
  (keys == ["database", "images", "release", "schemaVersion"]) and
  (.schemaVersion == "executive-ai.release-bundle/v1") and
  (.database | keys == ["alembicHead"]) and
  (.images | keys == ["api", "fileTool", "nginx", "postgres", "web", "worker"]) and
  (.release | keys == ["generatedAt", "gitCommit", "repository", "sourceRef", "trigger", "version", "workflow", "workflowRunId"]) and
  ([.database.alembicHead, .images.api, .images.fileTool, .images.nginx,
    .images.postgres, .images.web, .images.worker, .release.generatedAt,
    .release.gitCommit, .release.repository, .release.sourceRef,
    .release.trigger, .release.version, .release.workflow] | all(type == "string")) and
  (.release.workflowRunId | type == "number" and . > 0)
' "${bundle_file}" >/dev/null || die "release bundle schema is invalid or contains ambiguous fields"

version="$(jq -er '.release.version' "${bundle_file}")"
commit="$(jq -er '.release.gitCommit' "${bundle_file}")"
repository="$(jq -er '.release.repository' "${bundle_file}")"
source_ref="$(jq -er '.release.sourceRef' "${bundle_file}")"
trigger="$(jq -er '.release.trigger' "${bundle_file}")"
workflow="$(jq -er '.release.workflow' "${bundle_file}")"
alembic_head="$(jq -er '.database.alembicHead' "${bundle_file}")"

[[ "${version}" =~ ^[0-9]+\.[0-9]+\.[0-9]+([.-][A-Za-z0-9.-]+)?$ ]] \
  || die "release bundle version is not an immutable semantic version"
[[ "${commit}" =~ ^[0-9a-f]{40}$ ]] || die "release bundle Git commit is invalid"
[[ "${repository}" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] \
  || die "release bundle repository is invalid"
[[ "${alembic_head}" =~ ^[0-9a-f]{12,64}$ ]] || die "release bundle Alembic head is invalid"
[ "${workflow}" = ".github/workflows/release-images.yml" ] \
  || die "release bundle was produced by an unexpected workflow"

case "${trigger}" in
  push)
    [ "${source_ref}" = "refs/tags/production-v${version}" ] \
      || die "tag-triggered release bundle has a mismatched immutable version ref"
    ;;
  workflow_dispatch)
    [ "${source_ref}" = "refs/heads/main" ] \
      || die "manually triggered release bundle did not run from main"
    ;;
  *) die "release bundle trigger is not approved: ${trigger}" ;;
esac

[ -n "${RELEASE_VERSION:-}" ] || die "RELEASE_VERSION is required"
[ -n "${RELEASE_GIT_COMMIT:-}" ] || die "RELEASE_GIT_COMMIT is required"
[ -n "${EXPECTED_ALEMBIC_HEAD:-}" ] || die "EXPECTED_ALEMBIC_HEAD is required"
expected_repository="${RELEASE_GITHUB_REPOSITORY:-Mino-Zhang/executive-ai-secretary}"
[ "${version}" = "${RELEASE_VERSION}" ] || die "release bundle version does not match RELEASE_VERSION"
[ "${commit}" = "${RELEASE_GIT_COMMIT}" ] || die "release bundle commit does not match RELEASE_GIT_COMMIT"
[ "${repository}" = "${expected_repository}" ] \
  || die "release bundle repository does not match RELEASE_GITHUB_REPOSITORY"
[ "${alembic_head}" = "${EXPECTED_ALEMBIC_HEAD}" ] \
  || die "release bundle Alembic head does not match EXPECTED_ALEMBIC_HEAD"

for image_key in web api worker postgres nginx fileTool; do
  signed_image="$(jq -er --arg key "${image_key}" '.images[$key]' "${bundle_file}")"
  [[ "${signed_image}" =~ ^[^[:space:]@]+@sha256:[0-9a-f]{64}$ ]] \
    || die "signed ${image_key} image is not pinned by a valid sha256 digest"
done

assert_image_match() {
  local manifest_key="$1"
  local environment_name="$2"
  local expected_value="$3"
  local signed_value
  [ -n "${expected_value}" ] || die "${environment_name} is required"
  signed_value="$(jq -er --arg key "${manifest_key}" '.images[$key]' "${bundle_file}")"
  [ "${signed_value}" = "${expected_value}" ] \
    || die "${environment_name} does not exactly match the signed release bundle"
}

assert_image_match web WEB_IMAGE "${WEB_IMAGE:-}"
assert_image_match api API_IMAGE "${API_IMAGE:-}"
assert_image_match worker WORKER_IMAGE "${WORKER_IMAGE:-}"
assert_image_match postgres POSTGRES_IMAGE "${POSTGRES_IMAGE:-}"
assert_image_match nginx NGINX_IMAGE "${NGINX_IMAGE:-}"
assert_image_match fileTool FILE_TOOL_IMAGE "${FILE_TOOL_IMAGE:-}"

identity="https://github.com/${repository}/${workflow}@${source_ref}"
issuer="https://token.actions.githubusercontent.com"
workflow_claims=(
  --certificate-identity "${identity}"
  --certificate-oidc-issuer "${issuer}"
  --certificate-github-workflow-repository "${repository}"
  --certificate-github-workflow-sha "${commit}"
  --certificate-github-workflow-ref "${source_ref}"
  --certificate-github-workflow-trigger "${trigger}"
)

info "Verifying signed release bundle for ${version} (${commit})..."
cosign verify-blob \
  --bundle "${signature_bundle_file}" \
  "${workflow_claims[@]}" \
  "${bundle_file}" >/dev/null

for component in web api worker; do
  image="$(jq -er --arg key "${component}" '.images[$key]' "${bundle_file}")"
  case "${image}" in
    ghcr.io/*@sha256:*) ;;
    *) die "signed ${component} image must be a digest-pinned GHCR reference" ;;
  esac
  info "Verifying ${component} image signature and release annotations..."
  cosign verify \
    "${workflow_claims[@]}" \
    --annotations "release.version=${version}" \
    --annotations "release.git-commit=${commit}" \
    --annotations "release.component=${component}" \
    "${image}" >/dev/null
done

info "Signed release bundle, six image digests and Alembic head are consistent."
