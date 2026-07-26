#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
temporary_page="$(mktemp)"

cleanup() {
  cp "${temporary_page}" "${REPO_ROOT}/app/page.tsx"
  rm -f "${temporary_page}"
}
trap cleanup EXIT INT TERM

cp "${REPO_ROOT}/app/page.tsx" "${temporary_page}"
cp "${REPO_ROOT}/app/page.production.tsx" "${REPO_ROOT}/app/page.tsx"

cd "${REPO_ROOT}"
rm -rf "${REPO_ROOT}/dist"
NEXT_PUBLIC_APP_MODE=production NEXT_PUBLIC_API_BASE_URL=/api/v1 npm run build
node scripts/assert-production-artifact.mjs dist
