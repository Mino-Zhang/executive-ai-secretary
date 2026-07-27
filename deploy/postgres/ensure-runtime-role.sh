#!/usr/bin/env sh
set -eu

owner_password_file="${POSTGRES_OWNER_PASSWORD_FILE:-/run/secrets/postgres_password}"
migrator_password_file="${POSTGRES_MIGRATOR_PASSWORD_FILE:-/run/secrets/postgres_migrator_password}"
runtime_password_file="${POSTGRES_RUNTIME_PASSWORD_FILE:-/run/secrets/postgres_runtime_password}"
backup_password_file="${POSTGRES_BACKUP_PASSWORD_FILE:-/run/secrets/postgres_backup_password}"
api_password_file="${POSTGRES_API_PASSWORD_FILE:-/run/secrets/postgres_api_password}"
assistant_worker_password_file="${POSTGRES_ASSISTANT_WORKER_PASSWORD_FILE:-/run/secrets/postgres_assistant_worker_password}"
file_worker_password_file="${POSTGRES_FILE_WORKER_PASSWORD_FILE:-/run/secrets/postgres_file_worker_password}"
ingestion_password_file="${POSTGRES_INGESTION_PASSWORD_FILE:-/run/secrets/postgres_ingestion_password}"
scheduler_password_file="${POSTGRES_SCHEDULER_PASSWORD_FILE:-/run/secrets/postgres_scheduler_password}"
mcp_password_file="${POSTGRES_MCP_PASSWORD_FILE:-/run/secrets/postgres_mcp_password}"

role_variables="POSTGRES_USER POSTGRES_MIGRATOR_USER POSTGRES_RUNTIME_USER POSTGRES_BACKUP_USER POSTGRES_API_USER POSTGRES_ASSISTANT_WORKER_USER POSTGRES_FILE_WORKER_USER POSTGRES_INGESTION_USER POSTGRES_SCHEDULER_USER POSTGRES_MCP_USER"
for variable in ${role_variables} POSTGRES_DB; do
  value="$(printenv "${variable}" 2>/dev/null || true)"
  case "${value}" in
    ''|*[!A-Za-z0-9_]*)
      printf '%s is missing or unsafe\n' "${variable}" >&2
      exit 64
      ;;
  esac
done

seen_roles=""
for variable in ${role_variables}; do
  value="$(printenv "${variable}")"
  case " ${seen_roles} " in
    *" ${value} "*)
      printf 'database role names must be distinct\n' >&2
      exit 64
      ;;
  esac
  seen_roles="${seen_roles} ${value}"
done

for secret_file in \
  "${owner_password_file}" \
  "${migrator_password_file}" \
  "${runtime_password_file}" \
  "${backup_password_file}" \
  "${api_password_file}" \
  "${assistant_worker_password_file}" \
  "${file_worker_password_file}" \
  "${ingestion_password_file}" \
  "${scheduler_password_file}" \
  "${mcp_password_file}"; do
  [ -s "${secret_file}" ] || { printf 'database password secret is missing\n' >&2; exit 66; }
done

export PGPASSWORD
PGPASSWORD="$(cat "${owner_password_file}")"
migrator_password="$(cat "${migrator_password_file}")"
runtime_password="$(cat "${runtime_password_file}")"
backup_password="$(cat "${backup_password_file}")"
api_password="$(cat "${api_password_file}")"
assistant_worker_password="$(cat "${assistant_worker_password_file}")"
file_worker_password="$(cat "${file_worker_password_file}")"
ingestion_password="$(cat "${ingestion_password_file}")"
scheduler_password="$(cat "${scheduler_password_file}")"
mcp_password="$(cat "${mcp_password_file}")"

psql --host postgres --username "${POSTGRES_USER}" --dbname "${POSTGRES_DB}" \
  --set ON_ERROR_STOP=1 \
  --set owner_user="${POSTGRES_USER}" \
  --set migrator_user="${POSTGRES_MIGRATOR_USER}" \
  --set runtime_user="${POSTGRES_RUNTIME_USER}" \
  --set backup_user="${POSTGRES_BACKUP_USER}" \
  --set api_user="${POSTGRES_API_USER}" \
  --set assistant_worker_user="${POSTGRES_ASSISTANT_WORKER_USER}" \
  --set file_worker_user="${POSTGRES_FILE_WORKER_USER}" \
  --set ingestion_user="${POSTGRES_INGESTION_USER}" \
  --set scheduler_user="${POSTGRES_SCHEDULER_USER}" \
  --set mcp_user="${POSTGRES_MCP_USER}" \
  --set migrator_password="${migrator_password}" \
  --set runtime_password="${runtime_password}" \
  --set backup_password="${backup_password}" \
  --set api_password="${api_password}" \
  --set assistant_worker_password="${assistant_worker_password}" \
  --set file_worker_password="${file_worker_password}" \
  --set ingestion_password="${ingestion_password}" \
  --set scheduler_password="${scheduler_password}" \
  --set mcp_password="${mcp_password}" <<'SQL'
CREATE EXTENSION IF NOT EXISTS vector;

SELECT format(
  'CREATE ROLE %I LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS',
  role_name,
  role_password
)
FROM (
  VALUES
    (:'migrator_user', :'migrator_password'),
    (:'runtime_user', :'runtime_password'),
    (:'backup_user', :'backup_password'),
    (:'api_user', :'api_password'),
    (:'assistant_worker_user', :'assistant_worker_password'),
    (:'file_worker_user', :'file_worker_password'),
    (:'ingestion_user', :'ingestion_password'),
    (:'scheduler_user', :'scheduler_password'),
    (:'mcp_user', :'mcp_password')
) AS roles(role_name, role_password)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name)
\gexec

SELECT format(
  'ALTER ROLE %I WITH LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS',
  role_name,
  role_password
)
FROM (
  VALUES
    (:'migrator_user', :'migrator_password'),
    (:'runtime_user', :'runtime_password'),
    (:'backup_user', :'backup_password'),
    (:'api_user', :'api_password'),
    (:'assistant_worker_user', :'assistant_worker_password'),
    (:'file_worker_user', :'file_worker_password'),
    (:'ingestion_user', :'ingestion_password'),
    (:'scheduler_user', :'scheduler_password'),
    (:'mcp_user', :'mcp_password')
) AS roles(role_name, role_password)
\gexec

SELECT format('REVOKE CONNECT ON DATABASE %I FROM PUBLIC', current_database())
\gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO %I', current_database(), role_name)
FROM (
  VALUES
    (:'migrator_user'),
    (:'runtime_user'),
    (:'backup_user'),
    (:'api_user'),
    (:'assistant_worker_user'),
    (:'file_worker_user'),
    (:'ingestion_user'),
    (:'scheduler_user'),
    (:'mcp_user')
) AS roles(role_name)
\gexec

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
SELECT format('ALTER SCHEMA public OWNER TO %I', :'migrator_user')
\gexec
SELECT format('GRANT USAGE, CREATE ON SCHEMA public TO %I', :'migrator_user')
\gexec

-- Existing installations initially owned objects with the bootstrap owner. Transfer only
-- application-schema objects; the database and bootstrap owner remain recovery controls.
SELECT format('ALTER TABLE %I.%I OWNER TO %I', n.nspname, c.relname, :'migrator_user')
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
  AND pg_get_userbyid(c.relowner) <> :'migrator_user'
\gexec
SELECT format('ALTER SEQUENCE %I.%I OWNER TO %I', n.nspname, c.relname, :'migrator_user')
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind = 'S'
  AND pg_get_userbyid(c.relowner) <> :'migrator_user'
\gexec
SELECT format('ALTER VIEW %I.%I OWNER TO %I', n.nspname, c.relname, :'migrator_user')
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind = 'v'
  AND pg_get_userbyid(c.relowner) <> :'migrator_user'
\gexec
SELECT format('ALTER MATERIALIZED VIEW %I.%I OWNER TO %I', n.nspname, c.relname, :'migrator_user')
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind = 'm'
  AND pg_get_userbyid(c.relowner) <> :'migrator_user'
\gexec
SELECT format('ALTER FOREIGN TABLE %I.%I OWNER TO %I', n.nspname, c.relname, :'migrator_user')
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind = 'f'
  AND pg_get_userbyid(c.relowner) <> :'migrator_user'
\gexec
SELECT format(
  'ALTER FUNCTION %I.%I(%s) OWNER TO %I',
  n.nspname,
  p.proname,
  pg_get_function_identity_arguments(p.oid),
  :'migrator_user'
)
FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname = 'public' AND pg_get_userbyid(p.proowner) <> :'migrator_user'
\gexec
SELECT format('ALTER TYPE %I.%I OWNER TO %I', n.nspname, t.typname, :'migrator_user')
FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace
WHERE n.nspname = 'public' AND t.typtype IN ('e', 'd')
  AND pg_get_userbyid(t.typowner) <> :'migrator_user'
\gexec

SELECT format('REVOKE ALL ON SCHEMA public FROM %I', role_name)
FROM (
  VALUES
    (:'runtime_user'),
    (:'backup_user'),
    (:'api_user'),
    (:'assistant_worker_user'),
    (:'file_worker_user'),
    (:'ingestion_user'),
    (:'scheduler_user'),
    (:'mcp_user')
) AS roles(role_name)
\gexec
SELECT format('GRANT USAGE ON SCHEMA public TO %I', role_name)
FROM (
  VALUES
    (:'runtime_user'),
    (:'backup_user'),
    (:'api_user'),
    (:'assistant_worker_user'),
    (:'file_worker_user'),
    (:'ingestion_user'),
    (:'scheduler_user'),
    (:'mcp_user')
) AS roles(role_name)
\gexec

SELECT format(
  'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO %I',
  object_owner,
  :'runtime_user'
)
FROM (VALUES (:'owner_user'), (:'migrator_user')) AS owners(object_owner)
\gexec
SELECT format(
  'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO %I',
  object_owner,
  :'runtime_user'
)
FROM (VALUES (:'owner_user'), (:'migrator_user')) AS owners(object_owner)
\gexec
SELECT format(
  'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public GRANT SELECT ON TABLES TO %I',
  object_owner,
  :'backup_user'
)
FROM (VALUES (:'owner_user'), (:'migrator_user')) AS owners(object_owner)
\gexec
SELECT format(
  'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public GRANT SELECT ON SEQUENCES TO %I',
  object_owner,
  :'backup_user'
)
FROM (VALUES (:'owner_user'), (:'migrator_user')) AS owners(object_owner)
\gexec
SELECT format(
  'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO %I',
  object_owner,
  :'api_user'
)
FROM (VALUES (:'owner_user'), (:'migrator_user')) AS owners(object_owner)
\gexec
SELECT format(
  'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO %I',
  object_owner,
  :'api_user'
)
FROM (VALUES (:'owner_user'), (:'migrator_user')) AS owners(object_owner)
\gexec

SELECT format('REVOKE ALL ON ALL TABLES IN SCHEMA public FROM %I', :'runtime_user')
\gexec
SELECT format('REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM %I', :'runtime_user')
\gexec
SELECT format('GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO %I', :'runtime_user')
\gexec
SELECT format('GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO %I', :'runtime_user')
\gexec
SELECT format('REVOKE ALL ON ALL TABLES IN SCHEMA public FROM %I', :'backup_user')
\gexec
SELECT format('REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM %I', :'backup_user')
\gexec
SELECT format('GRANT SELECT ON ALL TABLES IN SCHEMA public TO %I', :'backup_user')
\gexec
SELECT format('GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO %I', :'backup_user')
\gexec

-- Normal services never share the compatibility runtime credential. Rebuild their
-- table-level grants deterministically after every migration so a newly-added table
-- is not exposed merely because it exists in the public application schema.
SELECT format('REVOKE ALL ON ALL TABLES IN SCHEMA public FROM %I', role_name)
FROM (
  VALUES
    (:'api_user'),
    (:'assistant_worker_user'),
    (:'file_worker_user'),
    (:'ingestion_user'),
    (:'scheduler_user'),
    (:'mcp_user')
) AS roles(role_name)
\gexec
SELECT format('REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM %I', role_name)
FROM (
  VALUES
    (:'api_user'),
    (:'assistant_worker_user'),
    (:'file_worker_user'),
    (:'ingestion_user'),
    (:'scheduler_user'),
    (:'mcp_user')
) AS roles(role_name)
\gexec

SELECT format('GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO %I', :'api_user')
\gexec
SELECT format('GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO %I', role_name)
FROM (
  VALUES
    (:'api_user'),
    (:'assistant_worker_user'),
    (:'file_worker_user'),
    (:'ingestion_user'),
    (:'scheduler_user')
) AS roles(role_name)
\gexec

-- Read access is explicit. The MCP role receives only the facts required by
-- controlled business tools and cannot read identities, sessions, files or secrets.
WITH grants(role_name, table_name) AS (
  VALUES
    (:'mcp_user', 'organization_units'),
    (:'mcp_user', 'data_domain_status'),
    (:'mcp_user', 'mcp_tool_configs'),
    (:'mcp_user', 'dim_customer'),
    (:'mcp_user', 'fact_opportunity'),
    (:'mcp_user', 'fact_delivery'),
    (:'mcp_user', 'fact_finance_collection'),
    (:'mcp_user', 'fact_target'),
    (:'mcp_user', 'daily_snapshot'),

    (:'scheduler_user', 'enterprises'),
    (:'scheduler_user', 'organization_units'),
    (:'scheduler_user', 'data_sources'),
    (:'scheduler_user', 'scheduled_tasks'),
    (:'scheduler_user', 'schedule_runs'),
    (:'scheduler_user', 'jobs'),

    (:'assistant_worker_user', 'enterprises'),
    (:'assistant_worker_user', 'organization_units'),
    (:'assistant_worker_user', 'users'),
    (:'assistant_worker_user', 'data_scope_grants'),
    (:'assistant_worker_user', 'conversations'),
    (:'assistant_worker_user', 'messages'),
    (:'assistant_worker_user', 'message_runs'),
    (:'assistant_worker_user', 'files'),
    (:'assistant_worker_user', 'conversation_files'),
    (:'assistant_worker_user', 'file_extractions'),
    (:'assistant_worker_user', 'file_chunks'),
    (:'assistant_worker_user', 'jobs'),
    (:'assistant_worker_user', 'job_attempts'),
    (:'assistant_worker_user', 'schedule_runs'),
    (:'assistant_worker_user', 'model_provider_configs'),
    (:'assistant_worker_user', 'mcp_tool_configs'),
    (:'assistant_worker_user', 'memories'),
    (:'assistant_worker_user', 'message_routes'),
    (:'assistant_worker_user', 'clarifications'),
    (:'assistant_worker_user', 'message_evidence'),
    (:'assistant_worker_user', 'audit_events'),
    (:'assistant_worker_user', 'audit_chain_heads'),

    (:'file_worker_user', 'enterprises'),
    (:'file_worker_user', 'organization_units'),
    (:'file_worker_user', 'users'),
    (:'file_worker_user', 'data_scope_grants'),
    (:'file_worker_user', 'files'),
    (:'file_worker_user', 'file_extractions'),
    (:'file_worker_user', 'file_chunks'),
    (:'file_worker_user', 'jobs'),
    (:'file_worker_user', 'job_attempts'),
    (:'file_worker_user', 'schedule_runs'),
    (:'file_worker_user', 'audit_events'),
    (:'file_worker_user', 'audit_chain_heads'),

    (:'ingestion_user', 'enterprises'),
    (:'ingestion_user', 'organization_units'),
    (:'ingestion_user', 'users'),
    (:'ingestion_user', 'data_scope_grants'),
    (:'ingestion_user', 'data_sources'),
    (:'ingestion_user', 'jobs'),
    (:'ingestion_user', 'job_attempts'),
    (:'ingestion_user', 'schedule_runs'),
    (:'ingestion_user', 'data_sync_runs'),
    (:'ingestion_user', 'data_domain_status'),
    (:'ingestion_user', 'source_checkpoints'),
    (:'ingestion_user', 'dim_person'),
    (:'ingestion_user', 'dim_customer'),
    (:'ingestion_user', 'fact_opportunity'),
    (:'ingestion_user', 'fact_delivery'),
    (:'ingestion_user', 'fact_finance_collection'),
    (:'ingestion_user', 'fact_target'),
    (:'ingestion_user', 'daily_snapshot'),
    (:'ingestion_user', 'audit_events'),
    (:'ingestion_user', 'audit_chain_heads')
)
SELECT format('GRANT SELECT ON TABLE public.%I TO %I', table_name, role_name)
FROM grants
WHERE to_regclass(format('public.%I', table_name)) IS NOT NULL
\gexec

-- Mutation grants are bounded to the tables owned by each process. Workers can
-- lease and finish jobs but cannot modify user/session/configuration records.
WITH grants(role_name, privileges, table_name) AS (
  VALUES
    (:'scheduler_user', 'INSERT, UPDATE', 'scheduled_tasks'),
    (:'scheduler_user', 'INSERT, UPDATE', 'schedule_runs'),
    (:'scheduler_user', 'INSERT, UPDATE', 'jobs'),

    (:'assistant_worker_user', 'UPDATE', 'jobs'),
    (:'assistant_worker_user', 'INSERT, UPDATE', 'job_attempts'),
    (:'assistant_worker_user', 'UPDATE', 'schedule_runs'),
    (:'assistant_worker_user', 'UPDATE', 'messages'),
    (:'assistant_worker_user', 'INSERT, UPDATE', 'message_runs'),
    (:'assistant_worker_user', 'INSERT, UPDATE', 'message_routes'),
    (:'assistant_worker_user', 'INSERT, UPDATE', 'clarifications'),
    (:'assistant_worker_user', 'INSERT, UPDATE', 'message_evidence'),
    (:'assistant_worker_user', 'INSERT', 'audit_events'),
    (:'assistant_worker_user', 'INSERT, UPDATE', 'audit_chain_heads'),

    (:'file_worker_user', 'UPDATE', 'jobs'),
    (:'file_worker_user', 'INSERT, UPDATE', 'job_attempts'),
    (:'file_worker_user', 'UPDATE', 'schedule_runs'),
    (:'file_worker_user', 'UPDATE', 'file_extractions'),
    (:'file_worker_user', 'INSERT, DELETE', 'file_chunks'),
    (:'file_worker_user', 'INSERT', 'audit_events'),
    (:'file_worker_user', 'INSERT, UPDATE', 'audit_chain_heads'),

    (:'ingestion_user', 'UPDATE', 'jobs'),
    (:'ingestion_user', 'INSERT, UPDATE', 'job_attempts'),
    (:'ingestion_user', 'UPDATE', 'schedule_runs'),
    (:'ingestion_user', 'INSERT, UPDATE', 'organization_units'),
    (:'ingestion_user', 'INSERT, UPDATE', 'data_sync_runs'),
    (:'ingestion_user', 'INSERT, UPDATE', 'data_domain_status'),
    (:'ingestion_user', 'INSERT, UPDATE', 'source_checkpoints'),
    (:'ingestion_user', 'INSERT, UPDATE', 'dim_person'),
    (:'ingestion_user', 'INSERT, UPDATE', 'dim_customer'),
    (:'ingestion_user', 'INSERT, UPDATE', 'fact_opportunity'),
    (:'ingestion_user', 'INSERT, UPDATE', 'fact_delivery'),
    (:'ingestion_user', 'INSERT, UPDATE', 'fact_finance_collection'),
    (:'ingestion_user', 'INSERT, UPDATE', 'fact_target'),
    (:'ingestion_user', 'INSERT, DELETE', 'daily_snapshot'),
    (:'ingestion_user', 'INSERT', 'audit_events'),
    (:'ingestion_user', 'INSERT, UPDATE', 'audit_chain_heads')
)
SELECT format('GRANT %s ON TABLE public.%I TO %I', privileges, table_name, role_name)
FROM grants
WHERE to_regclass(format('public.%I', table_name)) IS NOT NULL
\gexec

SELECT format('REVOKE UPDATE, DELETE, TRUNCATE ON TABLE public.audit_events FROM %I', :'runtime_user')
WHERE to_regclass('public.audit_events') IS NOT NULL
\gexec
SELECT format('REVOKE DELETE, TRUNCATE ON TABLE public.audit_chain_heads FROM %I', :'runtime_user')
WHERE to_regclass('public.audit_chain_heads') IS NOT NULL
\gexec
SELECT format('REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON TABLE public.alembic_version FROM %I', :'runtime_user')
WHERE to_regclass('public.alembic_version') IS NOT NULL
\gexec

SELECT format('REVOKE UPDATE, DELETE, TRUNCATE ON TABLE public.audit_events FROM %I', role_name)
FROM (
  VALUES
    (:'api_user'),
    (:'assistant_worker_user'),
    (:'file_worker_user'),
    (:'ingestion_user')
) AS roles(role_name)
WHERE to_regclass('public.audit_events') IS NOT NULL
\gexec
SELECT format('REVOKE DELETE, TRUNCATE ON TABLE public.audit_chain_heads FROM %I', role_name)
FROM (
  VALUES
    (:'api_user'),
    (:'assistant_worker_user'),
    (:'file_worker_user'),
    (:'ingestion_user')
) AS roles(role_name)
WHERE to_regclass('public.audit_chain_heads') IS NOT NULL
\gexec
SELECT format('REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON TABLE public.alembic_version FROM %I', role_name)
FROM (
  VALUES
    (:'api_user'),
    (:'assistant_worker_user'),
    (:'file_worker_user'),
    (:'ingestion_user'),
    (:'scheduler_user'),
    (:'mcp_user')
) AS roles(role_name)
WHERE to_regclass('public.alembic_version') IS NOT NULL
\gexec

SELECT count(*) = 9 AS restricted_roles_verified
FROM pg_roles
WHERE rolname IN (
  :'migrator_user',
  :'runtime_user',
  :'backup_user',
  :'api_user',
  :'assistant_worker_user',
  :'file_worker_user',
  :'ingestion_user',
  :'scheduler_user',
  :'mcp_user'
)
  AND NOT (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls)
\gset
\if :restricted_roles_verified
\else
  \echo 'database role privilege verification failed'
  \quit 3
\endif
SQL

unset PGPASSWORD migrator_password runtime_password backup_password api_password \
  assistant_worker_password file_worker_password ingestion_password scheduler_password mcp_password
