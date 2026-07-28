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

# New managed source databases start directly on the production 3.0 contract.
# The legacy 2.0 contract is intentionally not provisioned after the live
# three-table cut-over.
psql --set=ON_ERROR_STOP=1 --username "${POSTGRES_USER}" --dbname "${POSTGRES_DB}" \
  --file /opt/executive-ai-source/standard-ods-v3.sql

psql --set=ON_ERROR_STOP=1 --username "${POSTGRES_USER}" --dbname "${POSTGRES_DB}" <<'SQL'
REVOKE ALL ON SCHEMA executive_source_v3 FROM PUBLIC;
GRANT USAGE ON SCHEMA executive_source_v3 TO source_reader, source_writer;

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
GRANT SELECT ON TABLE executive_source_v3.ods_schema_version TO source_writer;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA executive_source_v3 TO source_writer;

ALTER DEFAULT PRIVILEGES IN SCHEMA executive_source_v3 REVOKE ALL ON TABLES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA executive_source_v3 REVOKE ALL ON SEQUENCES FROM PUBLIC;
SQL
