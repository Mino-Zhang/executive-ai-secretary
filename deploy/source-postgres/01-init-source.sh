#!/usr/bin/env bash
set -euo pipefail

reader_password="$(cat /run/secrets/source_reader_password)"
writer_password="$(cat /run/secrets/source_writer_password)"

psql --set=ON_ERROR_STOP=1 --username "${POSTGRES_USER}" --dbname "${POSTGRES_DB}" \
  --set=reader_password="${reader_password}" --set=writer_password="${writer_password}" <<'SQL'
SELECT format('CREATE ROLE source_reader LOGIN PASSWORD %L', :'reader_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'source_reader')\gexec
SELECT format('CREATE ROLE source_writer LOGIN PASSWORD %L', :'writer_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'source_writer')\gexec

ALTER ROLE source_reader NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION;
ALTER ROLE source_writer NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION;
ALTER ROLE source_reader SET default_transaction_read_only = on;
SQL

psql --set=ON_ERROR_STOP=1 --username "${POSTGRES_USER}" --dbname "${POSTGRES_DB}" \
  --file /opt/executive-ai-source/standard-ods.sql

psql --set=ON_ERROR_STOP=1 --username "${POSTGRES_USER}" --dbname "${POSTGRES_DB}" <<'SQL'
REVOKE ALL ON SCHEMA executive_source FROM PUBLIC;
GRANT USAGE ON SCHEMA executive_source TO source_reader, source_writer;

GRANT SELECT ON TABLE
  executive_source.ods_schema_version,
  executive_source.source_batches,
  executive_source.ods_organization_unit,
  executive_source.ods_person,
  executive_source.ods_customer,
  executive_source.ods_opportunity,
  executive_source.ods_delivery,
  executive_source.ods_collection,
  executive_source.ods_target
TO source_reader;

GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE
  executive_source.source_batches,
  executive_source.ods_organization_unit,
  executive_source.ods_person,
  executive_source.ods_customer,
  executive_source.ods_opportunity,
  executive_source.ods_delivery,
  executive_source.ods_collection,
  executive_source.ods_target
TO source_writer;
GRANT SELECT ON TABLE executive_source.ods_schema_version TO source_writer;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA executive_source TO source_writer;

ALTER DEFAULT PRIVILEGES IN SCHEMA executive_source REVOKE ALL ON TABLES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA executive_source REVOKE ALL ON SEQUENCES FROM PUBLIC;
SQL
