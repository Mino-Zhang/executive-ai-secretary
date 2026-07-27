from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from .demo_dataset import DEMO_SOURCE_SCHEMA_VERSION, DemoDataset

SOURCE_SCHEMA = "executive_source"

SOURCE_COLUMNS: dict[str, tuple[str, ...]] = {
    "organizations": (
        "source_system",
        "source_record_id",
        "source_updated_at",
        "load_batch_id",
        "is_deleted",
        "organization_code",
        "parent_organization_code",
        "display_name",
        "unit_type",
        "sort_order",
    ),
    "people": (
        "source_system",
        "source_record_id",
        "source_updated_at",
        "load_batch_id",
        "is_deleted",
        "organization_code",
        "display_name",
        "role_title",
        "is_active",
    ),
    "customers": (
        "source_system",
        "source_record_id",
        "source_updated_at",
        "load_batch_id",
        "is_deleted",
        "organization_code",
        "owner_person_record_id",
        "display_name",
        "industry",
        "region",
        "customer_since",
    ),
    "opportunities": (
        "source_system",
        "source_record_id",
        "source_updated_at",
        "load_batch_id",
        "is_deleted",
        "organization_code",
        "customer_record_id",
        "owner_person_record_id",
        "opportunity_code",
        "title",
        "stage",
        "status",
        "probability",
        "expected_amount",
        "expected_gross_profit",
        "created_date",
        "expected_close_date",
        "closed_date",
    ),
    "deliveries": (
        "source_system",
        "source_record_id",
        "source_updated_at",
        "load_batch_id",
        "is_deleted",
        "organization_code",
        "opportunity_record_id",
        "customer_record_id",
        "manager_person_record_id",
        "project_code",
        "project_name",
        "status",
        "risk_level",
        "completion_percent",
        "contract_amount",
        "gross_margin_rate",
        "planned_start_date",
        "planned_end_date",
        "actual_end_date",
        "current_milestone",
        "delay_days",
    ),
    "collections": (
        "source_system",
        "source_record_id",
        "source_updated_at",
        "load_batch_id",
        "is_deleted",
        "organization_code",
        "project_record_id",
        "customer_record_id",
        "invoice_amount",
        "receivable_amount",
        "collected_amount",
        "planned_collection_date",
        "actual_collection_date",
        "overdue_days",
        "aging_bucket",
        "status",
    ),
    "targets": (
        "source_system",
        "source_record_id",
        "source_updated_at",
        "load_batch_id",
        "is_deleted",
        "organization_code",
        "metric_code",
        "metric_name",
        "period_type",
        "period_start",
        "period_end",
        "target_value",
        "unit",
    ),
}

SOURCE_TABLES = {
    "organizations": "ods_organization_unit",
    "people": "ods_person",
    "customers": "ods_customer",
    "opportunities": "ods_opportunity",
    "deliveries": "ods_delivery",
    "collections": "ods_collection",
    "targets": "ods_target",
}

DATA_DOMAINS = ("opportunity", "delivery", "collection", "target")


class SourceContractError(RuntimeError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        self.code = code
        self.details = details or {}
        super().__init__(message)


@dataclass(frozen=True)
class SourceContractInspection:
    schema_version: str
    database_version: str
    database_version_num: int
    current_user: str
    transaction_read_only: bool
    role_is_privileged: bool
    tls_active: bool
    missing_tables: tuple[str, ...]
    missing_columns: dict[str, tuple[str, ...]]

    @property
    def valid(self) -> bool:
        return not self.missing_tables and not self.missing_columns


def psycopg_dsn(value: str) -> str:
    return value.replace("postgresql+psycopg://", "postgresql://", 1)


def connect_source(
    database_url: str,
    *,
    application_name: str,
    read_only: bool = True,
) -> psycopg.Connection:
    options = [
        "-c statement_timeout=120000",
        f"-c application_name={application_name}",
    ]
    if read_only:
        options.append("-c default_transaction_read_only=on")
    return psycopg.connect(
        psycopg_dsn(database_url),
        autocommit=False,
        row_factory=dict_row,
        options=" ".join(options),
    )


def inspect_source_contract(
    connection: psycopg.Connection,
    *,
    expected_version: str = DEMO_SOURCE_SCHEMA_VERSION,
    schema: str = SOURCE_SCHEMA,
) -> SourceContractInspection:
    expected_tables = {
        "ods_schema_version": ("schema_version",),
        "source_batches": (
            "batch_id",
            "source_system",
            "dataset_version",
            "reference_date",
            "source_data_as_of",
            "status",
        ),
        **{SOURCE_TABLES[domain]: SOURCE_COLUMNS[domain] for domain in SOURCE_TABLES},
    }
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
            raise SourceContractError(
                "source_role_missing",
                "无法确认脱敏源库连接账号",
            )
        rows = cursor.execute(
            """
            SELECT table_name, column_name
            FROM information_schema.columns
            WHERE table_schema = %s
            """,
            (schema,),
        ).fetchall()
    available: dict[str, set[str]] = {}
    for row in rows:
        available.setdefault(str(row["table_name"]), set()).add(str(row["column_name"]))
    missing_tables = tuple(sorted(table for table in expected_tables if table not in available))
    missing_columns = {
        table: tuple(sorted(set(columns) - available.get(table, set())))
        for table, columns in expected_tables.items()
        if table in available and set(columns) - available[table]
    }
    schema_version = ""
    if "ods_schema_version" not in missing_tables and "ods_schema_version" not in missing_columns:
        with connection.cursor() as cursor:
            version_row = cursor.execute(
                sql.SQL(
                    "SELECT schema_version FROM {}.ods_schema_version WHERE singleton = true"
                ).format(sql.Identifier(schema))
            ).fetchone()
        if version_row is None:
            raise SourceContractError(
                "source_schema_version_missing",
                "脱敏源库缺少 ods_schema_version 记录",
            )
        schema_version = str(version_row["schema_version"])
        if schema_version != expected_version:
            raise SourceContractError(
                "source_schema_version_unsupported",
                f"脱敏源库 Schema 版本 {schema_version} 与产品要求 {expected_version} 不一致",
                {"actual": schema_version, "expected": expected_version},
            )
    return SourceContractInspection(
        schema_version=schema_version,
        database_version=database_version,
        database_version_num=int(connection_state["database_version_num"]),
        current_user=str(connection_state["current_user"]),
        transaction_read_only=bool(connection_state["transaction_read_only"]),
        role_is_privileged=bool(connection_state["role_is_privileged"]),
        tls_active=bool(connection_state["tls_active"]),
        missing_tables=missing_tables,
        missing_columns=missing_columns,
    )


def require_valid_source_contract(
    connection: psycopg.Connection,
    *,
    expected_version: str = DEMO_SOURCE_SCHEMA_VERSION,
    schema: str = SOURCE_SCHEMA,
    require_read_only: bool = True,
) -> SourceContractInspection:
    inspection = inspect_source_contract(
        connection,
        expected_version=expected_version,
        schema=schema,
    )
    if not inspection.valid:
        raise SourceContractError(
            "source_schema_invalid",
            "脱敏源库不符合标准字段契约",
            {
                "missing_tables": inspection.missing_tables,
                "missing_columns": inspection.missing_columns,
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


def _upsert_rows(
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
    insert = sql.SQL("INSERT INTO {}.{} ({}) VALUES ({})").format(
        sql.Identifier(schema),
        sql.Identifier(table),
        sql.SQL(", ").join(map(sql.Identifier, columns)),
        sql.SQL(", ").join(sql.Placeholder(column) for column in columns),
    )
    update_columns = tuple(
        column for column in columns if column not in {"source_system", "source_record_id"}
    )
    statement = (
        insert
        + sql.SQL(" ON CONFLICT (source_system, source_record_id) DO UPDATE SET ")
        + sql.SQL(", ").join(
            sql.SQL("{} = EXCLUDED.{}").format(sql.Identifier(column), sql.Identifier(column))
            for column in update_columns
        )
    )
    with connection.cursor() as cursor:
        cursor.executemany(statement, materialized)
    return len(materialized)


def write_demo_dataset(
    connection: psycopg.Connection,
    dataset: DemoDataset,
    *,
    schema: str = SOURCE_SCHEMA,
) -> dict[str, int]:
    require_valid_source_contract(connection, schema=schema, require_read_only=False)
    if not dataset.validation.get("valid"):
        raise SourceContractError(
            "demo_dataset_invalid",
            "演示数据质量校验失败",
            dataset.validation,
        )
    with connection.transaction():
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL(
                    """
                    INSERT INTO {}.source_batches (
                        batch_id, source_system, dataset_version, reference_date,
                        source_data_as_of, status, seed, record_counts,
                        content_sha256, validation_result, completed_at
                    ) VALUES (
                        %(batch_id)s, 'simulated_generator', %(dataset_version)s,
                        %(reference_date)s, %(source_data_as_of)s, 'ready', %(seed)s,
                        %(record_counts)s::jsonb, %(content_sha256)s,
                        %(validation_result)s::jsonb, now()
                    )
                    ON CONFLICT (batch_id) DO UPDATE SET
                        source_data_as_of = EXCLUDED.source_data_as_of,
                        status = 'ready',
                        record_counts = EXCLUDED.record_counts,
                        content_sha256 = EXCLUDED.content_sha256,
                        validation_result = EXCLUDED.validation_result,
                        completed_at = now()
                    """
                ).format(sql.Identifier(schema)),
                {
                    "batch_id": dataset.batch_id,
                    "dataset_version": dataset.dataset_version,
                    "reference_date": dataset.reference_date,
                    "source_data_as_of": dataset.source_data_as_of,
                    "seed": dataset.seed,
                    "record_counts": json.dumps(dataset.record_counts, ensure_ascii=False),
                    "content_sha256": dataset.content_sha256,
                    "validation_result": json.dumps(dataset.validation, ensure_ascii=False),
                },
            )
        collections = {
            "organizations": dataset.organization_units,
            "people": dataset.people,
            "customers": dataset.customers,
            "opportunities": dataset.opportunities,
            "deliveries": dataset.deliveries,
            "collections": dataset.collections,
            "targets": dataset.targets,
        }
        counts = {
            domain: _upsert_rows(
                connection,
                schema=schema,
                table=SOURCE_TABLES[domain],
                columns=SOURCE_COLUMNS[domain],
                rows=rows,
            )
            for domain, rows in collections.items()
        }
    return counts


def latest_ready_batch(
    connection: psycopg.Connection,
    *,
    schema: str = SOURCE_SCHEMA,
) -> dict[str, Any]:
    with connection.cursor() as cursor:
        row = cursor.execute(
            sql.SQL(
                """
                SELECT batch_id, source_system, dataset_version, reference_date,
                       source_data_as_of, content_sha256, validation_result
                FROM {}.source_batches
                WHERE status = 'ready'
                ORDER BY source_data_as_of DESC, completed_at DESC
                LIMIT 1
                """
            ).format(sql.Identifier(schema))
        ).fetchone()
    if row is None:
        raise SourceContractError("source_batch_missing", "脱敏源库没有可用的成功批次")
    return dict(row)


def iter_source_rows(
    connection: psycopg.Connection,
    domain: str,
    *,
    schema: str = SOURCE_SCHEMA,
    page_size: int = 1000,
) -> Iterator[list[dict[str, Any]]]:
    if domain not in SOURCE_TABLES:
        raise SourceContractError("source_domain_invalid", f"未知数据域: {domain}")
    columns = SOURCE_COLUMNS[domain]
    table = SOURCE_TABLES[domain]
    last_id = 0
    while True:
        statement = sql.SQL("SELECT id, {} FROM {}.{} WHERE id > %s ORDER BY id LIMIT %s").format(
            sql.SQL(", ").join(map(sql.Identifier, columns)),
            sql.Identifier(schema),
            sql.Identifier(table),
        )
        with connection.cursor() as cursor:
            rows = cursor.execute(statement, (last_id, page_size)).fetchall()
        if not rows:
            return
        page = [dict(row) for row in rows]
        last_id = int(page[-1].pop("id"))
        for row in page[:-1]:
            row.pop("id", None)
        yield page


def source_domain_fingerprint(rows: Iterable[dict[str, Any]]) -> str:
    def normalize(value: object) -> object:
        if isinstance(value, (date, datetime, Decimal)):
            return str(value)
        return value

    digest = __import__("hashlib").sha256()
    for row in rows:
        digest.update(
            json.dumps(
                {key: normalize(value) for key, value in row.items()},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        )
        digest.update(b"\n")
    return digest.hexdigest()
