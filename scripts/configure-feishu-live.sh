#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/runtime.sh
. "${SCRIPT_DIR}/lib/runtime.sh"

environment="${1:-local-demo}"
[ "${environment}" = "local-demo" ] || die "Feishu Keychain import is local-demo only"
require_command security
load_runtime_environment "${environment}"

keychain_service="${FEISHU_KEYCHAIN_SERVICE:-com.openai.codex.feishu-monitor}"
app_id="$(security find-generic-password -s "${keychain_service}" -a app-id -w 2>/dev/null || true)"
app_secret="$(security find-generic-password -s "${keychain_service}" -a app-secret -w 2>/dev/null || true)"
[ -n "${app_id}" ] || die "Feishu App ID is missing from macOS Keychain"
[ -n "${app_secret}" ] || die "Feishu App Secret is missing from macOS Keychain"
[ "${FEISHU_APP_ID:-}" = "${app_id}" ] \
  || die "runtime FEISHU_APP_ID does not match the approved Keychain application"

target="$(runtime_dir_for "${environment}")/secrets/feishu_runtime_secret"
temporary="${target}.tmp.$$"
umask 077
printf '%s' "${app_secret}" > "${temporary}"
chmod 600 "${temporary}"
mv "${temporary}" "${target}"
chmod 600 "${target}"

info "Configured the local-demo Feishu runtime credential from macOS Keychain."
info "Only the application secret was copied; user OAuth tokens were not copied."
