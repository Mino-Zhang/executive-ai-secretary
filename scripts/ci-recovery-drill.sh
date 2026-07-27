#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/runtime.sh
. "${SCRIPT_DIR}/lib/runtime.sh"

if [ "${CI:-}" != "true" ] || [ "${GITHUB_ACTIONS:-}" != "true" ]; then
  die "this destructive recovery drill is restricted to a fresh GitHub Actions runner"
fi
require_command docker
require_command curl
require_command python3
require_command sudo

runtime_dir="$(runtime_dir_for local-demo)"
backup_root="${REPO_ROOT}/backups/local-demo"
[ ! -e "${runtime_dir}" ] || die "CI drill refuses an existing local-demo runtime"
[ ! -e "${backup_root}" ] || die "CI drill refuses an existing local-demo backup directory"

temporary_dir="$(mktemp -d)"
cleanup() {
  local status="$1"
  trap - EXIT INT TERM
  if [ "${status}" -ne 0 ]; then
    info "Recovery drill failed; collecting bounded service diagnostics."
    compose local-demo ps >&2 || true
    compose local-demo logs --no-color --tail=200 \
      postgres db-role-init migrate db-permissions api worker web nginx >&2 || true
  fi
  compose local-demo down --volumes --remove-orphans >/dev/null 2>&1 || true
  case "${temporary_dir}" in
    /tmp/*|/private/tmp/*) rm -rf -- "${temporary_dir}" ;;
  esac
  exit "${status}"
}
trap 'cleanup $?' EXIT
trap 'cleanup 130' INT
trap 'cleanup 143' TERM

json_field() {
  local field="$1"
  python3 -c 'import json,sys; print(json.load(sys.stdin)[sys.argv[1]])' "${field}"
}

http_status() {
  curl --silent --output /dev/null --write-out '%{http_code}' "$@"
}

admin_password="Tmp-A1!$(openssl rand -hex 18)"
executive_password="Tmp-E1!$(openssl rand -hex 18)"
executive_new_password="Final-E2!$(openssl rand -hex 18)"

"${SCRIPT_DIR}/prepare-env.sh" local-demo
# Docker Compose implements local file-backed secrets as bind mounts on Linux,
# preserving the host UID and mode. This drill is hard-restricted above to a
# fresh, single-tenant GitHub-hosted runner and uses only one-time random keys.
# Give each container identity access only to the ephemeral files it consumes.
# Database role-management containers run as PostgreSQL uid/gid 70, while the
# application and migration containers run as uid/gid 999. Shared database
# credentials are group-readable by PostgreSQL; application key rings remain
# 0400 to satisfy their stricter guard. Real environments retain 0600 ownership.
for postgres_secret in postgres_password postgres_backup_password; do
  sudo chown 70:70 "${runtime_dir}/secrets/${postgres_secret}"
  sudo chmod 0400 "${runtime_dir}/secrets/${postgres_secret}"
done
for shared_secret in postgres_migrator_password postgres_runtime_password; do
  sudo chown 999:70 "${runtime_dir}/secrets/${shared_secret}"
  sudo chmod 0440 "${runtime_dir}/secrets/${shared_secret}"
done
for app_secret in \
  session_secret \
  csrf_secret \
  file_encryption_key \
  file_encryption_key_ring \
  audit_hmac_key \
  audit_hmac_key_ring; do
  sudo chown 999:999 "${runtime_dir}/secrets/${app_secret}"
  sudo chmod 0400 "${runtime_dir}/secrets/${app_secret}"
done
"${SCRIPT_DIR}/start.sh" local-demo
printf '%s\n' "${admin_password}" | "${SCRIPT_DIR}/bootstrap-admin.sh" \
  local-demo admin@ci.invalid "CI 企业管理员" "CI 演示企业" ci-enterprise
printf '%s\n' "${executive_password}" | "${SCRIPT_DIR}/create-executive.sh" \
  local-demo ci-enterprise executive@ci.invalid "CI 董事长" enterprise
"${SCRIPT_DIR}/seed-demo.sh" local-demo ci-enterprise "SEED local-demo/ci-enterprise"

load_runtime_environment local-demo
api="${PUBLIC_BASE_URL}/api/v1"
admin_cookie="${temporary_dir}/admin.cookies"
executive_cookie="${temporary_dir}/executive.cookies"

admin_login="$(curl --fail-with-body --silent --show-error \
  --cookie-jar "${admin_cookie}" \
  --header 'Content-Type: application/json' \
  --data "{\"email\":\"admin@ci.invalid\",\"password\":\"${admin_password}\"}" \
  "${api}/auth/login")"
[ -n "$(printf '%s' "${admin_login}" | json_field csrf_token)" ] \
  || die "administrator login did not return a CSRF token"
[ "$(http_status --cookie "${admin_cookie}" "${api}/conversations")" = "403" ] \
  || die "administrator unexpectedly gained access to executive conversations"

executive_login="$(curl --fail-with-body --silent --show-error \
  --cookie-jar "${executive_cookie}" \
  --header 'Content-Type: application/json' \
  --data "{\"email\":\"executive@ci.invalid\",\"password\":\"${executive_password}\"}" \
  "${api}/auth/login")"
csrf="$(printf '%s' "${executive_login}" | json_field csrf_token)"

password_change="$(curl --fail-with-body --silent --show-error \
  --cookie "${executive_cookie}" --cookie-jar "${executive_cookie}" \
  --header 'Content-Type: application/json' \
  --header "X-CSRF-Token: ${csrf}" \
  --data "{\"current_password\":\"${executive_password}\",\"new_password\":\"${executive_new_password}\"}" \
  "${api}/auth/change-password")"
csrf="$(printf '%s' "${password_change}" | json_field csrf_token)"

baseline_conversation="$(curl --fail-with-body --silent --show-error \
  --cookie "${executive_cookie}" \
  --header 'Content-Type: application/json' \
  --header "X-CSRF-Token: ${csrf}" \
  --header 'Idempotency-Key: ci-baseline-conversation' \
  --data '{"title":"CI 恢复基线会话"}' \
  "${api}/conversations")"
baseline_conversation_id="$(printf '%s' "${baseline_conversation}" | json_field id)"

printf 'encrypted file recovery baseline\n' > "${temporary_dir}/baseline.txt"
baseline_file="$(curl --fail-with-body --silent --show-error \
  --cookie "${executive_cookie}" \
  --header "X-CSRF-Token: ${csrf}" \
  --header 'Idempotency-Key: ci-baseline-file' \
  --form "file=@${temporary_dir}/baseline.txt;type=text/plain" \
  "${api}/files")"
baseline_file_id="$(printf '%s' "${baseline_file}" | json_field id)"
curl --fail-with-body --silent --show-error --cookie "${executive_cookie}" \
  "${api}/files/${baseline_file_id}/content" --output "${temporary_dir}/downloaded-before.txt"
cmp "${temporary_dir}/baseline.txt" "${temporary_dir}/downloaded-before.txt"

backup_dir="$("${SCRIPT_DIR}/backup.sh" local-demo ci-recovery-baseline)"

transient_conversation="$(curl --fail-with-body --silent --show-error \
  --cookie "${executive_cookie}" \
  --header 'Content-Type: application/json' \
  --header "X-CSRF-Token: ${csrf}" \
  --header 'Idempotency-Key: ci-transient-conversation' \
  --data '{"title":"CI 恢复后必须消失"}' \
  "${api}/conversations")"
transient_conversation_id="$(printf '%s' "${transient_conversation}" | json_field id)"

printf 'transient file must disappear\n' > "${temporary_dir}/transient.txt"
transient_file="$(curl --fail-with-body --silent --show-error \
  --cookie "${executive_cookie}" \
  --header "X-CSRF-Token: ${csrf}" \
  --header 'Idempotency-Key: ci-transient-file' \
  --form "file=@${temporary_dir}/transient.txt;type=text/plain" \
  "${api}/files")"
transient_file_id="$(printf '%s' "${transient_file}" | json_field id)"

"${SCRIPT_DIR}/restore.sh" local-demo "${backup_dir}" "RESTORE local-demo"

curl --fail-with-body --silent --show-error --cookie "${executive_cookie}" \
  "${api}/conversations/${baseline_conversation_id}" >/dev/null
[ "$(http_status --cookie "${executive_cookie}" "${api}/conversations/${transient_conversation_id}")" = "404" ] \
  || die "post-backup conversation survived restore"
curl --fail-with-body --silent --show-error --cookie "${executive_cookie}" \
  "${api}/files/${baseline_file_id}/content" --output "${temporary_dir}/downloaded-after.txt"
cmp "${temporary_dir}/baseline.txt" "${temporary_dir}/downloaded-after.txt"
[ "$(http_status --cookie "${executive_cookie}" "${api}/files/${transient_file_id}")" = "404" ] \
  || die "post-backup file metadata survived restore"

"${SCRIPT_DIR}/smoke-test.sh" local-demo
info "CI recovery drill passed: auth, first-password change, RBAC, encrypted file I/O and restore integrity."
