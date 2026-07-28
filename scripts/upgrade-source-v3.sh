#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/runtime.sh
. "${SCRIPT_DIR}/lib/runtime.sh"

environment="${1:-}"
action="${2:-apply}"

validate_environment_name "${environment}"
case "${action}" in
  apply|--check) ;;
  *) die "usage: $0 <local-demo|customer-template> [apply|--check]" ;;
esac

require_command docker
load_runtime_environment "${environment}"

if [ "${environment}" = "customer-template" ] && [ "${MANAGED_SOURCE_DB:-false}" != "true" ]; then
  die "customer-template can be upgraded by this script only when MANAGED_SOURCE_DB=true"
fi

contract_file="${REPO_ROOT}/deploy/source-postgres/standard-ods-v3.sql"
[ -f "${contract_file}" ] || die "missing ODS 3.0 contract: ${contract_file}"

container_id="$(compose "${environment}" ps -q source-postgres)"
[ -n "${container_id}" ] || die "source-postgres is not running for ${environment}"
[ "$(printf '%s\n' "${container_id}" | wc -l | tr -d ' ')" = "1" ] \
  || die "expected exactly one source-postgres container"

actual_project="$(docker inspect --format '{{ index .Config.Labels "com.docker.compose.project" }}' "${container_id}")"
actual_service="$(docker inspect --format '{{ index .Config.Labels "com.docker.compose.service" }}' "${container_id}")"
[ "${actual_project}" = "${COMPOSE_PROJECT_NAME}" ] \
  || die "container belongs to unexpected Compose project: ${actual_project}"
[ "${actual_service}" = "source-postgres" ] \
  || die "container is not the source-postgres service: ${actual_service}"

container_state="$(docker inspect --format '{{.State.Status}}' "${container_id}")"
[ "${container_state}" = "running" ] || die "source-postgres is not running"

psql_in_source() {
  docker exec -i "${container_id}" sh -ec \
    'exec psql --set=ON_ERROR_STOP=1 --no-psqlrc --tuples-only --no-align --username "$POSTGRES_USER" --dbname "$POSTGRES_DB"' \
    "$@"
}

preflight="$(psql_in_source <<'SQL'
SELECT json_build_object(
  'database', current_database(),
  'server_version_num', current_setting('server_version_num')::integer,
  'v2_schema', to_regnamespace('executive_source') IS NOT NULL,
  'v2_version', (
    SELECT schema_version FROM executive_source.ods_schema_version WHERE singleton = true
  ),
  'reader_role', EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'source_reader'),
  'writer_role', EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'source_writer'),
  'reader_privileged', coalesce((
    SELECT rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication
    FROM pg_roles WHERE rolname = 'source_reader'
  ), true),
  'writer_privileged', coalesce((
    SELECT rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication
    FROM pg_roles WHERE rolname = 'source_writer'
  ), true)
);
SQL
)"

printf '%s' "${preflight}" | grep -Eq '"server_version_num"[ ]*:[ ]*1[567][0-9]{4}' \
  || die "source PostgreSQL must be version 15, 16, or 17: ${preflight}"
printf '%s' "${preflight}" | grep -Eq '"v2_schema"[ ]*:[ ]*true' \
  || die "executive_source V2 schema is missing: ${preflight}"
printf '%s' "${preflight}" | grep -Eq '"v2_version"[ ]*:[ ]*"2.0"' \
  || die "executive_source must remain at V2 version 2.0 before shadow upgrade: ${preflight}"
printf '%s' "${preflight}" | grep -Eq '"reader_role"[ ]*:[ ]*true' \
  || die "source_reader role is missing: ${preflight}"
printf '%s' "${preflight}" | grep -Eq '"writer_role"[ ]*:[ ]*true' \
  || die "source_writer role is missing: ${preflight}"
printf '%s' "${preflight}" | grep -Eq '"reader_privileged"[ ]*:[ ]*false' \
  || die "source_reader has privileged role attributes: ${preflight}"
printf '%s' "${preflight}" | grep -Eq '"writer_privileged"[ ]*:[ ]*false' \
  || die "source_writer has privileged role attributes: ${preflight}"

info "Preflight passed for ${COMPOSE_PROJECT_NAME}/source-postgres."
info "ODS 3.0 SQL sha256: $(sha256_file "${contract_file}")"

if [ "${action}" = "--check" ]; then
  info "Check only; no schema or privilege changes were made."
  exit 0
fi

info "Applying ODS 3.0; the contract remains 3.0-validating until catalog validation passes."
psql_in_source < "${contract_file}"
info "ODS 3.0 catalog validation passed."

psql_in_source <<'SQL'
REVOKE ALL ON SCHEMA executive_source_v3 FROM PUBLIC;
GRANT USAGE ON SCHEMA executive_source_v3 TO source_reader, source_writer;

REVOKE ALL ON ALL TABLES IN SCHEMA executive_source_v3 FROM source_reader, source_writer;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA executive_source_v3 FROM source_reader, source_writer;

GRANT SELECT ON TABLE
  executive_source_v3.ods_schema_version,
  executive_source_v3.source_batches,
  executive_source_v3.source_table_bindings,
  executive_source_v3.source_validation_issues,
  executive_source_v3.source_sync_checkpoints,
  executive_source_v3.ods_opportunity,
  executive_source_v3.ods_delivery,
  executive_source_v3.ods_collection
TO source_reader;

GRANT SELECT ON TABLE executive_source_v3.ods_schema_version TO source_writer;
GRANT SELECT, INSERT, UPDATE ON TABLE executive_source_v3.source_batches TO source_writer;
GRANT SELECT, INSERT ON TABLE
  executive_source_v3.source_table_bindings,
  executive_source_v3.source_validation_issues,
  executive_source_v3.ods_opportunity,
  executive_source_v3.ods_delivery,
  executive_source_v3.ods_collection
TO source_writer;
GRANT SELECT, INSERT, UPDATE ON TABLE
  executive_source_v3.source_sync_checkpoints
TO source_writer;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA executive_source_v3 TO source_writer;

REVOKE ALL ON FUNCTION executive_source_v3.reject_source_v3_snapshot_mutation() FROM PUBLIC;

ALTER DEFAULT PRIVILEGES IN SCHEMA executive_source_v3 REVOKE ALL ON TABLES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA executive_source_v3 REVOKE ALL ON SEQUENCES FROM PUBLIC;
SQL

verification="$(psql_in_source <<'SQL'
SELECT json_build_object(
  'v2_version', (
    SELECT schema_version FROM executive_source.ods_schema_version WHERE singleton = true
  ),
  'v3_version', (
    SELECT schema_version FROM executive_source_v3.ods_schema_version WHERE singleton = true
  ),
  'v3_tables', (
    SELECT count(*)
    FROM information_schema.tables
    WHERE table_schema = 'executive_source_v3' AND table_type = 'BASE TABLE'
  ),
  'reader_can_select', has_table_privilege(
    'source_reader', 'executive_source_v3.ods_opportunity', 'SELECT'
  ),
  'reader_can_write', has_table_privilege(
    'source_reader', 'executive_source_v3.ods_opportunity', 'INSERT,UPDATE,DELETE'
  ),
  'writer_can_insert', has_table_privilege(
    'source_writer', 'executive_source_v3.ods_opportunity', 'INSERT'
  ),
  'writer_can_mutate_snapshot', has_table_privilege(
    'source_writer', 'executive_source_v3.ods_opportunity', 'UPDATE,DELETE'
  )
);
SQL
)"

printf '%s' "${verification}" | grep -Eq '"v2_version"[ ]*:[ ]*"2.0"' \
  || die "V2 contract changed unexpectedly: ${verification}"
printf '%s' "${verification}" | grep -Eq '"v3_version"[ ]*:[ ]*"3.0"' \
  || die "V3 schema version verification failed: ${verification}"
printf '%s' "${verification}" | grep -Eq '"v3_tables"[ ]*:[ ]*8' \
  || die "V3 table count verification failed: ${verification}"
printf '%s' "${verification}" | grep -Eq '"reader_can_select"[ ]*:[ ]*true' \
  || die "source_reader SELECT verification failed: ${verification}"
printf '%s' "${verification}" | grep -Eq '"reader_can_write"[ ]*:[ ]*false' \
  || die "source_reader unexpectedly has write access: ${verification}"
printf '%s' "${verification}" | grep -Eq '"writer_can_insert"[ ]*:[ ]*true' \
  || die "source_writer INSERT verification failed: ${verification}"
printf '%s' "${verification}" | grep -Eq '"writer_can_mutate_snapshot"[ ]*:[ ]*false' \
  || die "source_writer unexpectedly has snapshot mutation access: ${verification}"

info "ODS source upgrade complete: ${verification}"
info "V2 was retained. No container was recreated and no business data was deleted."
