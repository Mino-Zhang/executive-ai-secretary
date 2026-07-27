#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/runtime.sh
. "${SCRIPT_DIR}/lib/runtime.sh"

require_command docker

for script in "${SCRIPT_DIR}"/*.sh "${SCRIPT_DIR}"/lib/*.sh; do
  bash -n "${script}"
done

if command -v shellcheck >/dev/null 2>&1; then
  shellcheck "${SCRIPT_DIR}"/*.sh "${SCRIPT_DIR}"/lib/*.sh
fi

for environment in local-demo customer-template; do
  env_file="$(env_file_for "${environment}")"
  if [ ! -f "${env_file}" ]; then
    "${SCRIPT_DIR}/prepare-env.sh" "${environment}"
  fi
  missing_role_secret=false
  for name in postgres_migrator_password postgres_runtime_password postgres_backup_password postgres_api_password postgres_assistant_worker_password postgres_file_worker_password postgres_ingestion_password postgres_scheduler_password postgres_mcp_password file_encryption_key_ring audit_hmac_key_ring integration_encryption_key_ring; do
    [ -s "$(runtime_dir_for "${environment}")/secrets/${name}" ] || missing_role_secret=true
  done
  if [ "${missing_role_secret}" = true ]; then
    "${SCRIPT_DIR}/upgrade-env-secrets.sh" "${environment}"
  fi
  require_secret_files "${environment}"
  role_secrets_before="$({
    sha256_file "$(runtime_dir_for "${environment}")/secrets/postgres_migrator_password"
    sha256_file "$(runtime_dir_for "${environment}")/secrets/postgres_runtime_password"
    sha256_file "$(runtime_dir_for "${environment}")/secrets/postgres_backup_password"
    sha256_file "$(runtime_dir_for "${environment}")/secrets/postgres_api_password"
    sha256_file "$(runtime_dir_for "${environment}")/secrets/postgres_assistant_worker_password"
    sha256_file "$(runtime_dir_for "${environment}")/secrets/postgres_file_worker_password"
    sha256_file "$(runtime_dir_for "${environment}")/secrets/postgres_ingestion_password"
    sha256_file "$(runtime_dir_for "${environment}")/secrets/postgres_scheduler_password"
    sha256_file "$(runtime_dir_for "${environment}")/secrets/postgres_mcp_password"
    sha256_file "$(runtime_dir_for "${environment}")/secrets/file_encryption_key_ring"
    sha256_file "$(runtime_dir_for "${environment}")/secrets/audit_hmac_key_ring"
    sha256_file "$(runtime_dir_for "${environment}")/secrets/integration_encryption_key_ring"
  } | tr '\n' ' ')"
  "${SCRIPT_DIR}/upgrade-env-secrets.sh" "${environment}" >/dev/null
  role_secrets_after="$({
    sha256_file "$(runtime_dir_for "${environment}")/secrets/postgres_migrator_password"
    sha256_file "$(runtime_dir_for "${environment}")/secrets/postgres_runtime_password"
    sha256_file "$(runtime_dir_for "${environment}")/secrets/postgres_backup_password"
    sha256_file "$(runtime_dir_for "${environment}")/secrets/postgres_api_password"
    sha256_file "$(runtime_dir_for "${environment}")/secrets/postgres_assistant_worker_password"
    sha256_file "$(runtime_dir_for "${environment}")/secrets/postgres_file_worker_password"
    sha256_file "$(runtime_dir_for "${environment}")/secrets/postgres_ingestion_password"
    sha256_file "$(runtime_dir_for "${environment}")/secrets/postgres_scheduler_password"
    sha256_file "$(runtime_dir_for "${environment}")/secrets/postgres_mcp_password"
    sha256_file "$(runtime_dir_for "${environment}")/secrets/file_encryption_key_ring"
    sha256_file "$(runtime_dir_for "${environment}")/secrets/audit_hmac_key_ring"
    sha256_file "$(runtime_dir_for "${environment}")/secrets/integration_encryption_key_ring"
  } | tr '\n' ' ')"
  [ "${role_secrets_before}" = "${role_secrets_after}" ] \
    || die "upgrade-env-secrets rotated an existing database role credential"
  compose "${environment}" config --quiet
done

local_config="$(compose local-demo config)"
customer_config="$(compose customer-template config)"
bootstrap_config="$(
  BOOTSTRAP_ADMIN_EMAIL=admin@example.com \
  BOOTSTRAP_ADMIN_DISPLAY_NAME=Admin \
  BOOTSTRAP_ENTERPRISE_NAME=Customer \
  BOOTSTRAP_ENTERPRISE_SLUG=customer \
  compose customer-template --profile bootstrap config
)"
executive_config="$(
  EXECUTIVE_EMAIL=chairman@example.com \
  EXECUTIVE_DISPLAY_NAME=Chairman \
  EXECUTIVE_ENTERPRISE_SLUG=customer \
  EXECUTIVE_SCOPE_MODE=enterprise \
  EXECUTIVE_ORGANIZATION_UNIT_CODES='' \
  compose customer-template --profile bootstrap config
)"
seed_config="$(
  DEMO_ENTERPRISE_SLUG=demo-enterprise \
  compose local-demo --profile demo-seed config
)"
source_admin_config="$(
  SOURCE_ENTERPRISE_SLUG=customer \
  SOURCE_DISPLAY_NAME='客户脱敏经营数据' \
  compose customer-template --profile source-admin config
)"

grep -q 'host_ip: 127.0.0.1' <<< "${local_config}" \
  || die "local-demo gateway is not loopback-only"
grep -q 'host_ip: 127.0.0.1' <<< "${customer_config}" \
  || die "customer-template gateway is not loopback-only"
grep -q 'SEED_DEMO_DATA: "false"' <<< "${customer_config}" \
  || die "customer-template seed invariant is missing"
grep -q 'source: audit_hmac_key' <<< "${customer_config}" \
  || die "backend services are missing the independent audit HMAC secret"
grep -q 'source: audit_hmac_key_ring' <<< "${customer_config}" \
  || die "backend services are missing the versioned audit key ring"
grep -q 'source: file_encryption_key_ring' <<< "${customer_config}" \
  || die "API and Worker are missing the versioned file key ring"
grep -q 'source: integration_encryption_key_ring' <<< "${customer_config}" \
  || die "API and assistant worker are missing the versioned integration key ring"
api_service="$(sed -n '/^  api:/,/^  worker:/p' "${REPO_ROOT}/compose.yml")"
assistant_service="$(sed -n '/^  worker:/,/^  ingestion-worker:/p' "${REPO_ROOT}/compose.yml")"
hermes_service="$(sed -n '/^  hermes-runtime:/,/^  web:/p' "${REPO_ROOT}/compose.yml")"
for service_contract in \
  'integration_encryption_key' \
  'integration_encryption_key_ring' \
  'hermes_runtime_hmac_key'; do
  grep -q "${service_contract}" <<< "${api_service}" \
    || die "API is missing the Anspire credential boundary: ${service_contract}"
  grep -q "${service_contract}" <<< "${assistant_service}" \
    || die "assistant worker is missing the Anspire credential boundary: ${service_contract}"
done
grep -q 'hermes_runtime_hmac_key' <<< "${hermes_service}" \
  || die "Hermes runtime is missing internal request signing"
if grep -Eq 'integration_encryption_key|model_api_key' <<< "${hermes_service}"; then
  die "Hermes runtime must not receive a persistent enterprise model credential"
fi
if grep -q 'model_api_key' "${REPO_ROOT}/compose.yml"; then
  die "legacy persistent model key secret must not be present"
fi
if grep -q 'source: secret_key' <<< "${customer_config}"; then
  die "legacy SECRET_KEY pseudo-separation must not be present"
fi
[ "$(grep -c 'export AUDIT_HMAC_KEY=' <<< "${customer_config}")" -eq 4 ] \
  || die "only api and the three job workers should receive the audit HMAC secret during normal startup"
[ "$(grep -c 'export AUDIT_HMAC_KEY=' <<< "${bootstrap_config}")" -eq 6 ] \
  || die "bootstrap services do not all receive the audit HMAC secret"
[ "$(grep -c 'export AUDIT_HMAC_KEY=' <<< "${seed_config}")" -eq 6 ] \
  || die "demo seed does not receive the audit HMAC secret"
grep -q 'SERVICE_ROLE: migration' <<< "${customer_config}" \
  || die "migration service role is not explicit"
for role in assistant_worker ingestion_worker file_worker; do
  grep -q "SERVICE_ROLE: ${role}" <<< "${customer_config}" \
    || die "${role} service role is not explicit"
done
embedding_cache_init="$({
  sed -n '/^  embedding-cache-init:/,/^  file-worker:/p' "${REPO_ROOT}/compose.yml"
})"
grep -q 'user: "0:0"' <<< "${embedding_cache_init}" \
  || die "embedding-cache-init does not explicitly run as root"
grep -q -- '- CHOWN' <<< "${embedding_cache_init}" \
  || die "embedding-cache-init is missing its sole required CHOWN capability"
if grep -Eq -- '- (DAC_OVERRIDE|FOWNER|SYS_ADMIN)' <<< "${embedding_cache_init}"; then
  die "embedding-cache-init receives broader filesystem capabilities than required"
fi
grep -q 'embedding-model-init:' \
  < <(sed -n '/^  file-worker:/,/^  scheduler:/p' "${REPO_ROOT}/compose.yml") \
  || die "file-worker does not wait for verified embedding model initialization"
embedding_model_init="$({
  sed -n '/^  embedding-model-init:/,/^  file-worker:/p' "${REPO_ROOT}/compose.yml"
})"
grep -q 'user: "999:999"' <<< "${embedding_model_init}" \
  || die "embedding-model-init is not pinned to the unprivileged application uid"
grep -q -- '- ai-egress' <<< "${embedding_model_init}" \
  || die "embedding-model-init does not use the isolated egress network"
if grep -q 'secrets:' <<< "${embedding_model_init}"; then
  die "embedding-model-init must not receive application or database secrets"
fi
for cache_contract in \
  'EMBEDDING_CACHE_DIR: /opt/models' \
  'HOME: /opt/models/home' \
  'HF_HOME: /opt/models/huggingface' \
  'HF_HUB_CACHE: /opt/models/huggingface/hub'; do
  grep -q "${cache_contract}" \
    < <(sed -n '/^  file-worker:/,/^  scheduler:/p' <<< "${customer_config}") \
    || die "file worker cache contract is missing: ${cache_contract}"
done
grep -q 'source: postgres_migrator_password' <<< "${customer_config}" \
  || die "independent migrator password is missing"
grep -q 'source: postgres_backup_password' <<< "${customer_config}" \
  || die "independent read-only backup password is missing"
grep -q 'REVOKE UPDATE, DELETE, TRUNCATE ON TABLE public.audit_events' \
  "${REPO_ROOT}/deploy/postgres/ensure-runtime-role.sh" \
  || die "runtime role can mutate immutable audit events"
grep -q 'REVOKE DELETE, TRUNCATE ON TABLE public.audit_chain_heads' \
  "${REPO_ROOT}/deploy/postgres/ensure-runtime-role.sh" \
  || die "runtime role can delete or truncate audit-chain heads"
assert_service_database_identity() {
  local service="$1"
  local next_service="$2"
  local expected_secret="$3"
  local expected_user="$4"
  local section
  local candidate
  section="$(sed -n "/^  ${service}:/,/^  ${next_service}:/p" "${REPO_ROOT}/compose.yml")"
  grep -q "${expected_secret}" <<< "${section}" \
    || die "${service} does not receive its dedicated database credential"
  grep -q "${expected_user}" <<< "${section}" \
    || die "${service} does not connect with its dedicated database role"
  for candidate in \
    postgres_runtime_password \
    postgres_api_password \
    postgres_assistant_worker_password \
    postgres_file_worker_password \
    postgres_ingestion_password \
    postgres_scheduler_password \
    postgres_mcp_password; do
    if [ "${candidate}" != "${expected_secret}" ] && grep -q "${candidate}" <<< "${section}"; then
      die "${service} receives another service's database credential: ${candidate}"
    fi
  done
  if grep -Eq 'postgres_password|postgres_migrator_password|postgres_backup_password' \
    <<< "${section}"; then
    die "${service} receives an owner, migrator or backup credential"
  fi
}

assert_service_database_identity api worker postgres_api_password POSTGRES_API_USER
assert_service_database_identity worker ingestion-worker \
  postgres_assistant_worker_password POSTGRES_ASSISTANT_WORKER_USER
assert_service_database_identity ingestion-worker embedding-cache-init \
  postgres_ingestion_password POSTGRES_INGESTION_USER
assert_service_database_identity file-worker scheduler \
  postgres_file_worker_password POSTGRES_FILE_WORKER_USER
assert_service_database_identity scheduler mcp-hub \
  postgres_scheduler_password POSTGRES_SCHEDULER_USER
assert_service_database_identity mcp-hub hermes-runtime postgres_mcp_password POSTGRES_MCP_USER

grep -q "(:'mcp_user', 'fact_opportunity')" \
  "${REPO_ROOT}/deploy/postgres/ensure-runtime-role.sh" \
  || die "MCP role is missing explicit business-fact read access"
if grep -Eq "\(:'mcp_user', '(INSERT|UPDATE|DELETE)" \
  "${REPO_ROOT}/deploy/postgres/ensure-runtime-role.sh"; then
  die "MCP role receives a mutation grant"
fi
if grep -Eq 'session_secret|csrf_secret|postgres_password|postgres_migrator_password|postgres_backup_password' \
  < <(sed -n '/^  worker:/,/^  web:/p' "${REPO_ROOT}/compose.yml"); then
  die "worker receives an unrelated session, CSRF, owner, migrator or backup secret"
fi
if grep -Eq 'file_encryption_key|file_encryption_key_ring' \
  < <(sed -n '/^  worker:/,/^  ingestion-worker:/p' "${REPO_ROOT}/compose.yml"); then
  die "assistant worker receives file decryption material it does not use"
fi
if grep -Eq 'file_encryption_key|file_encryption_key_ring|capability_hmac_key' \
  < <(sed -n '/^  ingestion-worker:/,/^  file-worker:/p' "${REPO_ROOT}/compose.yml"); then
  die "ingestion worker receives file or model-tool signing material it does not use"
fi
if grep -Eq 'capability_hmac_key' \
  < <(sed -n '/^  file-worker:/,/^  scheduler:/p' "${REPO_ROOT}/compose.yml"); then
  die "file worker receives model-tool signing material it does not use"
fi
if grep -Eq 'session_secret|csrf_secret|file_encryption_key|audit_hmac_key|postgres_password|postgres_runtime_password|postgres_backup_password' \
  < <(sed -n '/^  migrate:/,/^  db-permissions:/p' "${REPO_ROOT}/compose.yml"); then
  die "migrator receives an unrelated application, owner, runtime or backup secret"
fi
for bootstrap_flag in --email --display-name --enterprise-name --enterprise-slug --password-stdin --force-password-change; do
  grep -q -- "${bootstrap_flag}" <<< "${bootstrap_config}" \
    || die "bootstrap command is missing ${bootstrap_flag}"
done
for executive_flag in create-user --role --enterprise-wide-scope --organization-unit-code; do
  grep -q -- "${executive_flag}" <<< "${executive_config}" \
    || die "executive bootstrap command is missing ${executive_flag}"
done
grep -q -- '--enterprise-slug' <<< "${seed_config}" \
  || die "demo seed does not pass an explicit enterprise slug"
grep -q 'DEMO_ENTERPRISE_SLUG: demo-enterprise' <<< "${seed_config}" \
  || die "demo seed enterprise slug is not injected"
grep -q -- 'configure-source' <<< "${source_admin_config}" \
  || die "sanitized source registration command is missing"
grep -q -- '--display-name' <<< "${source_admin_config}" \
  || die "sanitized source display name is not explicit"
grep -q -- 'trigger-sync' <<< "${source_admin_config}" \
  || die "FDE immediate source synchronization command is missing"
grep -q -- '--enterprise-slug' <<< "${source_admin_config}" \
  || die "FDE immediate synchronization does not bind an enterprise"
grep -Fq 'pgvector/pgvector:0.8.5-pg17@sha256:d2ef61f42ef767baa5a1475393303cc235bcd92febd9d7014eddb48b41f3bad0' \
  "${REPO_ROOT}/deploy/compose/local-demo.yml" \
  || die "local sanitized source PostgreSQL is not pinned to the reviewed database image"

for application_dockerfile in \
  "${REPO_ROOT}/services/api/Dockerfile" \
  "${REPO_ROOT}/services/worker/Dockerfile"; do
  grep -Eq 'groupadd .*--gid 999' "${application_dockerfile}" \
    || die "application Dockerfile does not pin the shared gid to 999: ${application_dockerfile}"
  grep -Eq 'useradd .*--uid 999' "${application_dockerfile}" \
    || die "application Dockerfile does not pin the shared uid to 999: ${application_dockerfile}"
done

# Every external build-stage source must retain a human-readable tag and an
# immutable sha256 manifest-list digest. Named stages declared earlier in the
# same Dockerfile are the only unpinned FROM/COPY --from values permitted.
python3 - "${REPO_ROOT}/Dockerfile.web" \
  "${REPO_ROOT}/services/api/Dockerfile" \
  "${REPO_ROOT}/services/worker/Dockerfile" \
  "${REPO_ROOT}/services/hermes-runtime/Dockerfile" <<'PY'
from __future__ import annotations

import pathlib
import re
import sys

digest_image = re.compile(r"^[^@\s]+:[^@\s]+@sha256:[0-9a-f]{64}$")
for argument in sys.argv[1:]:
    path = pathlib.Path(argument)
    stages: set[str] = set()
    for number, raw_line in enumerate(path.read_text().splitlines(), start=1):
        line = raw_line.strip()
        from_match = re.match(r"FROM\s+(\S+)(?:\s+AS\s+(\S+))?$", line, re.IGNORECASE)
        if from_match:
            source, stage = from_match.groups()
            if source not in stages and not digest_image.fullmatch(source):
                raise SystemExit(
                    f"external FROM is not tag-and-digest pinned: {path}:{number}: {source}"
                )
            if stage:
                stages.add(stage)
            continue
        copy_match = re.search(r"(?:^|\s)--from=(\S+)", line)
        if copy_match:
            source = copy_match.group(1)
            if source not in stages and not source.isdigit() and not digest_image.fullmatch(source):
                raise SystemExit(
                    f"external COPY --from is not tag-and-digest pinned: {path}:{number}: {source}"
                )
PY

node_base='public.ecr.aws/docker/library/node:22.23.1-alpine3.24@sha256:16e22a550f3863206a3f701448c45f7912c6896a62de43add43bb9c86130c3e2'
python_base='public.ecr.aws/docker/library/python:3.12.13-slim-trixie@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de'
uv_base='ghcr.io/astral-sh/uv:0.10.5@sha256:476133fa2aaddb4cbee003e3dc79a88d327a5dc7cb3179b7f02cabd8fdfbcc6e'
[ "$(grep -Fc "${node_base}" "${REPO_ROOT}/Dockerfile.web")" -eq 2 ] \
  || die "web build does not use the reviewed Node manifest for both external stages"
for application_dockerfile in \
  "${REPO_ROOT}/services/api/Dockerfile" \
  "${REPO_ROOT}/services/worker/Dockerfile" \
  "${REPO_ROOT}/services/hermes-runtime/Dockerfile"; do
  grep -Fq "${python_base}" "${application_dockerfile}" \
    || die "application build does not use the reviewed Python manifest: ${application_dockerfile}"
  grep -Fq "${uv_base}" "${application_dockerfile}" \
    || die "application build does not use the reviewed uv manifest: ${application_dockerfile}"
done

grep -q 'user: "999:999"' \
  < <(sed -n '/^  file-tool:/,/^  db-backup-tool:/p' "${REPO_ROOT}/compose.yml") \
  || die "file-tool does not share the unprivileged API/Worker uid and gid"

if grep -Eiq '(password|secret|token)[[:space:]]*=[[:space:]]*[^#[:space:]]+' \
  "${REPO_ROOT}/deploy/environments/customer-template.env.example"; then
  die "customer-template contains a credential-like value"
fi

grep -q 'verify-release-bundle.sh' "${SCRIPT_DIR}/start-release.sh" \
  || die "release startup does not verify the signed release bundle"
# This is intentionally matched as literal shell source.
# shellcheck disable=SC2016
grep -q 'compose "${environment}" port nginx 8080' "${SCRIPT_DIR}/smoke-test.sh" \
  || die "smoke test does not verify Docker's effective gateway publication"
grep -q -- '--no-build' "${SCRIPT_DIR}/start-release.sh" \
  || die "release startup could rebuild unreviewed local source"
grep -q 'require_digest_image POSTGRES_IMAGE' "${SCRIPT_DIR}/start-release.sh" \
  || die "release startup does not pin infrastructure images by digest"
grep -q 'cosign sign --yes' "${REPO_ROOT}/.github/workflows/release-images.yml" \
  || die "release workflow does not sign immutable image digests"
grep -q 'cosign sign-blob --yes' "${REPO_ROOT}/.github/workflows/release-images.yml" \
  || die "release workflow does not sign the release bundle"
grep -q 'environment: production-images' "${REPO_ROOT}/.github/workflows/release-images.yml" \
  || die "release publishing is not protected by the production-images environment"
grep -q 'git merge-base --is-ancestor' "${REPO_ROOT}/.github/workflows/release-images.yml" \
  || die "release workflow does not bind the release commit to main ancestry"
# The workflow contract is intentionally matched as literal GitHub shell source.
# shellcheck disable=SC2016
grep -q '\[ "${SOURCE_REF}" = "refs/heads/main" \]' "${REPO_ROOT}/.github/workflows/release-images.yml" \
  || die "manual release dispatch is not restricted to main"

top_level_permissions="$(
  sed -n '/^permissions:/,/^jobs:/p' "${REPO_ROOT}/.github/workflows/release-images.yml"
)"
grep -q '^  actions: read$' <<< "${top_level_permissions}" \
  || die "release authorization cannot inspect GitHub Environment protection"
grep -q '^  contents: read$' <<< "${top_level_permissions}" \
  || die "release workflow does not default to contents:read"
if grep -Eq 'packages:|id-token:' <<< "${top_level_permissions}"; then
  die "release workflow grants publish permissions at top level"
fi
authorization_job="$(
  sed -n '/^  release_authorization:/,/^  verify:/p' \
    "${REPO_ROOT}/.github/workflows/release-images.yml"
)"
for authorization_contract in \
  'PRODUCTION_IMAGE_RELEASES_ENABLED' \
  '/environments/production-images' \
  'X-GitHub-Api-Version: 2026-03-10' \
  'required_reviewers' \
  'prevent_self_review == true' \
  'deployment-branch-policies' \
  '.total_count == 2' \
  '.name == "main" and .type == "branch"' \
  '.name == "production-v\*" and .type == "tag"' \
  'authorized=true'; do
  grep -q -- "${authorization_contract}" <<< "${authorization_job}" \
    || die "release authorization is missing fail-closed contract: ${authorization_contract}"
done
if grep -Eq 'packages:[[:space:]]*write|id-token:[[:space:]]*write|^[[:space:]]+environment:' \
  <<< "${authorization_job}"; then
  die "release authorization obtains publishing capability before the protected job"
fi
publish_job="$(sed -n '/^  publish:/,$p' "${REPO_ROOT}/.github/workflows/release-images.yml")"
grep -q 'needs: \[preflight, release_authorization\]' <<< "${publish_job}" \
  || die "publish job does not depend directly on the fail-closed authorization gate"
grep -q 'needs.release_authorization.outputs.authorized' <<< "${publish_job}" \
  || die "publish job does not require the positive authorization output"
for capability in 'packages: write' 'id-token: write'; do
  grep -q "${capability}" <<< "${publish_job}" \
    || die "protected publish job is missing ${capability}"
done

for workflow in \
  "${REPO_ROOT}/.github/workflows/ci.yml" \
  "${REPO_ROOT}/.github/workflows/release-images.yml"; do
  grep -q 'tests/test_postgres_key_rotation.py' "${workflow}" \
    || die "PostgreSQL key-rotation concurrency test is missing from $(basename "${workflow}")"
done

while IFS= read -r action_line; do
  action_ref="$(sed -E 's/.*uses:[[:space:]]*([^[:space:]#]+).*/\1/' <<< "${action_line}")"
  case "${action_ref}" in
    ./*) continue ;;
  esac
  [[ "${action_ref}" =~ @([0-9a-f]{40})$ ]] \
    || die "GitHub Action is not pinned to a full commit SHA: ${action_ref}"
  grep -Eq '#[[:space:]]+v?[0-9]+' <<< "${action_line}" \
    || die "pinned GitHub Action is missing its reviewed version comment: ${action_ref}"
done < <(grep -RhE 'uses:[[:space:]]*[^[:space:]#]+' "${REPO_ROOT}/.github/workflows")

release_baseline="${REPO_ROOT}/deploy/release-baseline.json"
actual_alembic_head="$(python3 "${SCRIPT_DIR}/resolve-alembic-head.py")"
baseline_alembic_head="$(jq -er '.alembicHead' "${release_baseline}")"
[ "${actual_alembic_head}" = "${baseline_alembic_head}" ] \
  || die "release baseline Alembic head does not match the migration graph"
grep -q "^EXPECTED_ALEMBIC_HEAD=${baseline_alembic_head}$" \
  "${REPO_ROOT}/deploy/environments/customer-template.env.example" \
  || die "customer release expectation does not match the reviewed Alembic head"

release_fixture_dir="$(mktemp -d)"
cleanup_release_fixture() {
  rm -rf -- "${release_fixture_dir}"
}
trap cleanup_release_fixture EXIT
mkdir -p "${release_fixture_dir}/bin"
cat > "${release_fixture_dir}/bin/cosign" <<'EOF'
#!/usr/bin/env sh
set -eu
printf '%s\n' "$*" >> "${COSIGN_TEST_LOG:?}"
case "$1" in
  verify|verify-blob) exit 0 ;;
  *) exit 1 ;;
esac
EOF
chmod 700 "${release_fixture_dir}/bin/cosign"
test_commit="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
test_digest="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
test_web="ghcr.io/mino-zhang/executive-ai-web@sha256:${test_digest}"
test_api="ghcr.io/mino-zhang/executive-ai-api@sha256:${test_digest}"
test_worker="ghcr.io/mino-zhang/executive-ai-worker@sha256:${test_digest}"
test_hermes="ghcr.io/mino-zhang/executive-ai-hermes@sha256:${test_digest}"
test_postgres="$(jq -er '.infrastructureImages.postgres' "${release_baseline}")"
test_nginx="$(jq -er '.infrastructureImages.nginx' "${release_baseline}")"
test_file_tool="$(jq -er '.infrastructureImages.fileTool' "${release_baseline}")"
jq -nS \
  --arg commit "${test_commit}" \
  --arg head "${baseline_alembic_head}" \
  --arg web "${test_web}" \
  --arg api "${test_api}" \
  --arg worker "${test_worker}" \
  --arg hermes "${test_hermes}" \
  --arg postgres "${test_postgres}" \
  --arg nginx "${test_nginx}" \
  --arg fileTool "${test_file_tool}" \
  '{
    schemaVersion: "executive-ai.release-bundle/v1",
    release: {
      version: "1.2.3",
      gitCommit: $commit,
      repository: "Mino-Zhang/executive-ai-secretary",
      sourceRef: "refs/tags/production-v1.2.3",
      trigger: "push",
      workflow: ".github/workflows/release-images.yml",
      workflowRunId: 1,
      generatedAt: "2026-07-27T00:00:00Z"
    },
    database: {alembicHead: $head},
    images: {
      web: $web,
      api: $api,
      worker: $worker,
      hermes: $hermes,
      postgres: $postgres,
      nginx: $nginx,
      fileTool: $fileTool
    }
  }' > "${release_fixture_dir}/release-bundle.json"
printf '{}\n' > "${release_fixture_dir}/release-bundle.sigstore.json"
cosign_test_log="${release_fixture_dir}/cosign.log"
PATH="${release_fixture_dir}/bin:${PATH}" \
COSIGN_TEST_LOG="${cosign_test_log}" \
RELEASE_VERSION=1.2.3 \
RELEASE_GIT_COMMIT="${test_commit}" \
RELEASE_GITHUB_REPOSITORY=Mino-Zhang/executive-ai-secretary \
EXPECTED_ALEMBIC_HEAD="${baseline_alembic_head}" \
WEB_IMAGE="${test_web}" \
API_IMAGE="${test_api}" \
WORKER_IMAGE="${test_worker}" \
HERMES_IMAGE="${test_hermes}" \
POSTGRES_IMAGE="${test_postgres}" \
NGINX_IMAGE="${test_nginx}" \
FILE_TOOL_IMAGE="${test_file_tool}" \
  "${SCRIPT_DIR}/verify-release-bundle.sh" \
  "${release_fixture_dir}/release-bundle.json" \
  "${release_fixture_dir}/release-bundle.sigstore.json" >/dev/null
[ "$(wc -l < "${cosign_test_log}" | tr -d ' ')" -eq 5 ] \
  || die "release verifier did not verify one bundle and four application images"
grep -q -- "--certificate-github-workflow-sha ${test_commit}" "${cosign_test_log}" \
  || die "release verifier does not bind Sigstore certificates to the signed commit"
grep -q -- '--annotations release.component=worker' "${cosign_test_log}" \
  || die "release verifier does not bind image signatures to their component"
if PATH="${release_fixture_dir}/bin:${PATH}" \
  COSIGN_TEST_LOG="${cosign_test_log}" \
  RELEASE_VERSION=1.2.3 RELEASE_GIT_COMMIT="${test_commit}" \
  RELEASE_GITHUB_REPOSITORY=Mino-Zhang/executive-ai-secretary \
  EXPECTED_ALEMBIC_HEAD="${baseline_alembic_head}" \
  WEB_IMAGE="${test_web}" API_IMAGE="${test_api}" WORKER_IMAGE="${test_worker}" \
  HERMES_IMAGE="${test_hermes}" \
  POSTGRES_IMAGE="docker.io/library/postgres@sha256:${test_digest}" \
  NGINX_IMAGE="${test_nginx}" FILE_TOOL_IMAGE="${test_file_tool}" \
    "${SCRIPT_DIR}/verify-release-bundle.sh" \
    "${release_fixture_dir}/release-bundle.json" \
    "${release_fixture_dir}/release-bundle.sigstore.json" >/dev/null 2>&1; then
  die "release verifier accepted an infrastructure image outside the signed bundle"
fi
if PATH="${release_fixture_dir}/bin:${PATH}" \
  COSIGN_TEST_LOG="${cosign_test_log}" \
  RELEASE_VERSION=1.2.3 RELEASE_GIT_COMMIT="${test_commit}" \
  RELEASE_GITHUB_REPOSITORY=Mino-Zhang/executive-ai-secretary \
  EXPECTED_ALEMBIC_HEAD="${baseline_alembic_head}" \
  WEB_IMAGE="${test_web}" API_IMAGE="${test_api}" \
  WORKER_IMAGE="ghcr.io/mino-zhang/executive-ai-worker@sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc" \
  HERMES_IMAGE="${test_hermes}" \
  POSTGRES_IMAGE="${test_postgres}" NGINX_IMAGE="${test_nginx}" \
  FILE_TOOL_IMAGE="${test_file_tool}" \
    "${SCRIPT_DIR}/verify-release-bundle.sh" \
    "${release_fixture_dir}/release-bundle.json" \
    "${release_fixture_dir}/release-bundle.sigstore.json" >/dev/null 2>&1; then
  die "release verifier accepted an application image from a different release bundle"
fi
if PATH="${release_fixture_dir}/bin:${PATH}" \
  COSIGN_TEST_LOG="${cosign_test_log}" \
  RELEASE_VERSION=1.2.3 RELEASE_GIT_COMMIT="${test_commit}" \
  RELEASE_GITHUB_REPOSITORY=Mino-Zhang/executive-ai-secretary \
  EXPECTED_ALEMBIC_HEAD=dddddddddddd \
  WEB_IMAGE="${test_web}" API_IMAGE="${test_api}" WORKER_IMAGE="${test_worker}" \
  HERMES_IMAGE="${test_hermes}" \
  POSTGRES_IMAGE="${test_postgres}" NGINX_IMAGE="${test_nginx}" \
  FILE_TOOL_IMAGE="${test_file_tool}" \
    "${SCRIPT_DIR}/verify-release-bundle.sh" \
    "${release_fixture_dir}/release-bundle.json" \
    "${release_fixture_dir}/release-bundle.sigstore.json" >/dev/null 2>&1; then
  die "release verifier accepted an Alembic head outside the signed bundle"
fi

restore_script="${SCRIPT_DIR}/restore.sh"
compatibility_line="$(grep -n 'executive_ai_api.migration_compatibility' "${restore_script}" | head -n 1 | cut -d: -f1)"
destructive_restore_line="$(grep -n 'pg_restore --username' "${restore_script}" | head -n 1 | cut -d: -f1)"
if [ -z "${compatibility_line}" ] || [ -z "${destructive_restore_line}" ] \
  || [ "${compatibility_line}" -ge "${destructive_restore_line}" ]; then
  die "restore does not reject incompatible migrations before destructive pg_restore"
fi
for restore_step in db-role-init migrate db-permissions restored_revision; do
  grep -q "${restore_step}" "${restore_script}" \
    || die "restore is missing required migration/permission step: ${restore_step}"
done

# These nginx variables are intentionally matched literally.
# shellcheck disable=SC2016
[ "$(grep -c 'proxy_set_header X-Forwarded-For \$remote_addr;' "${REPO_ROOT}/deploy/nginx/conf.d/default.conf")" -eq 3 ] \
  || die "gateway must overwrite, not append, untrusted X-Forwarded-For headers"
# shellcheck disable=SC2016
if grep -q '\$proxy_add_x_forwarded_for' "${REPO_ROOT}/deploy/nginx/conf.d/default.conf"; then
  die "gateway preserves caller-supplied X-Forwarded-For values"
fi

docker run --rm \
  --add-host api:127.0.0.1 \
  --add-host web:127.0.0.1 \
  --volume "${REPO_ROOT}/deploy/nginx/nginx.conf:/etc/nginx/nginx.conf:ro" \
  --volume "${REPO_ROOT}/deploy/nginx/conf.d/default.conf:/etc/nginx/conf.d/default.conf:ro" \
  nginx:1.27-alpine nginx -t

info "Infrastructure static checks passed."
