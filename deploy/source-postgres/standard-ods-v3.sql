-- Executive AI Researcher sanitized source contract 3.0.
--
-- This is the production contract for complete Feishu snapshots.  It remains
-- deliberately separate from the retired executive_source (2.0) schema so
-- legacy installations can be migrated without an in-place rewrite. ODS rows are
-- append-only: a record that disappears upstream is simply absent from the
-- next load_batch_id.

CREATE SCHEMA IF NOT EXISTS executive_source_v3;
SET search_path TO executive_source_v3, public;

CREATE TABLE IF NOT EXISTS ods_schema_version (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    schema_version varchar(32) NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now(),
    contract_name varchar(120) NOT NULL DEFAULT 'executive-ai-sanitized-ods-v3'
);

-- A failed or interrupted upgrade must never continue advertising itself as
-- contract 3.0.  This marker is committed before the transactional DDL and is
-- changed to 3.0 only after the catalog checks at the end of this file pass.
INSERT INTO ods_schema_version (singleton, schema_version)
VALUES (true, '3.0-validating')
ON CONFLICT (singleton) DO UPDATE
SET schema_version = EXCLUDED.schema_version,
    applied_at = now(),
    contract_name = 'executive-ai-sanitized-ods-v3';

BEGIN;

CREATE TABLE IF NOT EXISTS source_batches (
    batch_id varchar(160) PRIMARY KEY,
    source_system varchar(80) NOT NULL,
    dataset_version varchar(80) NOT NULL,
    reference_date date NOT NULL,
    source_data_as_of timestamptz NOT NULL,
    status varchar(32) NOT NULL CHECK (
        status IN ('building', 'validated', 'ready', 'rejected', 'activated', 'superseded')
    ),
    record_counts jsonb NOT NULL DEFAULT '{}'::jsonb,
    table_content_sha256 jsonb NOT NULL DEFAULT '{}'::jsonb,
    table_schema_sha256 jsonb NOT NULL DEFAULT '{}'::jsonb,
    content_sha256 char(64) NOT NULL,
    validation_result jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    validated_at timestamptz,
    completed_at timestamptz,
    activated_at timestamptz,
    CHECK (jsonb_typeof(record_counts) = 'object'),
    CHECK (jsonb_typeof(table_content_sha256) = 'object'),
    CHECK (jsonb_typeof(table_schema_sha256) = 'object'),
    CHECK (jsonb_typeof(validation_result) = 'object')
);

CREATE TABLE IF NOT EXISTS source_table_bindings (
    id bigserial PRIMARY KEY,
    load_batch_id varchar(160) NOT NULL REFERENCES source_batches(batch_id),
    domain varchar(32) NOT NULL CHECK (domain IN ('opportunity', 'delivery', 'collection')),
    source_system varchar(80) NOT NULL,
    app_token varchar(160) NOT NULL,
    table_id varchar(160) NOT NULL,
    display_name varchar(240) NOT NULL,
    field_mapping jsonb NOT NULL,
    field_types jsonb NOT NULL,
    schema_sha256 char(64) NOT NULL,
    record_count integer NOT NULL CHECK (record_count >= 0),
    validated_at timestamptz NOT NULL,
    UNIQUE (load_batch_id, domain),
    CHECK (jsonb_typeof(field_mapping) = 'object'),
    CHECK (jsonb_typeof(field_types) = 'object')
);

CREATE TABLE IF NOT EXISTS source_validation_issues (
    id bigserial PRIMARY KEY,
    load_batch_id varchar(160) NOT NULL REFERENCES source_batches(batch_id),
    severity varchar(16) NOT NULL CHECK (severity IN ('warning', 'error')),
    domain varchar(32) CHECK (domain IN ('opportunity', 'delivery', 'collection', 'batch')),
    source_record_id varchar(160),
    field_name varchar(160),
    error_code varchar(120) NOT NULL,
    message text NOT NULL,
    details jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (jsonb_typeof(details) = 'object')
);

CREATE TABLE IF NOT EXISTS source_sync_checkpoints (
    id bigserial PRIMARY KEY,
    source_system varchar(80) NOT NULL,
    domain varchar(32) NOT NULL CHECK (domain IN ('opportunity', 'delivery', 'collection')),
    app_token varchar(160) NOT NULL,
    table_id varchar(160) NOT NULL,
    last_batch_id varchar(160) REFERENCES source_batches(batch_id),
    next_page_token text,
    source_updated_at timestamptz,
    content_sha256 char(64),
    synchronized_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source_system, domain, app_token, table_id)
);

CREATE TABLE IF NOT EXISTS ods_opportunity (
    id bigserial PRIMARY KEY,
    load_batch_id varchar(160) NOT NULL REFERENCES source_batches(batch_id),
    source_system varchar(80) NOT NULL,
    source_record_id varchar(160) NOT NULL,
    source_native_record_id varchar(160) NOT NULL,
    source_updated_at timestamptz NOT NULL,
    is_deleted boolean NOT NULL DEFAULT false CHECK (is_deleted = false),
    legacy_source_record_id varchar(160),
    opportunity_code varchar(160) NOT NULL,
    organization_code varchar(160) NOT NULL,
    organization_name varchar(240) NOT NULL,
    title varchar(500) NOT NULL,
    customer_name varchar(300) NOT NULL,
    customer_value_level varchar(80) NOT NULL,
    sales_owner varchar(200) NOT NULL,
    presales_owners text[] NOT NULL DEFAULT ARRAY[]::text[],
    reliability_level varchar(32) NOT NULL CHECK (reliability_level IN ('high', 'medium', 'low')),
    stage_label varchar(120) NOT NULL,
    status_code varchar(32) NOT NULL CHECK (
        status_code IN ('won', 'active', 'paused', 'archived')
    ),
    expected_amount numeric(18,2) NOT NULL CHECK (expected_amount >= 0),
    signed_amount numeric(18,2) CHECK (signed_amount >= 0),
    expected_close_date date NOT NULL,
    entered_date date NOT NULL,
    products_services text[] NOT NULL DEFAULT ARRAY[]::text[],
    latest_progress text,
    industry varchar(160) NOT NULL,
    is_archived boolean NOT NULL DEFAULT false,
    archived_at date,
    UNIQUE (load_batch_id, source_record_id),
    CHECK ((status_code = 'won' AND signed_amount IS NOT NULL) OR status_code <> 'won'),
    CHECK ((is_archived AND archived_at IS NOT NULL) OR NOT is_archived)
);

CREATE TABLE IF NOT EXISTS ods_delivery (
    id bigserial PRIMARY KEY,
    load_batch_id varchar(160) NOT NULL REFERENCES source_batches(batch_id),
    source_system varchar(80) NOT NULL,
    source_record_id varchar(160) NOT NULL,
    source_native_record_id varchar(160) NOT NULL,
    source_updated_at timestamptz NOT NULL,
    is_deleted boolean NOT NULL DEFAULT false CHECK (is_deleted = false),
    project_code varchar(160) NOT NULL,
    opportunity_code varchar(160) NOT NULL,
    opportunity_name varchar(500) NOT NULL,
    customer_name varchar(300) NOT NULL,
    organization_code varchar(160) NOT NULL,
    organization_name varchar(240) NOT NULL,
    project_name varchar(500) NOT NULL,
    project_manager varchar(200) NOT NULL,
    delivery_owners text[] NOT NULL DEFAULT ARRAY[]::text[],
    status_label varchar(120) NOT NULL,
    status_code varchar(32) NOT NULL CHECK (
        status_code IN ('pending', 'active', 'attention', 'delayed', 'completed')
    ),
    risk_level varchar(80) NOT NULL,
    contract_amount numeric(18,2) NOT NULL CHECK (contract_amount >= 0),
    recognized_revenue numeric(18,2) NOT NULL CHECK (recognized_revenue >= 0),
    gross_margin_rate numeric(8,4) NOT NULL CHECK (gross_margin_rate BETWEEN 0 AND 1),
    planned_start_date date NOT NULL,
    planned_end_date date NOT NULL,
    actual_start_date date,
    actual_end_date date,
    current_milestone varchar(240),
    completion_rate numeric(8,4) NOT NULL CHECK (completion_rate BETWEEN 0 AND 1),
    delay_days integer NOT NULL DEFAULT 0 CHECK (delay_days >= 0),
    latest_progress text,
    data_updated_at timestamptz NOT NULL,
    UNIQUE (load_batch_id, source_record_id),
    CHECK (planned_end_date >= planned_start_date),
    CHECK (recognized_revenue <= contract_amount)
);

CREATE TABLE IF NOT EXISTS ods_collection (
    id bigserial PRIMARY KEY,
    load_batch_id varchar(160) NOT NULL REFERENCES source_batches(batch_id),
    source_system varchar(80) NOT NULL,
    source_record_id varchar(160) NOT NULL,
    source_native_record_id varchar(160) NOT NULL,
    source_updated_at timestamptz NOT NULL,
    is_deleted boolean NOT NULL DEFAULT false CHECK (is_deleted = false),
    collection_code varchar(160) NOT NULL,
    opportunity_code varchar(160) NOT NULL,
    project_code varchar(160) NOT NULL,
    customer_name varchar(300) NOT NULL,
    organization_code varchar(160) NOT NULL,
    organization_name varchar(240) NOT NULL,
    payment_type varchar(120) NOT NULL,
    payment_milestone varchar(200) NOT NULL,
    receivable_amount numeric(18,2) NOT NULL CHECK (receivable_amount >= 0),
    planned_collection_date date NOT NULL,
    actual_collection_date date,
    collected_amount numeric(18,2) NOT NULL CHECK (collected_amount >= 0),
    outstanding_amount numeric(18,2) NOT NULL CHECK (outstanding_amount >= 0),
    status_label varchar(120) NOT NULL,
    overdue_days integer NOT NULL DEFAULT 0 CHECK (overdue_days >= 0),
    aging_bucket varchar(80) NOT NULL,
    invoice_status varchar(80) NOT NULL,
    invoice_number varchar(160),
    collection_owner varchar(200) NOT NULL,
    latest_follow_up text,
    data_updated_at timestamptz NOT NULL,
    UNIQUE (load_batch_id, source_record_id),
    CHECK (receivable_amount = collected_amount + outstanding_amount)
);

CREATE INDEX IF NOT EXISTS ix_source_v3_batches_status
    ON source_batches (status, source_data_as_of DESC);
CREATE INDEX IF NOT EXISTS ix_source_v3_validation_batch
    ON source_validation_issues (load_batch_id, severity, domain);
CREATE INDEX IF NOT EXISTS ix_source_v3_opportunity_batch
    ON ods_opportunity (load_batch_id, organization_code, status_code);
CREATE INDEX IF NOT EXISTS ix_source_v3_delivery_batch
    ON ods_delivery (load_batch_id, organization_code, status_code);
CREATE INDEX IF NOT EXISTS ix_source_v3_collection_batch
    ON ods_collection (load_batch_id, organization_code, planned_collection_date);

-- Snapshot facts are immutable even when a privileged implementation account
-- is used accidentally.  New upstream state must always use a new batch.
CREATE OR REPLACE FUNCTION reject_source_v3_snapshot_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'executive_source_v3 snapshots are immutable; create a new batch';
END;
$$;

DROP TRIGGER IF EXISTS trg_immutable_ods_opportunity ON ods_opportunity;
CREATE TRIGGER trg_immutable_ods_opportunity
BEFORE UPDATE OR DELETE ON ods_opportunity
FOR EACH ROW EXECUTE FUNCTION reject_source_v3_snapshot_mutation();

DROP TRIGGER IF EXISTS trg_immutable_ods_delivery ON ods_delivery;
CREATE TRIGGER trg_immutable_ods_delivery
BEFORE UPDATE OR DELETE ON ods_delivery
FOR EACH ROW EXECUTE FUNCTION reject_source_v3_snapshot_mutation();

DROP TRIGGER IF EXISTS trg_immutable_ods_collection ON ods_collection;
CREATE TRIGGER trg_immutable_ods_collection
BEFORE UPDATE OR DELETE ON ods_collection
FOR EACH ROW EXECUTE FUNCTION reject_source_v3_snapshot_mutation();

-- CREATE TABLE IF NOT EXISTS is intentionally followed by a catalog audit.
-- It keeps the DDL idempotent without allowing a drifted legacy table to be
-- relabelled as 3.0 merely because its name already exists.
DO $source_v3_contract$
DECLARE
    defects text;
BEGIN
    WITH expected(table_name, column_name, formatted_type, not_null) AS (
        VALUES
            ('ods_schema_version', 'singleton', 'boolean', true),
            ('ods_schema_version', 'schema_version', 'character varying(32)', true),
            ('ods_schema_version', 'applied_at', 'timestamp with time zone', true),
            ('ods_schema_version', 'contract_name', 'character varying(120)', true),
            ('source_batches', 'batch_id', 'character varying(160)', true),
            ('source_batches', 'source_system', 'character varying(80)', true),
            ('source_batches', 'dataset_version', 'character varying(80)', true),
            ('source_batches', 'reference_date', 'date', true),
            ('source_batches', 'source_data_as_of', 'timestamp with time zone', true),
            ('source_batches', 'status', 'character varying(32)', true),
            ('source_batches', 'record_counts', 'jsonb', true),
            ('source_batches', 'table_content_sha256', 'jsonb', true),
            ('source_batches', 'table_schema_sha256', 'jsonb', true),
            ('source_batches', 'content_sha256', 'character(64)', true),
            ('source_batches', 'validation_result', 'jsonb', true),
            ('source_batches', 'created_at', 'timestamp with time zone', true),
            ('source_batches', 'validated_at', 'timestamp with time zone', false),
            ('source_batches', 'completed_at', 'timestamp with time zone', false),
            ('source_batches', 'activated_at', 'timestamp with time zone', false),
            ('source_table_bindings', 'id', 'bigint', true),
            ('source_table_bindings', 'load_batch_id', 'character varying(160)', true),
            ('source_table_bindings', 'domain', 'character varying(32)', true),
            ('source_table_bindings', 'source_system', 'character varying(80)', true),
            ('source_table_bindings', 'app_token', 'character varying(160)', true),
            ('source_table_bindings', 'table_id', 'character varying(160)', true),
            ('source_table_bindings', 'display_name', 'character varying(240)', true),
            ('source_table_bindings', 'field_mapping', 'jsonb', true),
            ('source_table_bindings', 'field_types', 'jsonb', true),
            ('source_table_bindings', 'schema_sha256', 'character(64)', true),
            ('source_table_bindings', 'record_count', 'integer', true),
            ('source_table_bindings', 'validated_at', 'timestamp with time zone', true),
            ('source_validation_issues', 'id', 'bigint', true),
            ('source_validation_issues', 'load_batch_id', 'character varying(160)', true),
            ('source_validation_issues', 'severity', 'character varying(16)', true),
            ('source_validation_issues', 'domain', 'character varying(32)', false),
            ('source_validation_issues', 'source_record_id', 'character varying(160)', false),
            ('source_validation_issues', 'field_name', 'character varying(160)', false),
            ('source_validation_issues', 'error_code', 'character varying(120)', true),
            ('source_validation_issues', 'message', 'text', true),
            ('source_validation_issues', 'details', 'jsonb', true),
            ('source_validation_issues', 'created_at', 'timestamp with time zone', true),
            ('source_sync_checkpoints', 'id', 'bigint', true),
            ('source_sync_checkpoints', 'source_system', 'character varying(80)', true),
            ('source_sync_checkpoints', 'domain', 'character varying(32)', true),
            ('source_sync_checkpoints', 'app_token', 'character varying(160)', true),
            ('source_sync_checkpoints', 'table_id', 'character varying(160)', true),
            ('source_sync_checkpoints', 'last_batch_id', 'character varying(160)', false),
            ('source_sync_checkpoints', 'next_page_token', 'text', false),
            ('source_sync_checkpoints', 'source_updated_at', 'timestamp with time zone', false),
            ('source_sync_checkpoints', 'content_sha256', 'character(64)', false),
            ('source_sync_checkpoints', 'synchronized_at', 'timestamp with time zone', true),
            ('ods_opportunity', 'id', 'bigint', true),
            ('ods_opportunity', 'load_batch_id', 'character varying(160)', true),
            ('ods_opportunity', 'source_system', 'character varying(80)', true),
            ('ods_opportunity', 'source_record_id', 'character varying(160)', true),
            ('ods_opportunity', 'source_native_record_id', 'character varying(160)', true),
            ('ods_opportunity', 'source_updated_at', 'timestamp with time zone', true),
            ('ods_opportunity', 'is_deleted', 'boolean', true),
            ('ods_opportunity', 'legacy_source_record_id', 'character varying(160)', false),
            ('ods_opportunity', 'opportunity_code', 'character varying(160)', true),
            ('ods_opportunity', 'organization_code', 'character varying(160)', true),
            ('ods_opportunity', 'organization_name', 'character varying(240)', true),
            ('ods_opportunity', 'title', 'character varying(500)', true),
            ('ods_opportunity', 'customer_name', 'character varying(300)', true),
            ('ods_opportunity', 'customer_value_level', 'character varying(80)', true),
            ('ods_opportunity', 'sales_owner', 'character varying(200)', true),
            ('ods_opportunity', 'presales_owners', 'text[]', true),
            ('ods_opportunity', 'reliability_level', 'character varying(32)', true),
            ('ods_opportunity', 'stage_label', 'character varying(120)', true),
            ('ods_opportunity', 'status_code', 'character varying(32)', true),
            ('ods_opportunity', 'expected_amount', 'numeric(18,2)', true),
            ('ods_opportunity', 'signed_amount', 'numeric(18,2)', false),
            ('ods_opportunity', 'expected_close_date', 'date', true),
            ('ods_opportunity', 'entered_date', 'date', true),
            ('ods_opportunity', 'products_services', 'text[]', true),
            ('ods_opportunity', 'latest_progress', 'text', false),
            ('ods_opportunity', 'industry', 'character varying(160)', true),
            ('ods_opportunity', 'is_archived', 'boolean', true),
            ('ods_opportunity', 'archived_at', 'date', false),
            ('ods_delivery', 'id', 'bigint', true),
            ('ods_delivery', 'load_batch_id', 'character varying(160)', true),
            ('ods_delivery', 'source_system', 'character varying(80)', true),
            ('ods_delivery', 'source_record_id', 'character varying(160)', true),
            ('ods_delivery', 'source_native_record_id', 'character varying(160)', true),
            ('ods_delivery', 'source_updated_at', 'timestamp with time zone', true),
            ('ods_delivery', 'is_deleted', 'boolean', true),
            ('ods_delivery', 'project_code', 'character varying(160)', true),
            ('ods_delivery', 'opportunity_code', 'character varying(160)', true),
            ('ods_delivery', 'opportunity_name', 'character varying(500)', true),
            ('ods_delivery', 'customer_name', 'character varying(300)', true),
            ('ods_delivery', 'organization_code', 'character varying(160)', true),
            ('ods_delivery', 'organization_name', 'character varying(240)', true),
            ('ods_delivery', 'project_name', 'character varying(500)', true),
            ('ods_delivery', 'project_manager', 'character varying(200)', true),
            ('ods_delivery', 'delivery_owners', 'text[]', true),
            ('ods_delivery', 'status_label', 'character varying(120)', true),
            ('ods_delivery', 'status_code', 'character varying(32)', true),
            ('ods_delivery', 'risk_level', 'character varying(80)', true),
            ('ods_delivery', 'contract_amount', 'numeric(18,2)', true),
            ('ods_delivery', 'recognized_revenue', 'numeric(18,2)', true),
            ('ods_delivery', 'gross_margin_rate', 'numeric(8,4)', true),
            ('ods_delivery', 'planned_start_date', 'date', true),
            ('ods_delivery', 'planned_end_date', 'date', true),
            ('ods_delivery', 'actual_start_date', 'date', false),
            ('ods_delivery', 'actual_end_date', 'date', false),
            ('ods_delivery', 'current_milestone', 'character varying(240)', false),
            ('ods_delivery', 'completion_rate', 'numeric(8,4)', true),
            ('ods_delivery', 'delay_days', 'integer', true),
            ('ods_delivery', 'latest_progress', 'text', false),
            ('ods_delivery', 'data_updated_at', 'timestamp with time zone', true),
            ('ods_collection', 'id', 'bigint', true),
            ('ods_collection', 'load_batch_id', 'character varying(160)', true),
            ('ods_collection', 'source_system', 'character varying(80)', true),
            ('ods_collection', 'source_record_id', 'character varying(160)', true),
            ('ods_collection', 'source_native_record_id', 'character varying(160)', true),
            ('ods_collection', 'source_updated_at', 'timestamp with time zone', true),
            ('ods_collection', 'is_deleted', 'boolean', true),
            ('ods_collection', 'collection_code', 'character varying(160)', true),
            ('ods_collection', 'opportunity_code', 'character varying(160)', true),
            ('ods_collection', 'project_code', 'character varying(160)', true),
            ('ods_collection', 'customer_name', 'character varying(300)', true),
            ('ods_collection', 'organization_code', 'character varying(160)', true),
            ('ods_collection', 'organization_name', 'character varying(240)', true),
            ('ods_collection', 'payment_type', 'character varying(120)', true),
            ('ods_collection', 'payment_milestone', 'character varying(200)', true),
            ('ods_collection', 'receivable_amount', 'numeric(18,2)', true),
            ('ods_collection', 'planned_collection_date', 'date', true),
            ('ods_collection', 'actual_collection_date', 'date', false),
            ('ods_collection', 'collected_amount', 'numeric(18,2)', true),
            ('ods_collection', 'outstanding_amount', 'numeric(18,2)', true),
            ('ods_collection', 'status_label', 'character varying(120)', true),
            ('ods_collection', 'overdue_days', 'integer', true),
            ('ods_collection', 'aging_bucket', 'character varying(80)', true),
            ('ods_collection', 'invoice_status', 'character varying(80)', true),
            ('ods_collection', 'invoice_number', 'character varying(160)', false),
            ('ods_collection', 'collection_owner', 'character varying(200)', true),
            ('ods_collection', 'latest_follow_up', 'text', false),
            ('ods_collection', 'data_updated_at', 'timestamp with time zone', true)
    ), actual AS (
        SELECT relation.relname AS table_name,
               attribute.attname AS column_name,
               pg_catalog.format_type(attribute.atttypid, attribute.atttypmod)
                   AS formatted_type,
               attribute.attnotnull AS not_null
        FROM pg_catalog.pg_class AS relation
        JOIN pg_catalog.pg_namespace AS namespace
          ON namespace.oid = relation.relnamespace
        JOIN pg_catalog.pg_attribute AS attribute
          ON attribute.attrelid = relation.oid
        WHERE namespace.nspname = 'executive_source_v3'
          AND relation.relkind IN ('r', 'p')
          AND attribute.attnum > 0
          AND NOT attribute.attisdropped
    )
    SELECT string_agg(
        format('%s.%s expected %s %s', expected.table_name, expected.column_name,
               expected.formatted_type, CASE WHEN expected.not_null THEN 'NOT NULL' ELSE 'NULL' END),
        '; ' ORDER BY expected.table_name, expected.column_name
    ) INTO defects
    FROM expected
    LEFT JOIN actual USING (table_name, column_name)
    WHERE actual.column_name IS NULL
       OR actual.formatted_type <> expected.formatted_type
       OR actual.not_null <> expected.not_null;

    IF defects IS NOT NULL THEN
        RAISE EXCEPTION 'ODS 3.0 column contract invalid: %', defects;
    END IF;

    WITH expected(table_name, constraint_type, columns, foreign_table, foreign_columns) AS (
        VALUES
            ('ods_schema_version', 'p', ARRAY['singleton']::text[], NULL, NULL),
            ('source_batches', 'p', ARRAY['batch_id']::text[], NULL, NULL),
            ('source_table_bindings', 'p', ARRAY['id']::text[], NULL, NULL),
            ('source_validation_issues', 'p', ARRAY['id']::text[], NULL, NULL),
            ('source_sync_checkpoints', 'p', ARRAY['id']::text[], NULL, NULL),
            ('ods_opportunity', 'p', ARRAY['id']::text[], NULL, NULL),
            ('ods_delivery', 'p', ARRAY['id']::text[], NULL, NULL),
            ('ods_collection', 'p', ARRAY['id']::text[], NULL, NULL),
            ('source_table_bindings', 'u', ARRAY['load_batch_id', 'domain']::text[], NULL, NULL),
            ('source_sync_checkpoints', 'u', ARRAY['source_system', 'domain', 'app_token', 'table_id']::text[], NULL, NULL),
            ('ods_opportunity', 'u', ARRAY['load_batch_id', 'source_record_id']::text[], NULL, NULL),
            ('ods_delivery', 'u', ARRAY['load_batch_id', 'source_record_id']::text[], NULL, NULL),
            ('ods_collection', 'u', ARRAY['load_batch_id', 'source_record_id']::text[], NULL, NULL),
            ('source_table_bindings', 'f', ARRAY['load_batch_id']::text[], 'source_batches', ARRAY['batch_id']::text[]),
            ('source_validation_issues', 'f', ARRAY['load_batch_id']::text[], 'source_batches', ARRAY['batch_id']::text[]),
            ('source_sync_checkpoints', 'f', ARRAY['last_batch_id']::text[], 'source_batches', ARRAY['batch_id']::text[]),
            ('ods_opportunity', 'f', ARRAY['load_batch_id']::text[], 'source_batches', ARRAY['batch_id']::text[]),
            ('ods_delivery', 'f', ARRAY['load_batch_id']::text[], 'source_batches', ARRAY['batch_id']::text[]),
            ('ods_collection', 'f', ARRAY['load_batch_id']::text[], 'source_batches', ARRAY['batch_id']::text[])
    ), actual AS (
        SELECT relation.relname AS table_name,
               constraint_record.contype::text AS constraint_type,
               coalesce(constraint_index.indisvalid, true) AS is_valid,
               coalesce(constraint_index.indisready, true) AS is_ready,
               ARRAY(
                   SELECT attribute.attname::text
                   FROM unnest(constraint_record.conkey) WITH ORDINALITY AS key(attnum, ord)
                   JOIN pg_catalog.pg_attribute AS attribute
                     ON attribute.attrelid = constraint_record.conrelid
                    AND attribute.attnum = key.attnum
                   ORDER BY key.ord
               ) AS columns,
               foreign_namespace.nspname::text AS foreign_table_schema,
               foreign_relation.relname::text AS foreign_table,
               CASE WHEN constraint_record.confrelid = 0 THEN NULL ELSE ARRAY(
                   SELECT attribute.attname::text
                   FROM unnest(constraint_record.confkey) WITH ORDINALITY AS key(attnum, ord)
                   JOIN pg_catalog.pg_attribute AS attribute
                     ON attribute.attrelid = constraint_record.confrelid
                    AND attribute.attnum = key.attnum
                   ORDER BY key.ord
               ) END AS foreign_columns
        FROM pg_catalog.pg_constraint AS constraint_record
        JOIN pg_catalog.pg_class AS relation ON relation.oid = constraint_record.conrelid
        JOIN pg_catalog.pg_namespace AS namespace ON namespace.oid = relation.relnamespace
        LEFT JOIN pg_catalog.pg_class AS foreign_relation
          ON foreign_relation.oid = constraint_record.confrelid
        LEFT JOIN pg_catalog.pg_namespace AS foreign_namespace
          ON foreign_namespace.oid = foreign_relation.relnamespace
        LEFT JOIN pg_catalog.pg_index AS constraint_index
          ON constraint_index.indexrelid = constraint_record.conindid
        WHERE namespace.nspname = 'executive_source_v3'
          AND constraint_record.contype IN ('p', 'u', 'f')
    )
    SELECT string_agg(
        format('%s %s (%s)', expected.table_name, expected.constraint_type,
               array_to_string(expected.columns, ',')),
        '; ' ORDER BY expected.table_name, expected.constraint_type
    ) INTO defects
    FROM expected
    WHERE NOT EXISTS (
        SELECT 1 FROM actual
        WHERE actual.table_name = expected.table_name
          AND actual.constraint_type = expected.constraint_type
          AND actual.columns = expected.columns
          AND actual.is_valid
          AND actual.is_ready
          AND (
              expected.foreign_table IS NULL
              OR actual.foreign_table_schema = 'executive_source_v3'
          )
          AND actual.foreign_table IS NOT DISTINCT FROM expected.foreign_table
          AND actual.foreign_columns IS NOT DISTINCT FROM expected.foreign_columns
    );

    IF defects IS NOT NULL THEN
        RAISE EXCEPTION 'ODS 3.0 key contract invalid: %', defects;
    END IF;

    WITH expected(index_name, table_name, columns) AS (
        VALUES
            ('ix_source_v3_batches_status', 'source_batches', ARRAY['status', 'source_data_as_of']::text[]),
            ('ix_source_v3_validation_batch', 'source_validation_issues', ARRAY['load_batch_id', 'severity', 'domain']::text[]),
            ('ix_source_v3_opportunity_batch', 'ods_opportunity', ARRAY['load_batch_id', 'organization_code', 'status_code']::text[]),
            ('ix_source_v3_delivery_batch', 'ods_delivery', ARRAY['load_batch_id', 'organization_code', 'status_code']::text[]),
            ('ix_source_v3_collection_batch', 'ods_collection', ARRAY['load_batch_id', 'organization_code', 'planned_collection_date']::text[])
    ), actual AS (
        SELECT index_relation.relname AS index_name,
               table_relation.relname AS table_name,
               ARRAY(
                   SELECT attribute.attname::text
                   FROM unnest(index_record.indkey) WITH ORDINALITY AS key(attnum, ord)
                   JOIN pg_catalog.pg_attribute AS attribute
                     ON attribute.attrelid = index_record.indrelid
                    AND attribute.attnum = key.attnum
                   ORDER BY key.ord
               ) AS columns,
               index_record.indisvalid,
               index_record.indisready
        FROM pg_catalog.pg_index AS index_record
        JOIN pg_catalog.pg_class AS table_relation ON table_relation.oid = index_record.indrelid
        JOIN pg_catalog.pg_namespace AS namespace ON namespace.oid = table_relation.relnamespace
        JOIN pg_catalog.pg_class AS index_relation ON index_relation.oid = index_record.indexrelid
        WHERE namespace.nspname = 'executive_source_v3'
    )
    SELECT string_agg(expected.index_name, '; ' ORDER BY expected.index_name) INTO defects
    FROM expected
    WHERE NOT EXISTS (
        SELECT 1 FROM actual
        WHERE actual.index_name = expected.index_name
          AND actual.table_name = expected.table_name
          AND actual.columns = expected.columns
          AND actual.indisvalid
          AND actual.indisready
    );

    IF defects IS NOT NULL THEN
        RAISE EXCEPTION 'ODS 3.0 index contract invalid: %', defects;
    END IF;

    WITH expected(table_name, trigger_name) AS (
        VALUES
            ('ods_opportunity', 'trg_immutable_ods_opportunity'),
            ('ods_delivery', 'trg_immutable_ods_delivery'),
            ('ods_collection', 'trg_immutable_ods_collection')
    )
    SELECT string_agg(expected.trigger_name, '; ' ORDER BY expected.trigger_name) INTO defects
    FROM expected
    WHERE NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_trigger AS trigger_record
        JOIN pg_catalog.pg_class AS relation ON relation.oid = trigger_record.tgrelid
        JOIN pg_catalog.pg_namespace AS namespace ON namespace.oid = relation.relnamespace
        JOIN pg_catalog.pg_proc AS function_record ON function_record.oid = trigger_record.tgfoid
        JOIN pg_catalog.pg_namespace AS function_namespace
          ON function_namespace.oid = function_record.pronamespace
        WHERE namespace.nspname = 'executive_source_v3'
          AND relation.relname = expected.table_name
          AND trigger_record.tgname = expected.trigger_name
          AND NOT trigger_record.tgisinternal
          AND trigger_record.tgenabled IN ('O', 'A')
          AND function_namespace.nspname = 'executive_source_v3'
          AND function_record.proname = 'reject_source_v3_snapshot_mutation'
          AND (trigger_record.tgtype & 1) = 1
          AND (trigger_record.tgtype & 2) = 2
          AND (trigger_record.tgtype & 8) = 8
          AND (trigger_record.tgtype & 16) = 16
    );

    IF defects IS NOT NULL THEN
        RAISE EXCEPTION 'ODS 3.0 immutable trigger contract invalid: %', defects;
    END IF;
END;
$source_v3_contract$;

INSERT INTO ods_schema_version (singleton, schema_version)
VALUES (true, '3.0')
ON CONFLICT (singleton) DO UPDATE
SET schema_version = EXCLUDED.schema_version,
    applied_at = now(),
    contract_name = 'executive-ai-sanitized-ods-v3';

COMMENT ON SCHEMA executive_source_v3 IS
    'Sanitized ODS 3.0. Immutable complete snapshots from the three approved Feishu tables.';

COMMIT;
