#!/usr/bin/env sh
set -eu

owner_password_file="${POSTGRES_OWNER_PASSWORD_FILE:-/run/secrets/postgres_password}"
migrator_password_file="${POSTGRES_MIGRATOR_PASSWORD_FILE:-/run/secrets/postgres_migrator_password}"
runtime_password_file="${POSTGRES_RUNTIME_PASSWORD_FILE:-/run/secrets/postgres_runtime_password}"
backup_password_file="${POSTGRES_BACKUP_PASSWORD_FILE:-/run/secrets/postgres_backup_password}"

for variable in POSTGRES_USER POSTGRES_MIGRATOR_USER POSTGRES_RUNTIME_USER POSTGRES_BACKUP_USER POSTGRES_DB; do
  value="$(printenv "${variable}" 2>/dev/null || true)"
  case "${value}" in
    ''|*[!A-Za-z0-9_]*)
      printf '%s is missing or unsafe\n' "${variable}" >&2
      exit 64
      ;;
  esac
done
[ "${POSTGRES_USER}" != "${POSTGRES_MIGRATOR_USER}" ] \
  && [ "${POSTGRES_USER}" != "${POSTGRES_RUNTIME_USER}" ] \
  && [ "${POSTGRES_USER}" != "${POSTGRES_BACKUP_USER}" ] \
  && [ "${POSTGRES_MIGRATOR_USER}" != "${POSTGRES_RUNTIME_USER}" ] \
  && [ "${POSTGRES_MIGRATOR_USER}" != "${POSTGRES_BACKUP_USER}" ] \
  && [ "${POSTGRES_RUNTIME_USER}" != "${POSTGRES_BACKUP_USER}" ] \
  || { printf 'database role names must be distinct\n' >&2; exit 64; }

for secret_file in "${owner_password_file}" "${migrator_password_file}" "${runtime_password_file}" "${backup_password_file}"; do
  [ -s "${secret_file}" ] || { printf 'database password secret is missing\n' >&2; exit 66; }
done

export PGPASSWORD
PGPASSWORD="$(cat "${owner_password_file}")"
migrator_password="$(cat "${migrator_password_file}")"
runtime_password="$(cat "${runtime_password_file}")"
backup_password="$(cat "${backup_password_file}")"

psql --host postgres --username "${POSTGRES_USER}" --dbname "${POSTGRES_DB}" \
  --set ON_ERROR_STOP=1 \
  --set owner_user="${POSTGRES_USER}" \
  --set migrator_user="${POSTGRES_MIGRATOR_USER}" \
  --set runtime_user="${POSTGRES_RUNTIME_USER}" \
  --set backup_user="${POSTGRES_BACKUP_USER}" \
  --set migrator_password="${migrator_password}" \
  --set runtime_password="${runtime_password}" \
  --set backup_password="${backup_password}" <<'SQL'
SELECT format(
  'CREATE ROLE %I LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS',
  role_name,
  role_password
)
FROM (
  VALUES
    (:'migrator_user', :'migrator_password'),
    (:'runtime_user', :'runtime_password'),
    (:'backup_user', :'backup_password')
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
    (:'backup_user', :'backup_password')
) AS roles(role_name, role_password)
\gexec

SELECT format('REVOKE CONNECT ON DATABASE %I FROM PUBLIC', current_database())
\gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO %I', current_database(), role_name)
FROM (VALUES (:'migrator_user'), (:'runtime_user'), (:'backup_user')) AS roles(role_name)
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
FROM (VALUES (:'runtime_user'), (:'backup_user')) AS roles(role_name)
\gexec
SELECT format('GRANT USAGE ON SCHEMA public TO %I', role_name)
FROM (VALUES (:'runtime_user'), (:'backup_user')) AS roles(role_name)
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

SELECT format('REVOKE UPDATE, DELETE, TRUNCATE ON TABLE public.audit_events FROM %I', :'runtime_user')
WHERE to_regclass('public.audit_events') IS NOT NULL
\gexec
SELECT format('REVOKE DELETE, TRUNCATE ON TABLE public.audit_chain_heads FROM %I', :'runtime_user')
WHERE to_regclass('public.audit_chain_heads') IS NOT NULL
\gexec
SELECT format('REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON TABLE public.alembic_version FROM %I', :'runtime_user')
WHERE to_regclass('public.alembic_version') IS NOT NULL
\gexec

SELECT count(*) = 3 AS restricted_roles_verified
FROM pg_roles
WHERE rolname IN (:'migrator_user', :'runtime_user', :'backup_user')
  AND NOT (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls)
\gset
\if :restricted_roles_verified
\else
  \echo 'database role privilege verification failed'
  \quit 3
\endif
SQL

unset PGPASSWORD migrator_password runtime_password backup_password
