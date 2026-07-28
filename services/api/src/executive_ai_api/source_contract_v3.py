from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any

import psycopg
from psycopg import sql

from .source_contract import SourceContractError, connect_source

SOURCE_V3_SCHEMA = "executive_source_v3"
SOURCE_V3_SCHEMA_VERSION = "3.0"
SOURCE_V3_DOMAINS = ("opportunity", "delivery", "collection")
SOURCE_V3_SYSTEM = "simulated_feishu_live"

SOURCE_V3_TABLES = {
    "opportunity": "ods_opportunity",
    "delivery": "ods_delivery",
    "collection": "ods_collection",
}

SOURCE_V3_COLUMNS: dict[str, tuple[str, ...]] = {
    "opportunity": (
        "load_batch_id",
        "source_system",
        "source_record_id",
        "source_native_record_id",
        "source_updated_at",
        "is_deleted",
        "legacy_source_record_id",
        "opportunity_code",
        "organization_code",
        "organization_name",
        "title",
        "customer_name",
        "customer_value_level",
        "sales_owner",
        "presales_owners",
        "reliability_level",
        "stage_label",
        "status_code",
        "expected_amount",
        "signed_amount",
        "expected_close_date",
        "entered_date",
        "products_services",
        "latest_progress",
        "industry",
        "is_archived",
        "archived_at",
    ),
    "delivery": (
        "load_batch_id",
        "source_system",
        "source_record_id",
        "source_native_record_id",
        "source_updated_at",
        "is_deleted",
        "project_code",
        "opportunity_code",
        "opportunity_name",
        "customer_name",
        "organization_code",
        "organization_name",
        "project_name",
        "project_manager",
        "delivery_owners",
        "status_label",
        "status_code",
        "risk_level",
        "contract_amount",
        "recognized_revenue",
        "gross_margin_rate",
        "planned_start_date",
        "planned_end_date",
        "actual_start_date",
        "actual_end_date",
        "current_milestone",
        "completion_rate",
        "delay_days",
        "latest_progress",
        "data_updated_at",
    ),
    "collection": (
        "load_batch_id",
        "source_system",
        "source_record_id",
        "source_native_record_id",
        "source_updated_at",
        "is_deleted",
        "collection_code",
        "opportunity_code",
        "project_code",
        "customer_name",
        "organization_code",
        "organization_name",
        "payment_type",
        "payment_milestone",
        "receivable_amount",
        "planned_collection_date",
        "actual_collection_date",
        "collected_amount",
        "outstanding_amount",
        "status_label",
        "overdue_days",
        "aging_bucket",
        "invoice_status",
        "invoice_number",
        "collection_owner",
        "latest_follow_up",
        "data_updated_at",
    ),
}

ORGANIZATION_CODE_OVERRIDES = {
    "华东事业部": "east",
    "华南事业部": "south",
    "华北事业部": "north",
    "西南事业部": "west",
    "战略客户事业部": "strategic",
    "创新业务事业部": "innovation",
}

RELIABILITY_CODES = {
    "高": "high",
    "high": "high",
    "中": "medium",
    "medium": "medium",
    "低": "low",
    "low": "low",
}

ACTIVE_OPPORTUNITY_STAGES = {
    "POC测试",
    "商务报价",
    "方案沟通",
    "线索/意向",
    "比赛",
}

DELIVERY_STATUS_CODES = {
    "待启动": "pending",
    "进行中": "active",
    "交付中": "active",
    "实施中": "active",
    "关注": "attention",
    "风险关注": "attention",
    "延期关注": "delayed",
    "已完成": "completed",
}


@dataclass(frozen=True)
class SourceV3ContractInspection:
    schema_version: str
    database_version: str
    database_version_num: int
    current_user: str
    transaction_read_only: bool
    role_is_privileged: bool
    tls_active: bool
    missing_tables: tuple[str, ...]
    missing_columns: dict[str, tuple[str, ...]]
    invalid_columns: dict[str, tuple[str, ...]]
    missing_primary_keys: tuple[str, ...]
    missing_unique_constraints: tuple[str, ...]
    missing_foreign_keys: tuple[str, ...]
    missing_indexes: tuple[str, ...]
    invalid_immutable_triggers: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return not any(
            (
                self.missing_tables,
                self.missing_columns,
                self.invalid_columns,
                self.missing_primary_keys,
                self.missing_unique_constraints,
                self.missing_foreign_keys,
                self.missing_indexes,
                self.invalid_immutable_triggers,
            )
        )


@dataclass(frozen=True)
class SourceV3PreparedBatch:
    batch_id: str
    reference_date: date
    source_data_as_of: datetime
    rows: dict[str, tuple[dict[str, Any], ...]]
    table_content_sha256: dict[str, str]
    table_schema_sha256: dict[str, str]
    content_sha256: str
    validation: dict[str, Any]

    @property
    def record_counts(self) -> dict[str, int]:
        return {domain: len(self.rows[domain]) for domain in SOURCE_V3_DOMAINS}


ColumnContract = tuple[str, bool]


def _column_contract(
    values: dict[str, tuple[str, bool]],
) -> dict[str, ColumnContract]:
    return values


def _expected_v3_column_contracts() -> dict[str, dict[str, ColumnContract]]:
    """Return the catalog-level ODS 3.0 contract.

    The second tuple item is ``attnotnull``.  PostgreSQL's canonical
    ``format_type`` output is used so varchar/numeric typmods cannot drift
    silently while the public column names remain unchanged.
    """

    return {
        "ods_schema_version": _column_contract(
            {
                "singleton": ("boolean", True),
                "schema_version": ("character varying(32)", True),
                "applied_at": ("timestamp with time zone", True),
                "contract_name": ("character varying(120)", True),
            }
        ),
        "source_batches": _column_contract(
            {
                "batch_id": ("character varying(160)", True),
                "source_system": ("character varying(80)", True),
                "dataset_version": ("character varying(80)", True),
                "reference_date": ("date", True),
                "source_data_as_of": ("timestamp with time zone", True),
                "status": ("character varying(32)", True),
                "record_counts": ("jsonb", True),
                "table_content_sha256": ("jsonb", True),
                "table_schema_sha256": ("jsonb", True),
                "content_sha256": ("character(64)", True),
                "validation_result": ("jsonb", True),
                "created_at": ("timestamp with time zone", True),
                "validated_at": ("timestamp with time zone", False),
                "completed_at": ("timestamp with time zone", False),
                "activated_at": ("timestamp with time zone", False),
            }
        ),
        "source_table_bindings": _column_contract(
            {
                "id": ("bigint", True),
                "load_batch_id": ("character varying(160)", True),
                "domain": ("character varying(32)", True),
                "source_system": ("character varying(80)", True),
                "app_token": ("character varying(160)", True),
                "table_id": ("character varying(160)", True),
                "display_name": ("character varying(240)", True),
                "field_mapping": ("jsonb", True),
                "field_types": ("jsonb", True),
                "schema_sha256": ("character(64)", True),
                "record_count": ("integer", True),
                "validated_at": ("timestamp with time zone", True),
            }
        ),
        "source_validation_issues": _column_contract(
            {
                "id": ("bigint", True),
                "load_batch_id": ("character varying(160)", True),
                "severity": ("character varying(16)", True),
                "domain": ("character varying(32)", False),
                "source_record_id": ("character varying(160)", False),
                "field_name": ("character varying(160)", False),
                "error_code": ("character varying(120)", True),
                "message": ("text", True),
                "details": ("jsonb", True),
                "created_at": ("timestamp with time zone", True),
            }
        ),
        "source_sync_checkpoints": _column_contract(
            {
                "id": ("bigint", True),
                "source_system": ("character varying(80)", True),
                "domain": ("character varying(32)", True),
                "app_token": ("character varying(160)", True),
                "table_id": ("character varying(160)", True),
                "last_batch_id": ("character varying(160)", False),
                "next_page_token": ("text", False),
                "source_updated_at": ("timestamp with time zone", False),
                "content_sha256": ("character(64)", False),
                "synchronized_at": ("timestamp with time zone", True),
            }
        ),
        "ods_opportunity": _column_contract(
            {
                "id": ("bigint", True),
                "load_batch_id": ("character varying(160)", True),
                "source_system": ("character varying(80)", True),
                "source_record_id": ("character varying(160)", True),
                "source_native_record_id": ("character varying(160)", True),
                "source_updated_at": ("timestamp with time zone", True),
                "is_deleted": ("boolean", True),
                "legacy_source_record_id": ("character varying(160)", False),
                "opportunity_code": ("character varying(160)", True),
                "organization_code": ("character varying(160)", True),
                "organization_name": ("character varying(240)", True),
                "title": ("character varying(500)", True),
                "customer_name": ("character varying(300)", True),
                "customer_value_level": ("character varying(80)", True),
                "sales_owner": ("character varying(200)", True),
                "presales_owners": ("text[]", True),
                "reliability_level": ("character varying(32)", True),
                "stage_label": ("character varying(120)", True),
                "status_code": ("character varying(32)", True),
                "expected_amount": ("numeric(18,2)", True),
                "signed_amount": ("numeric(18,2)", False),
                "expected_close_date": ("date", True),
                "entered_date": ("date", True),
                "products_services": ("text[]", True),
                "latest_progress": ("text", False),
                "industry": ("character varying(160)", True),
                "is_archived": ("boolean", True),
                "archived_at": ("date", False),
            }
        ),
        "ods_delivery": _column_contract(
            {
                "id": ("bigint", True),
                "load_batch_id": ("character varying(160)", True),
                "source_system": ("character varying(80)", True),
                "source_record_id": ("character varying(160)", True),
                "source_native_record_id": ("character varying(160)", True),
                "source_updated_at": ("timestamp with time zone", True),
                "is_deleted": ("boolean", True),
                "project_code": ("character varying(160)", True),
                "opportunity_code": ("character varying(160)", True),
                "opportunity_name": ("character varying(500)", True),
                "customer_name": ("character varying(300)", True),
                "organization_code": ("character varying(160)", True),
                "organization_name": ("character varying(240)", True),
                "project_name": ("character varying(500)", True),
                "project_manager": ("character varying(200)", True),
                "delivery_owners": ("text[]", True),
                "status_label": ("character varying(120)", True),
                "status_code": ("character varying(32)", True),
                "risk_level": ("character varying(80)", True),
                "contract_amount": ("numeric(18,2)", True),
                "recognized_revenue": ("numeric(18,2)", True),
                "gross_margin_rate": ("numeric(8,4)", True),
                "planned_start_date": ("date", True),
                "planned_end_date": ("date", True),
                "actual_start_date": ("date", False),
                "actual_end_date": ("date", False),
                "current_milestone": ("character varying(240)", False),
                "completion_rate": ("numeric(8,4)", True),
                "delay_days": ("integer", True),
                "latest_progress": ("text", False),
                "data_updated_at": ("timestamp with time zone", True),
            }
        ),
        "ods_collection": _column_contract(
            {
                "id": ("bigint", True),
                "load_batch_id": ("character varying(160)", True),
                "source_system": ("character varying(80)", True),
                "source_record_id": ("character varying(160)", True),
                "source_native_record_id": ("character varying(160)", True),
                "source_updated_at": ("timestamp with time zone", True),
                "is_deleted": ("boolean", True),
                "collection_code": ("character varying(160)", True),
                "opportunity_code": ("character varying(160)", True),
                "project_code": ("character varying(160)", True),
                "customer_name": ("character varying(300)", True),
                "organization_code": ("character varying(160)", True),
                "organization_name": ("character varying(240)", True),
                "payment_type": ("character varying(120)", True),
                "payment_milestone": ("character varying(200)", True),
                "receivable_amount": ("numeric(18,2)", True),
                "planned_collection_date": ("date", True),
                "actual_collection_date": ("date", False),
                "collected_amount": ("numeric(18,2)", True),
                "outstanding_amount": ("numeric(18,2)", True),
                "status_label": ("character varying(120)", True),
                "overdue_days": ("integer", True),
                "aging_bucket": ("character varying(80)", True),
                "invoice_status": ("character varying(80)", True),
                "invoice_number": ("character varying(160)", False),
                "collection_owner": ("character varying(200)", True),
                "latest_follow_up": ("text", False),
                "data_updated_at": ("timestamp with time zone", True),
            }
        ),
    }


SOURCE_V3_PRIMARY_KEYS: dict[str, tuple[str, ...]] = {
    "ods_schema_version": ("singleton",),
    "source_batches": ("batch_id",),
    "source_table_bindings": ("id",),
    "source_validation_issues": ("id",),
    "source_sync_checkpoints": ("id",),
    "ods_opportunity": ("id",),
    "ods_delivery": ("id",),
    "ods_collection": ("id",),
}

SOURCE_V3_UNIQUE_CONSTRAINTS: dict[str, tuple[tuple[str, ...], ...]] = {
    "source_table_bindings": (("load_batch_id", "domain"),),
    "source_sync_checkpoints": (("source_system", "domain", "app_token", "table_id"),),
    "ods_opportunity": (("load_batch_id", "source_record_id"),),
    "ods_delivery": (("load_batch_id", "source_record_id"),),
    "ods_collection": (("load_batch_id", "source_record_id"),),
}

SOURCE_V3_FOREIGN_KEYS: tuple[tuple[str, tuple[str, ...], str, tuple[str, ...]], ...] = (
    ("source_table_bindings", ("load_batch_id",), "source_batches", ("batch_id",)),
    ("source_validation_issues", ("load_batch_id",), "source_batches", ("batch_id",)),
    ("source_sync_checkpoints", ("last_batch_id",), "source_batches", ("batch_id",)),
    ("ods_opportunity", ("load_batch_id",), "source_batches", ("batch_id",)),
    ("ods_delivery", ("load_batch_id",), "source_batches", ("batch_id",)),
    ("ods_collection", ("load_batch_id",), "source_batches", ("batch_id",)),
)

SOURCE_V3_INDEXES: dict[str, tuple[str, tuple[str, ...]]] = {
    "ix_source_v3_batches_status": ("source_batches", ("status", "source_data_as_of")),
    "ix_source_v3_validation_batch": (
        "source_validation_issues",
        ("load_batch_id", "severity", "domain"),
    ),
    "ix_source_v3_opportunity_batch": (
        "ods_opportunity",
        ("load_batch_id", "organization_code", "status_code"),
    ),
    "ix_source_v3_delivery_batch": (
        "ods_delivery",
        ("load_batch_id", "organization_code", "status_code"),
    ),
    "ix_source_v3_collection_batch": (
        "ods_collection",
        ("load_batch_id", "organization_code", "planned_collection_date"),
    ),
}

SOURCE_V3_IMMUTABLE_TRIGGERS: dict[str, str] = {
    "ods_opportunity": "trg_immutable_ods_opportunity",
    "ods_delivery": "trg_immutable_ods_delivery",
    "ods_collection": "trg_immutable_ods_collection",
}


def _expected_v3_tables() -> dict[str, tuple[str, ...]]:
    return {table: tuple(columns) for table, columns in _expected_v3_column_contracts().items()}


def inspect_source_v3_contract(
    connection: psycopg.Connection,
    *,
    expected_version: str = SOURCE_V3_SCHEMA_VERSION,
    schema: str = SOURCE_V3_SCHEMA,
) -> SourceV3ContractInspection:
    with connection.cursor() as cursor:
        database_version = str(cursor.execute("SHOW server_version").fetchone()["server_version"])
        connection_state = cursor.execute(
            """
            SELECT current_setting('server_version_num')::integer AS database_version_num,
                   current_user AS current_user,
                   current_setting('transaction_read_only') = 'on' AS transaction_read_only,
                   (role.rolsuper OR role.rolcreatedb OR role.rolcreaterole OR role.rolreplication)
                       AS role_is_privileged,
                   coalesce(ssl.ssl, false) AS tls_active
            FROM pg_roles AS role
            LEFT JOIN pg_stat_ssl AS ssl ON ssl.pid = pg_backend_pid()
            WHERE role.rolname = current_user
            """
        ).fetchone()
        if connection_state is None:
            raise SourceContractError("source_role_missing", "无法确认脱敏源库连接账号")
        column_rows = cursor.execute(
            """
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
            WHERE namespace.nspname = %s
              AND relation.relkind IN ('r', 'p')
              AND attribute.attnum > 0
              AND NOT attribute.attisdropped
            """,
            (schema,),
        ).fetchall()
        constraint_rows = cursor.execute(
            """
            SELECT relation.relname AS table_name,
                   constraint_record.contype AS constraint_type,
                   coalesce(constraint_index.indisvalid, true) AS is_valid,
                   coalesce(constraint_index.indisready, true) AS is_ready,
                   ARRAY(
                       SELECT attribute.attname
                       FROM unnest(constraint_record.conkey) WITH ORDINALITY AS key(attnum, ord)
                       JOIN pg_catalog.pg_attribute AS attribute
                         ON attribute.attrelid = constraint_record.conrelid
                        AND attribute.attnum = key.attnum
                       ORDER BY key.ord
                   ) AS columns,
                   foreign_namespace.nspname AS foreign_table_schema,
                   foreign_relation.relname AS foreign_table_name,
                   CASE WHEN constraint_record.confrelid = 0 THEN ARRAY[]::name[] ELSE ARRAY(
                       SELECT attribute.attname
                       FROM unnest(constraint_record.confkey)
                            WITH ORDINALITY AS key(attnum, ord)
                       JOIN pg_catalog.pg_attribute AS attribute
                         ON attribute.attrelid = constraint_record.confrelid
                        AND attribute.attnum = key.attnum
                       ORDER BY key.ord
                   ) END AS foreign_columns
            FROM pg_catalog.pg_constraint AS constraint_record
            JOIN pg_catalog.pg_class AS relation
              ON relation.oid = constraint_record.conrelid
            JOIN pg_catalog.pg_namespace AS namespace
              ON namespace.oid = relation.relnamespace
            LEFT JOIN pg_catalog.pg_class AS foreign_relation
              ON foreign_relation.oid = constraint_record.confrelid
            LEFT JOIN pg_catalog.pg_namespace AS foreign_namespace
              ON foreign_namespace.oid = foreign_relation.relnamespace
            LEFT JOIN pg_catalog.pg_index AS constraint_index
              ON constraint_index.indexrelid = constraint_record.conindid
            WHERE namespace.nspname = %s
              AND constraint_record.contype IN ('p', 'u', 'f')
            """,
            (schema,),
        ).fetchall()
        index_rows = cursor.execute(
            """
            SELECT index_relation.relname AS index_name,
                   table_relation.relname AS table_name,
                   ARRAY(
                       SELECT attribute.attname
                       FROM unnest(index_record.indkey)
                            WITH ORDINALITY AS key(attnum, ord)
                       JOIN pg_catalog.pg_attribute AS attribute
                         ON attribute.attrelid = index_record.indrelid
                        AND attribute.attnum = key.attnum
                       ORDER BY key.ord
                   ) AS columns,
                   index_record.indisvalid AS is_valid,
                   index_record.indisready AS is_ready
            FROM pg_catalog.pg_index AS index_record
            JOIN pg_catalog.pg_class AS table_relation
              ON table_relation.oid = index_record.indrelid
            JOIN pg_catalog.pg_namespace AS namespace
              ON namespace.oid = table_relation.relnamespace
            JOIN pg_catalog.pg_class AS index_relation
              ON index_relation.oid = index_record.indexrelid
            WHERE namespace.nspname = %s
            """,
            (schema,),
        ).fetchall()
        trigger_rows = cursor.execute(
            """
            SELECT relation.relname AS table_name,
                   trigger_record.tgname AS trigger_name,
                   function_namespace.nspname AS function_schema,
                   function_record.proname AS function_name,
                   trigger_record.tgenabled AS enabled,
                   (trigger_record.tgtype & 1) = 1 AS row_level,
                   (trigger_record.tgtype & 2) = 2 AS before_event,
                   (trigger_record.tgtype & 8) = 8 AS delete_event,
                   (trigger_record.tgtype & 16) = 16 AS update_event
            FROM pg_catalog.pg_trigger AS trigger_record
            JOIN pg_catalog.pg_class AS relation
              ON relation.oid = trigger_record.tgrelid
            JOIN pg_catalog.pg_namespace AS namespace
              ON namespace.oid = relation.relnamespace
            JOIN pg_catalog.pg_proc AS function_record
              ON function_record.oid = trigger_record.tgfoid
            JOIN pg_catalog.pg_namespace AS function_namespace
              ON function_namespace.oid = function_record.pronamespace
            WHERE namespace.nspname = %s
              AND NOT trigger_record.tgisinternal
            """,
            (schema,),
        ).fetchall()

    expected_contracts = _expected_v3_column_contracts()
    expected_tables = _expected_v3_tables()
    available: dict[str, dict[str, tuple[str, bool]]] = {}
    for row in column_rows:
        available.setdefault(str(row["table_name"]), {})[str(row["column_name"])] = (
            str(row["formatted_type"]),
            bool(row["not_null"]),
        )
    missing_tables = tuple(sorted(table for table in expected_tables if table not in available))
    missing_columns = {
        table: tuple(sorted(set(columns) - set(available.get(table, {}))))
        for table, columns in expected_tables.items()
        if table in available and set(columns) - set(available[table])
    }
    invalid_columns: dict[str, tuple[str, ...]] = {}
    for table, columns in expected_contracts.items():
        issues: list[str] = []
        for column, (expected_type, expected_not_null) in columns.items():
            actual = available.get(table, {}).get(column)
            if actual is None:
                continue
            actual_type, actual_not_null = actual
            if actual_type != expected_type or actual_not_null != expected_not_null:
                expected_nullability = "NOT NULL" if expected_not_null else "NULL"
                actual_nullability = "NOT NULL" if actual_not_null else "NULL"
                issues.append(
                    f"{column}: expected {expected_type} {expected_nullability}, "
                    f"got {actual_type} {actual_nullability}"
                )
        if issues:
            invalid_columns[table] = tuple(sorted(issues))

    primary_keys = {
        (str(row["table_name"]), tuple(str(value) for value in row["columns"]))
        for row in constraint_rows
        if str(row["constraint_type"]) == "p" and bool(row["is_valid"]) and bool(row["is_ready"])
    }
    missing_primary_keys = tuple(
        sorted(
            f"{table}({', '.join(columns)})"
            for table, columns in SOURCE_V3_PRIMARY_KEYS.items()
            if (table, columns) not in primary_keys
        )
    )

    unique_constraints = {
        (str(row["table_name"]), tuple(str(value) for value in row["columns"]))
        for row in constraint_rows
        if str(row["constraint_type"]) == "u" and bool(row["is_valid"]) and bool(row["is_ready"])
    }
    missing_unique_constraints = tuple(
        sorted(
            f"{table}({', '.join(columns)})"
            for table, expected_constraints in SOURCE_V3_UNIQUE_CONSTRAINTS.items()
            for columns in expected_constraints
            if (table, columns) not in unique_constraints
        )
    )

    foreign_keys = {
        (
            str(row["table_name"]),
            tuple(str(value) for value in row["columns"]),
            str(row["foreign_table_schema"]),
            str(row["foreign_table_name"]),
            tuple(str(value) for value in row["foreign_columns"]),
        )
        for row in constraint_rows
        if str(row["constraint_type"]) == "f"
    }
    missing_foreign_keys = tuple(
        sorted(
            f"{table}({', '.join(columns)}) -> {foreign_table}({', '.join(foreign_columns)})"
            for table, columns, foreign_table, foreign_columns in SOURCE_V3_FOREIGN_KEYS
            if (table, columns, schema, foreign_table, foreign_columns) not in foreign_keys
        )
    )

    available_indexes = {
        str(row["index_name"]): (
            str(row["table_name"]),
            tuple(str(value) for value in row["columns"]),
        )
        for row in index_rows
        if bool(row["is_valid"]) and bool(row["is_ready"])
    }
    missing_indexes = tuple(
        sorted(
            f"{index_name}:{table}({', '.join(columns)})"
            for index_name, (table, columns) in SOURCE_V3_INDEXES.items()
            if available_indexes.get(index_name) != (table, columns)
        )
    )

    valid_triggers = {
        (str(row["table_name"]), str(row["trigger_name"]))
        for row in trigger_rows
        if str(row["function_schema"]) == schema
        and str(row["function_name"]) == "reject_source_v3_snapshot_mutation"
        and str(row["enabled"]) in {"O", "A"}
        and bool(row["row_level"])
        and bool(row["before_event"])
        and bool(row["delete_event"])
        and bool(row["update_event"])
    }
    invalid_immutable_triggers = tuple(
        sorted(
            f"{trigger_name}:{table}"
            for table, trigger_name in SOURCE_V3_IMMUTABLE_TRIGGERS.items()
            if (table, trigger_name) not in valid_triggers
        )
    )

    schema_version = ""
    if "ods_schema_version" not in missing_tables and not {"singleton", "schema_version"} - set(
        available.get("ods_schema_version", {})
    ):
        with connection.cursor() as cursor:
            row = cursor.execute(
                sql.SQL(
                    "SELECT schema_version FROM {}.ods_schema_version WHERE singleton = true"
                ).format(sql.Identifier(schema))
            ).fetchone()
        if row is None:
            raise SourceContractError(
                "source_schema_version_missing", "脱敏源库缺少 ods_schema_version 记录"
            )
        schema_version = str(row["schema_version"])
        if schema_version != expected_version:
            raise SourceContractError(
                "source_schema_version_unsupported",
                f"脱敏源库 Schema 版本 {schema_version} 与产品要求 {expected_version} 不一致",
                {"actual": schema_version, "expected": expected_version},
            )

    return SourceV3ContractInspection(
        schema_version=schema_version,
        database_version=database_version,
        database_version_num=int(connection_state["database_version_num"]),
        current_user=str(connection_state["current_user"]),
        transaction_read_only=bool(connection_state["transaction_read_only"]),
        role_is_privileged=bool(connection_state["role_is_privileged"]),
        tls_active=bool(connection_state["tls_active"]),
        missing_tables=missing_tables,
        missing_columns=missing_columns,
        invalid_columns=invalid_columns,
        missing_primary_keys=missing_primary_keys,
        missing_unique_constraints=missing_unique_constraints,
        missing_foreign_keys=missing_foreign_keys,
        missing_indexes=missing_indexes,
        invalid_immutable_triggers=invalid_immutable_triggers,
    )


def require_valid_source_v3_contract(
    connection: psycopg.Connection,
    *,
    expected_version: str = SOURCE_V3_SCHEMA_VERSION,
    schema: str = SOURCE_V3_SCHEMA,
    require_read_only: bool = True,
) -> SourceV3ContractInspection:
    inspection = inspect_source_v3_contract(
        connection, expected_version=expected_version, schema=schema
    )
    if not inspection.valid:
        raise SourceContractError(
            "source_schema_invalid",
            "脱敏源库不符合 ODS 3.0 字段契约",
            {
                "missing_tables": inspection.missing_tables,
                "missing_columns": inspection.missing_columns,
                "invalid_columns": inspection.invalid_columns,
                "missing_primary_keys": inspection.missing_primary_keys,
                "missing_unique_constraints": inspection.missing_unique_constraints,
                "missing_foreign_keys": inspection.missing_foreign_keys,
                "missing_indexes": inspection.missing_indexes,
                "invalid_immutable_triggers": inspection.invalid_immutable_triggers,
            },
        )
    if not 150000 <= inspection.database_version_num < 180000:
        raise SourceContractError(
            "source_postgres_version_unsupported",
            "脱敏源库仅支持 PostgreSQL 15 至 17",
            {"database_version": inspection.database_version},
        )
    if inspection.role_is_privileged:
        raise SourceContractError(
            "source_role_privileged",
            "脱敏源库账号不得拥有超级用户、建库、建角色或复制权限",
            {"current_user": inspection.current_user},
        )
    if require_read_only and not inspection.transaction_read_only:
        raise SourceContractError(
            "source_role_not_read_only",
            "产品连接脱敏源库时必须处于只读事务模式",
            {"current_user": inspection.current_user},
        )
    return inspection


def _canonical_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def _stable_code(prefix: str, value: object) -> str:
    text = str(value).strip()
    return f"{prefix}-{hashlib.sha256(text.encode('utf-8')).hexdigest()[:24]}"


def _organization_code(value: object) -> str:
    text = str(value).strip()
    return ORGANIZATION_CODE_OVERRIDES.get(text) or _stable_code("division", text)


def _split_values(value: object) -> list[str]:
    if value in (None, ""):
        return []
    values = value if isinstance(value, (list, tuple, set)) else [value]
    output: list[str] = []
    for raw in values:
        for item in re.split(r"[|、，,;\n]+", str(raw)):
            text = item.strip()
            if text and text not in output:
                output.append(text)
    return output


def _timestamp(value: object, *, fallback: datetime) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, date):
        return datetime.combine(value, time.min, tzinfo=UTC)
    return fallback


def _ratio(value: object) -> Decimal:
    number = Decimal(str(value or 0))
    if number > 1:
        number /= Decimal("100")
    return max(Decimal("0"), min(Decimal("1"), number))


def _boolean(value: object, *, field: str, record_id: str) -> bool:
    normalized = str(value or "").strip().lower()
    if normalized in {"是", "true", "1", "yes", "y", "已归档"}:
        return True
    if normalized in {"否", "false", "0", "no", "n", "", "未归档"}:
        return False
    raise SourceContractError(
        "source_boolean_invalid",
        f"{field}字段不是可识别的布尔值",
        {"record_id": record_id, "field": field, "value": value},
    )


def _opportunity_status(stage: object) -> str:
    label = str(stage).strip()
    if label == "赢单":
        return "won"
    if label == "搁置":
        return "paused"
    if label == "归档":
        return "archived"
    if label in ACTIVE_OPPORTUNITY_STAGES:
        return "active"
    raise SourceContractError(
        "source_opportunity_stage_invalid",
        "商机当前阶段不在已发布的标准映射中",
        {"value": label},
    )


def _delivery_status(status: object) -> str:
    label = str(status).strip()
    mapped = DELIVERY_STATUS_CODES.get(label)
    if mapped is None:
        raise SourceContractError(
            "source_delivery_status_invalid",
            "项目状态不在已发布的标准映射中",
            {"value": label},
        )
    return mapped


def _binding_schema(binding: Any) -> dict[str, Any]:
    return {
        "domain": str(binding.domain),
        "app_token": str(binding.app_token),
        "table_id": str(binding.table_id),
        "fields": [
            {
                "key": str(field.key),
                "field_id": str(field.field_id),
                "field_name": str(field.field_name),
                "field_type": int(field.field_type),
                "required": bool(field.required),
            }
            for field in binding.fields
        ],
    }


def table_schema_hashes(bindings: Sequence[Any]) -> dict[str, str]:
    return {str(binding.domain): _canonical_hash(_binding_schema(binding)) for binding in bindings}


def prepare_source_v3_batch(
    snapshot: Any,
    bindings: Sequence[Any],
    *,
    batch_id: str | None = None,
) -> SourceV3PreparedBatch:
    if not snapshot.validation.get("valid"):
        raise SourceContractError("source_snapshot_invalid", "三表校验未通过")
    schema_hashes = table_schema_hashes(bindings)
    missing_bindings = sorted(set(SOURCE_V3_DOMAINS) - set(schema_hashes))
    if missing_bindings:
        raise SourceContractError(
            "source_binding_incomplete",
            "三张飞书表必须同时绑定",
            {"missing_domains": missing_bindings},
        )
    if batch_id is None:
        identity = _canonical_hash(
            {"content_sha256": snapshot.content_sha256, "schema_hashes": schema_hashes}
        )
        batch_id = f"feishu-v3-{identity[:24]}"
    fetched_at = _timestamp(snapshot.fetched_at, fallback=datetime.now(UTC))

    opportunities: list[dict[str, Any]] = []
    for item in snapshot.opportunities:
        opportunity_id = str(item["opportunity_id"])
        stage_label = str(item["stage"])
        status_code = _opportunity_status(stage_label)
        reliability = RELIABILITY_CODES.get(str(item["reliability"]).strip().lower())
        if reliability is None:
            raise SourceContractError(
                "source_reliability_invalid",
                "商机靠谱度只允许高、中、低",
                {"record_id": opportunity_id, "value": item.get("reliability")},
            )
        signed_amount = item.get("signed_amount")
        if status_code == "won" and signed_amount is None:
            raise SourceContractError(
                "source_won_amount_missing",
                "赢单商机必须提供签约金额",
                {"record_id": opportunity_id},
            )
        if status_code != "won" and signed_amount not in (None, Decimal("0"), 0, "0"):
            raise SourceContractError(
                "source_non_won_amount_present",
                "非赢单商机不应填写签约金额",
                {"record_id": opportunity_id},
            )
        is_archived = _boolean(
            item.get("is_archived"), field="is_archived", record_id=opportunity_id
        )
        if is_archived and item.get("archived_date") is None:
            raise SourceContractError(
                "source_archive_date_missing",
                "已归档商机必须提供归档时间",
                {"record_id": opportunity_id},
            )
        if not is_archived and item.get("archived_date") is not None:
            raise SourceContractError(
                "source_archive_state_inconsistent",
                "未归档商机不应填写归档时间",
                {"record_id": opportunity_id},
            )
        source_updated_at = _timestamp(item.get("source_modified_at"), fallback=fetched_at)
        organization_name = str(item["organization_name"]).strip()
        opportunities.append(
            {
                "load_batch_id": batch_id,
                "source_system": SOURCE_V3_SYSTEM,
                "source_record_id": opportunity_id,
                "source_native_record_id": str(item["source_native_record_id"]),
                "source_updated_at": source_updated_at,
                "is_deleted": False,
                "legacy_source_record_id": item.get("source_record_id"),
                "opportunity_code": opportunity_id,
                "organization_code": _organization_code(organization_name),
                "organization_name": organization_name,
                "title": str(item["opportunity_name"]).strip(),
                "customer_name": str(item["customer_name"]).strip(),
                "customer_value_level": str(item["customer_value_level"]).strip(),
                "sales_owner": str(item["sales_owner"]).strip(),
                "presales_owners": _split_values(item.get("presales_owners")),
                "reliability_level": reliability,
                "stage_label": stage_label,
                "status_code": status_code,
                "expected_amount": item["expected_amount"],
                "signed_amount": signed_amount if status_code == "won" else None,
                "expected_close_date": item["expected_close_date"],
                "entered_date": item["entered_date"],
                "products_services": _split_values(item.get("products_services")),
                "latest_progress": item.get("latest_progress"),
                "industry": str(item["industry"]).strip(),
                "is_archived": is_archived,
                "archived_at": item.get("archived_date"),
            }
        )

    deliveries: list[dict[str, Any]] = []
    for item in snapshot.deliveries:
        project_id = str(item["project_id"])
        source_updated_at = _timestamp(item.get("source_modified_at"), fallback=fetched_at)
        organization_name = str(item["organization_name"]).strip()
        deliveries.append(
            {
                "load_batch_id": batch_id,
                "source_system": SOURCE_V3_SYSTEM,
                "source_record_id": project_id,
                "source_native_record_id": str(item["source_native_record_id"]),
                "source_updated_at": source_updated_at,
                "is_deleted": False,
                "project_code": project_id,
                "opportunity_code": str(item["opportunity_id"]),
                "opportunity_name": str(item["opportunity_name"]).strip(),
                "customer_name": str(item["customer_name"]).strip(),
                "organization_code": _organization_code(organization_name),
                "organization_name": organization_name,
                "project_name": str(item["project_name"]).strip(),
                "project_manager": str(item["project_manager"]).strip(),
                "delivery_owners": _split_values(item.get("delivery_owner")),
                "status_label": str(item["status"]).strip(),
                "status_code": _delivery_status(item["status"]),
                "risk_level": str(item["risk_level"]).strip(),
                "contract_amount": item["contract_amount"],
                "recognized_revenue": item["recognized_revenue"],
                "gross_margin_rate": _ratio(item.get("gross_margin_rate")),
                "planned_start_date": item["planned_start_date"],
                "planned_end_date": item["planned_end_date"],
                "actual_start_date": item.get("actual_start_date"),
                "actual_end_date": item.get("actual_end_date"),
                "current_milestone": item.get("current_milestone"),
                "completion_rate": _ratio(item.get("completion_rate")),
                "delay_days": int(item.get("delay_days") or 0),
                "latest_progress": item.get("latest_progress"),
                "data_updated_at": _timestamp(
                    item.get("source_updated_date"), fallback=source_updated_at
                ),
            }
        )

    collections: list[dict[str, Any]] = []
    for item in snapshot.collections:
        collection_id = str(item["collection_id"])
        source_updated_at = _timestamp(item.get("source_modified_at"), fallback=fetched_at)
        organization_name = str(item["organization_name"]).strip()
        receivable = item["receivable_amount"]
        collected = item["collected_amount"]
        outstanding = item["outstanding_amount"]
        if receivable != collected + outstanding:
            raise SourceContractError(
                "source_financial_invariant_failed",
                "应收金额必须等于已回款金额与未回款金额之和",
                {"record_id": collection_id},
            )
        collections.append(
            {
                "load_batch_id": batch_id,
                "source_system": SOURCE_V3_SYSTEM,
                "source_record_id": collection_id,
                "source_native_record_id": str(item["source_native_record_id"]),
                "source_updated_at": source_updated_at,
                "is_deleted": False,
                "collection_code": collection_id,
                "opportunity_code": str(item["opportunity_id"]),
                "project_code": str(item["project_id"]),
                "customer_name": str(item["customer_name"]).strip(),
                "organization_code": _organization_code(organization_name),
                "organization_name": organization_name,
                "payment_type": str(item["payment_type"]).strip(),
                "payment_milestone": str(item["payment_milestone"]).strip(),
                "receivable_amount": receivable,
                "planned_collection_date": item["planned_collection_date"],
                "actual_collection_date": item.get("actual_collection_date"),
                "collected_amount": collected,
                "outstanding_amount": outstanding,
                "status_label": str(item["status"]).strip(),
                "overdue_days": int(item.get("overdue_days") or 0),
                "aging_bucket": str(item["aging_bucket"]).strip(),
                "invoice_status": str(item["invoice_status"]).strip(),
                "invoice_number": item.get("invoice_number"),
                "collection_owner": str(item["collection_owner"]).strip(),
                "latest_follow_up": item.get("latest_follow_up"),
                "data_updated_at": _timestamp(
                    item.get("source_updated_date"), fallback=source_updated_at
                ),
            }
        )

    # Keep the ODS payload and its fingerprints independent from upstream
    # pagination order.  This is also applied here (in addition to the Feishu
    # connector) because customer-side importers may construct snapshots
    # directly.
    rows = {
        "opportunity": tuple(sorted(opportunities, key=lambda row: row["source_record_id"])),
        "delivery": tuple(sorted(deliveries, key=lambda row: row["source_record_id"])),
        "collection": tuple(sorted(collections, key=lambda row: row["source_record_id"])),
    }
    table_content = {domain: _canonical_hash(rows[domain]) for domain in SOURCE_V3_DOMAINS}
    reference_date = max(
        (
            row["data_updated_at"].date()
            for domain in ("delivery", "collection")
            for row in rows[domain]
        ),
        default=fetched_at.date(),
    )
    source_data_as_of = max(
        (
            row.get("data_updated_at") or row["source_updated_at"]
            for domain in SOURCE_V3_DOMAINS
            for row in rows[domain]
        ),
        default=fetched_at,
    )
    return SourceV3PreparedBatch(
        batch_id=batch_id,
        reference_date=reference_date,
        source_data_as_of=source_data_as_of,
        rows=rows,
        table_content_sha256=table_content,
        table_schema_sha256=schema_hashes,
        content_sha256=str(snapshot.content_sha256),
        validation={**snapshot.validation, "contract_version": SOURCE_V3_SCHEMA_VERSION},
    )


def _insert_immutable_rows(
    connection: psycopg.Connection,
    *,
    schema: str,
    table: str,
    columns: tuple[str, ...],
    rows: Iterable[dict[str, Any]],
) -> int:
    materialized = list(rows)
    if not materialized:
        return 0
    statement = sql.SQL("INSERT INTO {}.{} ({}) VALUES ({})").format(
        sql.Identifier(schema),
        sql.Identifier(table),
        sql.SQL(", ").join(map(sql.Identifier, columns)),
        sql.SQL(", ").join(sql.Placeholder(column) for column in columns),
    )
    with connection.cursor() as cursor:
        cursor.executemany(statement, materialized)
    return len(materialized)


def _source_v3_batch_payload_matches(
    row: dict[str, Any],
    prepared: SourceV3PreparedBatch,
    *,
    dataset_version: str,
) -> bool:
    """Return whether a persisted batch represents the same upstream snapshot.

    ``content_sha256`` is the canonical hash produced by the connector and the
    schema hashes bind that content to the exact three-table field contract.
    Batch IDs and load-batch foreign keys are deliberately excluded: a source
    can legitimately return to content that was activated earlier.
    """

    return (
        str(row.get("content_sha256") or "").strip() == prepared.content_sha256
        and dict(row.get("table_schema_sha256") or {}) == prepared.table_schema_sha256
        and str(row.get("dataset_version") or "") == dataset_version
    )


def _rekey_source_v3_batch(
    prepared: SourceV3PreparedBatch,
    *,
    batch_id: str,
) -> SourceV3PreparedBatch:
    """Clone an immutable snapshot under a new event identity.

    The business content and content fingerprints remain unchanged. Only the
    batch foreign key carried by each ODS row changes.
    """

    if batch_id == prepared.batch_id:
        return prepared
    return SourceV3PreparedBatch(
        batch_id=batch_id,
        reference_date=prepared.reference_date,
        source_data_as_of=prepared.source_data_as_of,
        rows={
            domain: tuple({**row, "load_batch_id": batch_id} for row in prepared.rows[domain])
            for domain in SOURCE_V3_DOMAINS
        },
        table_content_sha256=dict(prepared.table_content_sha256),
        table_schema_sha256=dict(prepared.table_schema_sha256),
        content_sha256=prepared.content_sha256,
        validation=dict(prepared.validation),
    )


def _source_v3_replay_batch_id(base_batch_id: str, replay_number: int) -> str:
    suffix = f"-replay-{replay_number:06d}"
    return f"{base_batch_id[: 160 - len(suffix)]}{suffix}"


def write_source_v3_batch(
    connection: psycopg.Connection,
    prepared: SourceV3PreparedBatch,
    bindings: Sequence[Any],
    *,
    dataset_version: str,
    schema: str = SOURCE_V3_SCHEMA,
) -> dict[str, Any]:
    """Append one complete V3 source snapshot.

    Replaying the newest usable content is idempotent. Existing content is
    never updated or deleted. If the source deliberately returns to content
    from a superseded historical batch, a new replay batch is appended so that
    the reverted state can become the newest ready snapshot.
    """

    require_valid_source_v3_contract(connection, schema=schema, require_read_only=False)
    binding_by_domain = {str(binding.domain): binding for binding in bindings}
    with connection.transaction():
        with connection.cursor() as cursor:
            # Serialize the complete identity decision and append. Without
            # this lock, two workers could both allocate the same replay
            # suffix or append duplicate snapshots for one upstream state.
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"{schema}:source-v3-write",),
            )
            current = cursor.execute(
                sql.SQL(
                    """
                    SELECT batch_id, content_sha256, table_schema_sha256,
                           dataset_version, status
                    FROM {}.source_batches
                    WHERE status IN ('ready', 'activated')
                    ORDER BY completed_at DESC NULLS LAST, created_at DESC, batch_id DESC
                    LIMIT 1
                    """
                ).format(sql.Identifier(schema))
            ).fetchone()
            if current is not None and _source_v3_batch_payload_matches(
                dict(current), prepared, dataset_version=dataset_version
            ):
                return {
                    "batch_id": str(current["batch_id"]),
                    "record_counts": prepared.record_counts,
                    "content_sha256": prepared.content_sha256,
                    "table_content_sha256": prepared.table_content_sha256,
                    "table_schema_sha256": prepared.table_schema_sha256,
                    "status": str(current["status"]),
                    "idempotent": True,
                }

            existing = cursor.execute(
                sql.SQL(
                    "SELECT content_sha256, table_schema_sha256, dataset_version, status "
                    "FROM {}.source_batches WHERE batch_id = %s"
                ).format(sql.Identifier(schema)),
                (prepared.batch_id,),
            ).fetchone()
            effective = prepared
            if existing is not None:
                if not _source_v3_batch_payload_matches(
                    dict(existing), prepared, dataset_version=dataset_version
                ):
                    if str(existing["content_sha256"]).strip() != prepared.content_sha256:
                        code = "source_batch_hash_conflict"
                        message = "相同批次 ID 对应了不同内容"
                    elif (
                        dict(existing["table_schema_sha256"] or {}) != prepared.table_schema_sha256
                    ):
                        code = "source_batch_schema_conflict"
                        message = "相同批次 ID 对应了不同字段契约"
                    else:
                        code = "source_batch_dataset_conflict"
                        message = "相同批次 ID 对应了不同数据集版本"
                    raise SourceContractError(code, message, {"batch_id": prepared.batch_id})
                if str(existing["status"]) not in {
                    "validated",
                    "ready",
                    "activated",
                    "superseded",
                }:
                    raise SourceContractError(
                        "source_batch_not_replayable",
                        "已存在的未成功批次不允许覆盖",
                        {"batch_id": prepared.batch_id, "status": str(existing["status"])},
                    )

                # The same payload exists, but it is no longer the newest
                # usable state. This is a real source rollback, not an
                # idempotent retry, so append a new event identity.
                replay_number = 1
                while True:
                    candidate = _source_v3_replay_batch_id(prepared.batch_id, replay_number)
                    occupied = cursor.execute(
                        sql.SQL("SELECT 1 FROM {}.source_batches WHERE batch_id = %s").format(
                            sql.Identifier(schema)
                        ),
                        (candidate,),
                    ).fetchone()
                    if occupied is None:
                        effective = _rekey_source_v3_batch(prepared, batch_id=candidate)
                        break
                    replay_number += 1

            inserted = cursor.execute(
                sql.SQL(
                    """
                    INSERT INTO {}.source_batches (
                        batch_id, source_system, dataset_version, reference_date,
                        source_data_as_of, status, record_counts,
                        table_content_sha256, table_schema_sha256, content_sha256,
                        validation_result
                    ) VALUES (
                        %s, %s, %s, %s, %s, 'building', %s::jsonb,
                        %s::jsonb, %s::jsonb, %s, %s::jsonb
                    ) ON CONFLICT (batch_id) DO NOTHING
                    RETURNING batch_id
                    """
                ).format(sql.Identifier(schema)),
                (
                    effective.batch_id,
                    SOURCE_V3_SYSTEM,
                    dataset_version,
                    effective.reference_date,
                    effective.source_data_as_of,
                    json.dumps(effective.record_counts, ensure_ascii=False),
                    json.dumps(effective.table_content_sha256, ensure_ascii=False),
                    json.dumps(effective.table_schema_sha256, ensure_ascii=False),
                    effective.content_sha256,
                    json.dumps(effective.validation, ensure_ascii=False),
                ),
            ).fetchone()
            if inserted is None:
                raise SourceContractError(
                    "source_batch_concurrent_retry",
                    "同一 ODS 3.0 批次正在由另一个任务写入，请重试",
                    {"batch_id": effective.batch_id},
                )
            for domain in SOURCE_V3_DOMAINS:
                binding = binding_by_domain[domain]
                field_mapping = {str(field.key): str(field.field_id) for field in binding.fields}
                field_types = {str(field.key): int(field.field_type) for field in binding.fields}
                cursor.execute(
                    sql.SQL(
                        """
                        INSERT INTO {}.source_table_bindings (
                            load_batch_id, domain, source_system, app_token, table_id,
                            display_name, field_mapping, field_types, schema_sha256,
                            record_count, validated_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, now())
                        """
                    ).format(sql.Identifier(schema)),
                    (
                        effective.batch_id,
                        domain,
                        SOURCE_V3_SYSTEM,
                        str(binding.app_token),
                        str(binding.table_id),
                        str(binding.display_name),
                        json.dumps(field_mapping, ensure_ascii=False),
                        json.dumps(field_types, ensure_ascii=False),
                        effective.table_schema_sha256[domain],
                        effective.record_counts[domain],
                    ),
                )
        for domain in SOURCE_V3_DOMAINS:
            _insert_immutable_rows(
                connection,
                schema=schema,
                table=SOURCE_V3_TABLES[domain],
                columns=SOURCE_V3_COLUMNS[domain],
                rows=effective.rows[domain],
            )
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL(
                    """
                    UPDATE {}.source_batches
                    SET status = 'ready', validated_at = now(), completed_at = now()
                    WHERE batch_id = %s AND status = 'building'
                    """
                ).format(sql.Identifier(schema)),
                (effective.batch_id,),
            )
            for domain in SOURCE_V3_DOMAINS:
                binding = binding_by_domain[domain]
                cursor.execute(
                    sql.SQL(
                        """
                        INSERT INTO {}.source_sync_checkpoints (
                            source_system, domain, app_token, table_id, last_batch_id,
                            next_page_token, source_updated_at, content_sha256, synchronized_at
                        ) VALUES (%s, %s, %s, %s, %s, NULL, %s, %s, now())
                        ON CONFLICT (source_system, domain, app_token, table_id)
                        DO UPDATE SET
                            last_batch_id = EXCLUDED.last_batch_id,
                            next_page_token = NULL,
                            source_updated_at = EXCLUDED.source_updated_at,
                            content_sha256 = EXCLUDED.content_sha256,
                            synchronized_at = now()
                        """
                    ).format(sql.Identifier(schema)),
                    (
                        SOURCE_V3_SYSTEM,
                        domain,
                        str(binding.app_token),
                        str(binding.table_id),
                        effective.batch_id,
                        effective.source_data_as_of,
                        effective.table_content_sha256[domain],
                    ),
                )
    return {
        "batch_id": effective.batch_id,
        "record_counts": effective.record_counts,
        "content_sha256": effective.content_sha256,
        "table_content_sha256": effective.table_content_sha256,
        "table_schema_sha256": effective.table_schema_sha256,
        "status": "ready",
        "idempotent": False,
    }


def write_source_v3_snapshot(
    snapshot: Any,
    bindings: Sequence[Any],
    *,
    source_writer_database_url: str,
    dataset_version: str,
    schema: str = SOURCE_V3_SCHEMA,
    batch_id: str | None = None,
) -> dict[str, Any]:
    prepared = prepare_source_v3_batch(snapshot, bindings, batch_id=batch_id)
    with connect_source(
        source_writer_database_url,
        application_name="executive-ai-feishu-v3-importer",
        read_only=False,
    ) as connection:
        return write_source_v3_batch(
            connection,
            prepared,
            bindings,
            dataset_version=dataset_version,
            schema=schema,
        )


def record_rejected_source_v3_batch(
    connection: psycopg.Connection,
    *,
    batch_id: str,
    dataset_version: str,
    source_data_as_of: datetime,
    content_sha256: str,
    issues: Sequence[dict[str, Any]],
    schema: str = SOURCE_V3_SCHEMA,
) -> None:
    """Persist a rejected batch without inserting any ODS facts."""

    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                INSERT INTO {}.source_batches (
                    batch_id, source_system, dataset_version, reference_date,
                    source_data_as_of, status, content_sha256, validation_result,
                    completed_at
                ) VALUES (%s, %s, %s, %s, %s, 'rejected', %s, %s::jsonb, now())
                ON CONFLICT (batch_id) DO NOTHING
                """
            ).format(sql.Identifier(schema)),
            (
                batch_id,
                SOURCE_V3_SYSTEM,
                dataset_version,
                source_data_as_of.date(),
                source_data_as_of,
                content_sha256,
                json.dumps({"valid": False, "issue_count": len(issues)}, ensure_ascii=False),
            ),
        )
        for issue in issues:
            cursor.execute(
                sql.SQL(
                    """
                    INSERT INTO {}.source_validation_issues (
                        load_batch_id, severity, domain, source_record_id,
                        field_name, error_code, message, details
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                    """
                ).format(sql.Identifier(schema)),
                (
                    batch_id,
                    str(issue.get("severity") or "error"),
                    issue.get("domain") or "batch",
                    issue.get("source_record_id"),
                    issue.get("field_name"),
                    str(issue.get("error_code") or "source_validation_failed"),
                    str(issue.get("message") or "源数据校验失败"),
                    json.dumps(issue.get("details") or {}, ensure_ascii=False),
                ),
            )


def latest_ready_source_v3_batch(
    connection: psycopg.Connection,
    *,
    schema: str = SOURCE_V3_SCHEMA,
) -> dict[str, Any]:
    with connection.cursor() as cursor:
        row = cursor.execute(
            sql.SQL(
                """
                SELECT batch_id, source_system, dataset_version, reference_date,
                       source_data_as_of, record_counts, table_content_sha256,
                       table_schema_sha256, content_sha256, validation_result,
                       created_at, completed_at
                FROM {}.source_batches
                WHERE status IN ('ready', 'activated')
                ORDER BY completed_at DESC NULLS LAST, created_at DESC, batch_id DESC
                LIMIT 1
                """
            ).format(sql.Identifier(schema))
        ).fetchone()
    if row is None:
        raise SourceContractError("source_batch_missing", "ODS 3.0 没有可用的成功批次")
    return dict(row)


def iter_source_v3_rows(
    connection: psycopg.Connection,
    domain: str,
    *,
    batch_id: str,
    schema: str = SOURCE_V3_SCHEMA,
    page_size: int = 1000,
) -> Iterator[list[dict[str, Any]]]:
    if domain not in SOURCE_V3_TABLES:
        raise SourceContractError("source_domain_invalid", f"未知数据域: {domain}")
    last_id = 0
    columns = SOURCE_V3_COLUMNS[domain]
    while True:
        statement = sql.SQL(
            "SELECT id, {} FROM {}.{} WHERE load_batch_id = %s AND id > %s ORDER BY id LIMIT %s"
        ).format(
            sql.SQL(", ").join(map(sql.Identifier, columns)),
            sql.Identifier(schema),
            sql.Identifier(SOURCE_V3_TABLES[domain]),
        )
        with connection.cursor() as cursor:
            rows = cursor.execute(statement, (batch_id, last_id, page_size)).fetchall()
        if not rows:
            return
        page = [dict(row) for row in rows]
        last_id = int(page[-1]["id"])
        for row in page:
            row.pop("id", None)
        yield page


def mark_source_v3_batch_activated(
    *,
    source_writer_database_url: str,
    batch_id: str,
    schema: str = SOURCE_V3_SCHEMA,
) -> None:
    """Complete the source/product cross-database saga after product commit.

    This operation is intentionally idempotent. It is only available when the
    deployment owns a separate source-writer credential; customer read-only
    sources keep `ready` and rely on the product activation audit trail.
    """

    with connect_source(
        source_writer_database_url,
        application_name="executive-ai-source-v3-activation-marker",
        read_only=False,
    ) as connection:
        require_valid_source_v3_contract(connection, schema=schema, require_read_only=False)
        with connection.transaction(), connection.cursor() as cursor:
            # Serialize the source-side half of the activation saga.  Product
            # activation is already serialized by the DataSource row lock;
            # this lock plus the monotonic completed_at check prevents a slow
            # older job from superseding a newer source batch afterwards.
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"{schema}:source-v3-activation",),
            )
            row = cursor.execute(
                sql.SQL(
                    "SELECT status, completed_at, created_at FROM {}.source_batches "
                    "WHERE batch_id = %s FOR UPDATE"
                ).format(sql.Identifier(schema)),
                (batch_id,),
            ).fetchone()
            if row is None:
                raise SourceContractError(
                    "source_batch_missing",
                    "待标记的 ODS 3.0 批次不存在",
                    {"batch_id": batch_id},
                )
            if str(row["status"]) == "activated":
                return
            if str(row["status"]) == "superseded":
                return
            if str(row["status"]) not in {"ready", "validated"}:
                raise SourceContractError(
                    "source_batch_not_activatable",
                    "ODS 3.0 批次状态不允许激活",
                    {"batch_id": batch_id, "status": str(row["status"])},
                )
            current = cursor.execute(
                sql.SQL(
                    "SELECT batch_id, completed_at, created_at FROM {}.source_batches "
                    "WHERE status = 'activated' ORDER BY completed_at DESC NULLS LAST, "
                    "created_at DESC, batch_id DESC LIMIT 1 FOR UPDATE"
                ).format(sql.Identifier(schema))
            ).fetchone()
            candidate_order = (
                row["completed_at"] or row["created_at"],
                row["created_at"],
                batch_id,
            )
            if current is not None:
                current_order = (
                    current["completed_at"] or current["created_at"],
                    current["created_at"],
                    str(current["batch_id"]),
                )
                if current_order > candidate_order:
                    cursor.execute(
                        sql.SQL(
                            "UPDATE {}.source_batches SET status = 'superseded' "
                            "WHERE batch_id = %s AND status IN ('ready', 'validated')"
                        ).format(sql.Identifier(schema)),
                        (batch_id,),
                    )
                    return
            cursor.execute(
                sql.SQL(
                    "UPDATE {}.source_batches SET status = 'superseded' "
                    "WHERE status = 'activated' AND batch_id <> %s"
                ).format(sql.Identifier(schema)),
                (batch_id,),
            )
            cursor.execute(
                sql.SQL(
                    "UPDATE {}.source_batches SET status = 'activated', activated_at = now() "
                    "WHERE batch_id = %s"
                ).format(sql.Identifier(schema)),
                (batch_id,),
            )


def source_v3_domain_fingerprint(rows: Iterable[dict[str, Any]]) -> str:
    materialized = list(rows)
    if all(
        isinstance(row, dict) and row.get("source_record_id") is not None for row in materialized
    ):
        materialized.sort(key=lambda row: str(row["source_record_id"]))
    return _canonical_hash(materialized)
