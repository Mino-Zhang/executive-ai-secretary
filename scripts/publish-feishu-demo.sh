#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/runtime.sh
. "${SCRIPT_DIR}/lib/runtime.sh"

environment="${1:-}"
enterprise_slug="${2:-}"
[ "${environment}" = "local-demo" ] || die "Feishu demo publishing is permitted only for local-demo"
[ -n "${enterprise_slug}" ] || die "enterprise slug is required"
load_runtime_environment "${environment}"
require_secret_files "${environment}"
[ -n "${FEISHU_APP_ID:-}" ] || die "FEISHU_APP_ID is not configured"
[ -n "${FEISHU_BITABLE_APP_TOKEN:-}" ] || die "FEISHU_BITABLE_APP_TOKEN is not configured"
[ -n "${FEISHU_BITABLE_TABLE_ID:-}" ] || die "FEISHU_BITABLE_TABLE_ID is not configured"
[ -s "$(runtime_dir_for "${environment}")/secrets/feishu_provisioning_secret" ] \
  || die "the one-time Feishu provisioning credential is empty"

DEMO_ENTERPRISE_SLUG="${enterprise_slug}" \
  compose "${environment}" --profile feishu-provision run --rm publish-feishu-demo
info "Simulated SA opportunities published to the configured Feishu Bitable."
