-- Executive AI Researcher phase-2 sanitized source contract.
-- Customers own this database and write only de-identified business data.
-- Product runtime accounts receive SELECT on the explicit ODS columns only.

CREATE SCHEMA IF NOT EXISTS executive_source;
SET search_path TO executive_source, public;

CREATE TABLE IF NOT EXISTS ods_schema_version (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    schema_version varchar(32) NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now(),
    contract_name varchar(120) NOT NULL DEFAULT 'executive-ai-sanitized-ods'
);

INSERT INTO ods_schema_version (singleton, schema_version)
VALUES (true, '2.0')
ON CONFLICT (singleton) DO UPDATE
SET schema_version = EXCLUDED.schema_version,
    applied_at = now();

CREATE TABLE IF NOT EXISTS source_batches (
    batch_id varchar(160) PRIMARY KEY,
    source_system varchar(80) NOT NULL,
    dataset_version varchar(80) NOT NULL,
    reference_date date NOT NULL,
    source_data_as_of timestamptz NOT NULL,
    status varchar(32) NOT NULL CHECK (status IN ('building', 'ready', 'failed', 'superseded')),
    seed varchar(160),
    record_counts jsonb NOT NULL DEFAULT '{}'::jsonb,
    content_sha256 char(64),
    validation_result jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz
);

CREATE TABLE IF NOT EXISTS ods_organization_unit (
    id bigserial PRIMARY KEY,
    source_system varchar(80) NOT NULL,
    source_record_id varchar(160) NOT NULL,
    source_updated_at timestamptz NOT NULL,
    load_batch_id varchar(160) NOT NULL REFERENCES source_batches(batch_id),
    is_deleted boolean NOT NULL DEFAULT false,
    organization_code varchar(80) NOT NULL,
    parent_organization_code varchar(80),
    display_name varchar(200) NOT NULL,
    unit_type varchar(40) NOT NULL DEFAULT 'division',
    sort_order integer NOT NULL DEFAULT 0,
    UNIQUE (source_system, source_record_id)
);

CREATE TABLE IF NOT EXISTS ods_person (
    id bigserial PRIMARY KEY,
    source_system varchar(80) NOT NULL,
    source_record_id varchar(160) NOT NULL,
    source_updated_at timestamptz NOT NULL,
    load_batch_id varchar(160) NOT NULL REFERENCES source_batches(batch_id),
    is_deleted boolean NOT NULL DEFAULT false,
    organization_code varchar(80) NOT NULL,
    display_name varchar(200) NOT NULL,
    role_title varchar(160),
    is_active boolean NOT NULL DEFAULT true,
    UNIQUE (source_system, source_record_id)
);

CREATE TABLE IF NOT EXISTS ods_customer (
    id bigserial PRIMARY KEY,
    source_system varchar(80) NOT NULL,
    source_record_id varchar(160) NOT NULL,
    source_updated_at timestamptz NOT NULL,
    load_batch_id varchar(160) NOT NULL REFERENCES source_batches(batch_id),
    is_deleted boolean NOT NULL DEFAULT false,
    organization_code varchar(80) NOT NULL,
    owner_person_record_id varchar(160),
    display_name varchar(240) NOT NULL,
    industry varchar(120),
    region varchar(120),
    customer_since date,
    UNIQUE (source_system, source_record_id)
);

CREATE TABLE IF NOT EXISTS ods_opportunity (
    id bigserial PRIMARY KEY,
    source_system varchar(80) NOT NULL,
    source_record_id varchar(160) NOT NULL,
    source_updated_at timestamptz NOT NULL,
    load_batch_id varchar(160) NOT NULL REFERENCES source_batches(batch_id),
    is_deleted boolean NOT NULL DEFAULT false,
    organization_code varchar(80) NOT NULL,
    customer_record_id varchar(160) NOT NULL,
    owner_person_record_id varchar(160),
    opportunity_code varchar(120) NOT NULL,
    title varchar(300) NOT NULL,
    stage varchar(80) NOT NULL,
    status varchar(40) NOT NULL,
    probability integer NOT NULL CHECK (probability BETWEEN 0 AND 100),
    expected_amount numeric(18,2) NOT NULL CHECK (expected_amount >= 0),
    expected_gross_profit numeric(18,2) NOT NULL CHECK (expected_gross_profit >= 0),
    created_date date NOT NULL,
    expected_close_date date NOT NULL,
    closed_date date,
    UNIQUE (source_system, source_record_id)
);

CREATE TABLE IF NOT EXISTS ods_delivery (
    id bigserial PRIMARY KEY,
    source_system varchar(80) NOT NULL,
    source_record_id varchar(160) NOT NULL,
    source_updated_at timestamptz NOT NULL,
    load_batch_id varchar(160) NOT NULL REFERENCES source_batches(batch_id),
    is_deleted boolean NOT NULL DEFAULT false,
    organization_code varchar(80) NOT NULL,
    opportunity_record_id varchar(160) NOT NULL,
    customer_record_id varchar(160) NOT NULL,
    manager_person_record_id varchar(160),
    project_code varchar(120) NOT NULL,
    project_name varchar(300) NOT NULL,
    status varchar(40) NOT NULL,
    risk_level varchar(40) NOT NULL,
    completion_percent integer NOT NULL CHECK (completion_percent BETWEEN 0 AND 100),
    contract_amount numeric(18,2) NOT NULL CHECK (contract_amount >= 0),
    gross_margin_rate numeric(8,4) NOT NULL CHECK (gross_margin_rate BETWEEN 0 AND 1),
    planned_start_date date NOT NULL,
    planned_end_date date NOT NULL,
    actual_end_date date,
    current_milestone varchar(200),
    delay_days integer NOT NULL DEFAULT 0 CHECK (delay_days >= 0),
    UNIQUE (source_system, source_record_id)
);

CREATE TABLE IF NOT EXISTS ods_collection (
    id bigserial PRIMARY KEY,
    source_system varchar(80) NOT NULL,
    source_record_id varchar(160) NOT NULL,
    source_updated_at timestamptz NOT NULL,
    load_batch_id varchar(160) NOT NULL REFERENCES source_batches(batch_id),
    is_deleted boolean NOT NULL DEFAULT false,
    organization_code varchar(80) NOT NULL,
    project_record_id varchar(160) NOT NULL,
    customer_record_id varchar(160) NOT NULL,
    invoice_amount numeric(18,2) NOT NULL CHECK (invoice_amount >= 0),
    receivable_amount numeric(18,2) NOT NULL CHECK (receivable_amount >= 0),
    collected_amount numeric(18,2) NOT NULL CHECK (collected_amount >= 0),
    planned_collection_date date NOT NULL,
    actual_collection_date date,
    overdue_days integer NOT NULL DEFAULT 0 CHECK (overdue_days >= 0),
    aging_bucket varchar(40) NOT NULL,
    status varchar(40) NOT NULL,
    CHECK (collected_amount <= receivable_amount),
    CHECK (receivable_amount <= invoice_amount),
    UNIQUE (source_system, source_record_id)
);

CREATE TABLE IF NOT EXISTS ods_target (
    id bigserial PRIMARY KEY,
    source_system varchar(80) NOT NULL,
    source_record_id varchar(160) NOT NULL,
    source_updated_at timestamptz NOT NULL,
    load_batch_id varchar(160) NOT NULL REFERENCES source_batches(batch_id),
    is_deleted boolean NOT NULL DEFAULT false,
    organization_code varchar(80) NOT NULL,
    metric_code varchar(80) NOT NULL,
    metric_name varchar(160) NOT NULL,
    period_type varchar(32) NOT NULL,
    period_start date NOT NULL,
    period_end date NOT NULL,
    target_value numeric(18,2) NOT NULL CHECK (target_value >= 0),
    unit varchar(32) NOT NULL,
    CHECK (period_end >= period_start),
    UNIQUE (source_system, source_record_id)
);

CREATE INDEX IF NOT EXISTS ix_ods_org_batch ON ods_organization_unit (load_batch_id);
CREATE INDEX IF NOT EXISTS ix_ods_person_batch ON ods_person (load_batch_id);
CREATE INDEX IF NOT EXISTS ix_ods_customer_batch ON ods_customer (load_batch_id);
CREATE INDEX IF NOT EXISTS ix_ods_opportunity_batch ON ods_opportunity (load_batch_id);
CREATE INDEX IF NOT EXISTS ix_ods_delivery_batch ON ods_delivery (load_batch_id);
CREATE INDEX IF NOT EXISTS ix_ods_collection_batch ON ods_collection (load_batch_id);
CREATE INDEX IF NOT EXISTS ix_ods_target_batch ON ods_target (load_batch_id);
CREATE INDEX IF NOT EXISTS ix_ods_opportunity_org_close ON ods_opportunity (organization_code, expected_close_date);
CREATE INDEX IF NOT EXISTS ix_ods_delivery_org_status ON ods_delivery (organization_code, status, risk_level);
CREATE INDEX IF NOT EXISTS ix_ods_collection_org_due ON ods_collection (organization_code, planned_collection_date, status);
CREATE INDEX IF NOT EXISTS ix_ods_target_org_period ON ods_target (organization_code, metric_code, period_start, period_end);

COMMENT ON SCHEMA executive_source IS
    'De-identified source contract. Do not store raw CRM PII or reverse-identity mappings.';
