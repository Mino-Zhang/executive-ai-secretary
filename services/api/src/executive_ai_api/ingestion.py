from __future__ import annotations

import hashlib
import os
import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .database import SessionLocal
from .feishu import (
    FeishuBitableClient,
    sync_feishu_opportunities_to_source,
)
from .feishu_live import (
    fetch_fixed_live_snapshot_from_settings,
    fixed_bindings_from_settings,
    write_live_snapshot_to_source,
)
from .models import (
    DailySnapshot,
    DataDomainStatus,
    DataSource,
    DataSyncRun,
    DimCustomer,
    DimPerson,
    FactDelivery,
    FactFinanceCollection,
    FactOpportunity,
    FactTarget,
    Job,
    OrganizationUnit,
    SourceCheckpoint,
)
from .operating_data_v3 import (
    OperatingDataV3Error,
    activate_source_v3_batch,
    materialize_source_v3_batch,
    v3_failure_payload,
)
from .security import utc_now
from .source_contract import (
    SourceContractError,
    connect_source,
    iter_source_rows,
    latest_ready_batch,
    require_valid_source_contract,
    source_domain_fingerprint,
)
from .source_contract_v3 import (
    SOURCE_V3_SCHEMA_VERSION,
    latest_ready_source_v3_batch,
    mark_source_v3_batch_activated,
    record_rejected_source_v3_batch,
    require_valid_source_v3_contract,
)

DOMAIN_TO_SOURCE = {
    "opportunity": "opportunities",
    "delivery": "deliveries",
    "collection": "collections",
    "target": "targets",
}
DOMAIN_MODELS = {
    "opportunity": FactOpportunity,
    "delivery": FactDelivery,
    "collection": FactFinanceCollection,
    "target": FactTarget,
}


class IngestionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


SOURCE_SECRET_REFERENCE_PATTERN = re.compile(r"^SOURCE_DATABASE_URL(?:_[A-Z0-9]+(?:_[A-Z0-9]+)*)?$")
SOURCE_SCHEMA_PATTERN = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


@dataclass(frozen=True)
class ResolvedSourceConnection:
    database_url: str
    schema: str
    schema_version: str
    connection_mode: str
    secret_reference_key: str


def require_isolated_data_source(db: Session, data_source: DataSource) -> None:
    """Fail closed while phase 2 supports one enabled source per deployment.

    The product database is tenant-aware, but source PostgreSQL does not yet carry
    a cryptographic enterprise binding. Until that contract exists, allowing two
    enabled DataSource rows could make an operator mistake look like valid data for
    a different enterprise. A hard deployment guard is safer than relying on a
    shared global URL or on human naming conventions.
    """

    conflict = db.scalar(
        select(DataSource.id).where(
            DataSource.is_enabled.is_(True),
            DataSource.id != data_source.id,
        )
    )
    if conflict is not None:
        raise IngestionError(
            "source_deployment_isolation_violation",
            "当前版本每套部署仅允许一个企业启用一个脱敏数据源",
        )


def _read_source_secret(reference: str, settings: Settings) -> str:
    if not SOURCE_SECRET_REFERENCE_PATTERN.fullmatch(reference):
        raise IngestionError(
            "source_secret_reference_invalid",
            "数据源密钥引用必须是 SOURCE_DATABASE_URL 或其独立后缀变量",
        )
    if reference == "SOURCE_DATABASE_URL":
        if settings.source_database_url is None:
            raise IngestionError("source_database_not_configured", "未配置脱敏源库连接")
        return settings.source_database_url.get_secret_value()

    direct_value = os.environ.get(reference)
    file_value = os.environ.get(f"{reference}_FILE")
    if direct_value and file_value:
        raise IngestionError(
            "source_secret_reference_ambiguous",
            "数据源连接密钥不能同时使用直接值和文件引用",
        )
    if direct_value:
        return direct_value
    if file_value:
        try:
            value = Path(file_value).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise IngestionError(
                "source_secret_file_unreadable",
                "数据源连接密钥文件无法读取",
            ) from exc
        if value:
            return value
    raise IngestionError(
        "source_secret_reference_missing",
        "数据源连接密钥引用未配置",
    )


def _database_target_identity(database_url: str) -> tuple[str, str, int, str]:
    parsed = urlsplit(database_url)
    scheme = parsed.scheme.removesuffix("+psycopg")
    database = parsed.path.lstrip("/")
    if scheme != "postgresql" or not parsed.hostname or not database:
        raise IngestionError(
            "source_database_url_invalid",
            "脱敏源库连接必须是包含主机和数据库名的 PostgreSQL URL",
        )
    return scheme, parsed.hostname.lower(), parsed.port or 5432, database


def resolve_source_connection(
    db: Session,
    data_source: DataSource,
    settings: Settings | None = None,
) -> ResolvedSourceConnection:
    settings = settings or get_settings()
    require_isolated_data_source(db, data_source)
    reference = (data_source.secret_reference_key or "").strip()
    if not reference:
        raise IngestionError(
            "source_secret_reference_missing",
            "数据源未绑定独立的连接密钥引用",
        )
    database_url = _read_source_secret(reference, settings)
    _database_target_identity(database_url)

    configuration = data_source.configuration_json or {}
    schema = str(configuration.get("schema") or settings.source_schema)
    if not SOURCE_SCHEMA_PATTERN.fullmatch(schema):
        raise IngestionError("source_schema_invalid", "数据源 Schema 名称无效")
    connection_mode = str(configuration.get("connection_mode") or settings.source_connection_mode)
    if connection_mode not in {"internal", "external"}:
        raise IngestionError("source_connection_mode_invalid", "数据源连接模式无效")
    if settings.source_connection_mode == "external" and connection_mode != "external":
        raise IngestionError(
            "source_connection_mode_downgrade_forbidden",
            "DataSource 不得降低部署级外部源库 TLS 要求",
        )
    if connection_mode == "external":
        ssl_modes = parse_qs(urlsplit(database_url).query).get("sslmode", [])
        if ssl_modes != ["verify-full"]:
            raise IngestionError(
                "source_tls_required",
                "客户外部脱敏源库必须使用 sslmode=verify-full",
            )
    return ResolvedSourceConnection(
        database_url=database_url,
        schema=schema,
        schema_version=data_source.schema_version,
        connection_mode=connection_mode,
        secret_reference_key=reference,
    )


def _materialize_source(
    connection,
    source_domain: str,
    *,
    settings: Settings,
    schema: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for page in iter_source_rows(
        connection,
        source_domain,
        schema=schema,
        page_size=settings.source_query_page_size,
    ):
        rows.extend(page)
    return rows


def test_source_connection(
    data_source: DataSource,
    *,
    db: Session,
    allow_empty: bool = False,
    settings: Settings | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    resolved = resolve_source_connection(db, data_source, settings)
    started_at = utc_now()
    with connect_source(
        resolved.database_url,
        application_name="executive-ai-source-test",
    ) as connection:
        batch: dict[str, Any] | None = None
        if resolved.schema_version == SOURCE_V3_SCHEMA_VERSION:
            inspection = require_valid_source_v3_contract(
                connection,
                expected_version=resolved.schema_version,
                schema=resolved.schema,
            )
            try:
                batch = latest_ready_source_v3_batch(connection, schema=resolved.schema)
            except SourceContractError as exc:
                if not allow_empty or exc.code != "source_batch_missing":
                    raise
        else:
            inspection = require_valid_source_contract(
                connection,
                expected_version=resolved.schema_version,
                schema=resolved.schema,
            )
            batch = latest_ready_batch(connection, schema=resolved.schema)
        if resolved.connection_mode == "external" and not inspection.tls_active:
            raise SourceContractError(
                "source_tls_required",
                "客户外部脱敏源库必须使用 TLS 连接",
            )
    return {
        "ok": True,
        "schema_version": inspection.schema_version,
        "database_version": inspection.database_version,
        "current_user": inspection.current_user,
        "read_only": inspection.transaction_read_only,
        "tls_active": inspection.tls_active,
        "latest_batch_id": batch["batch_id"] if batch else "",
        "source_data_as_of": batch["source_data_as_of"] if batch else None,
        "duration_ms": int((utc_now() - started_at).total_seconds() * 1000),
    }


def _upsert_organizations(
    db: Session,
    *,
    enterprise_id: uuid.UUID,
    rows: Iterable[dict[str, Any]],
) -> dict[str, OrganizationUnit]:
    existing = {
        item.code: item
        for item in db.scalars(
            select(OrganizationUnit).where(OrganizationUnit.enterprise_id == enterprise_id)
        ).all()
    }
    active_codes: set[str] = set()
    ordered_rows = sorted(
        rows, key=lambda row: (row.get("parent_organization_code") is not None, row["sort_order"])
    )
    for row in ordered_rows:
        code = str(row["organization_code"])
        active_codes.add(code)
        item = existing.get(code)
        if item is None:
            item = OrganizationUnit(
                enterprise_id=enterprise_id,
                name=str(row["display_name"]),
                code=code,
                unit_type=str(row["unit_type"]),
                enabled_for_analysis=True,
                data_connected=True,
                sort_order=int(row["sort_order"]),
                config_json={"source_record_id": row["source_record_id"]},
            )
            db.add(item)
            db.flush()
            existing[code] = item
        else:
            item.name = str(row["display_name"])
            item.unit_type = str(row["unit_type"])
            item.data_connected = not bool(row["is_deleted"])
            item.enabled_for_analysis = not bool(row["is_deleted"])
            item.is_active = not bool(row["is_deleted"])
            item.sort_order = int(row["sort_order"])
            item.config_json = {
                **item.config_json,
                "source_record_id": row["source_record_id"],
            }
    db.flush()
    for row in ordered_rows:
        parent_code = row.get("parent_organization_code")
        if parent_code and str(parent_code) in existing:
            existing[str(row["organization_code"])].parent_id = existing[str(parent_code)].id
    # The sanitized source contract is authoritative for the analyzable
    # organization scope. Keep missing legacy rows for audit/history, but do not
    # let seed data or retired units silently expand a user's query boundary.
    for code, item in existing.items():
        if code not in active_codes:
            item.is_active = False
            item.enabled_for_analysis = False
            item.data_connected = False
    return {
        code: item for code, item in existing.items() if code in active_codes and item.is_active
    }


def _upsert_people(
    db: Session,
    *,
    enterprise_id: uuid.UUID,
    data_source_id: uuid.UUID,
    organization_map: dict[str, OrganizationUnit],
    rows: Iterable[dict[str, Any]],
    dataset_version: str,
    synced_at: datetime,
) -> dict[str, DimPerson]:
    existing = {
        item.source_record_id: item
        for item in db.scalars(
            select(DimPerson).where(
                DimPerson.enterprise_id == enterprise_id,
                DimPerson.data_source_id == data_source_id,
            )
        ).all()
    }
    for row in rows:
        source_record_id = str(row["source_record_id"])
        item = existing.get(source_record_id)
        if item is None:
            item = DimPerson(
                enterprise_id=enterprise_id,
                data_source_id=data_source_id,
                source_record_id=source_record_id,
                display_name=str(row["display_name"]),
                source_system=str(row["source_system"]),
                source_updated_at=row["source_updated_at"],
                synced_at=synced_at,
            )
            db.add(item)
            existing[source_record_id] = item
        item.organization_unit_id = organization_map[str(row["organization_code"])].id
        item.display_name = str(row["display_name"])
        item.role_title = str(row["role_title"]) if row.get("role_title") else None
        item.is_active = bool(row["is_active"]) and not bool(row["is_deleted"])
        item.source_system = str(row["source_system"])
        item.dataset_version = dataset_version
        item.source_updated_at = row["source_updated_at"]
        item.synced_at = synced_at
    db.flush()
    return existing


def _upsert_customers(
    db: Session,
    *,
    enterprise_id: uuid.UUID,
    data_source_id: uuid.UUID,
    organization_map: dict[str, OrganizationUnit],
    people_map: dict[str, DimPerson],
    rows: Iterable[dict[str, Any]],
    dataset_version: str,
    synced_at: datetime,
) -> dict[str, DimCustomer]:
    existing = {
        item.source_record_id: item
        for item in db.scalars(
            select(DimCustomer).where(
                DimCustomer.enterprise_id == enterprise_id,
                DimCustomer.data_source_id == data_source_id,
            )
        ).all()
    }
    for row in rows:
        source_record_id = str(row["source_record_id"])
        item = existing.get(source_record_id)
        if item is None:
            item = DimCustomer(
                enterprise_id=enterprise_id,
                data_source_id=data_source_id,
                source_record_id=source_record_id,
                display_name=str(row["display_name"]),
                source_system=str(row["source_system"]),
                source_updated_at=row["source_updated_at"],
                synced_at=synced_at,
            )
            db.add(item)
            existing[source_record_id] = item
        organization = organization_map.get(str(row["organization_code"]))
        if organization is None:
            raise IngestionError(
                "source_reference_invalid",
                f"客户 {source_record_id} 关联了未知事业部",
            )
        item.organization_unit_id = organization.id
        owner = people_map.get(str(row["owner_person_record_id"]))
        item.owner_person_id = owner.id if owner else None
        item.display_name = str(row["display_name"])
        item.industry = str(row["industry"]) if row.get("industry") else None
        item.region = str(row["region"]) if row.get("region") else None
        item.source_system = str(row["source_system"])
        item.dataset_version = dataset_version
        item.source_updated_at = row["source_updated_at"]
        item.synced_at = synced_at
    db.flush()
    return existing


def _fact_rows(
    domain: str,
    rows: Iterable[dict[str, Any]],
    *,
    enterprise_id: uuid.UUID,
    data_source_id: uuid.UUID,
    sync_run_id: uuid.UUID,
    organization_map: dict[str, OrganizationUnit],
    people_map: dict[str, DimPerson],
    customer_map: dict[str, DimCustomer],
    dataset_version: str,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in rows:
        if row["is_deleted"]:
            continue
        organization = organization_map.get(str(row["organization_code"]))
        if organization is None:
            raise IngestionError(
                "source_reference_invalid",
                f"{domain} {row['source_record_id']} 关联了未知事业部",
            )
        common = {
            "enterprise_id": enterprise_id,
            "data_source_id": data_source_id,
            "sync_run_id": sync_run_id,
            "organization_unit_id": organization.id,
            "source_record_id": str(row["source_record_id"]),
            "source_system": str(row["source_system"]),
            "source_updated_at": row["source_updated_at"],
            "dataset_version": dataset_version,
            "is_current": False,
        }
        if domain == "opportunity":
            customer = customer_map.get(str(row["customer_record_id"]))
            owner = people_map.get(str(row["owner_person_record_id"]))
            if customer is None:
                raise IngestionError("source_reference_invalid", "商机关联了未知客户")
            output.append(
                {
                    **common,
                    "customer_id": customer.id,
                    "owner_person_id": owner.id if owner else None,
                    "opportunity_code": str(row["opportunity_code"]),
                    "title": str(row["title"]),
                    "stage": str(row["stage"]),
                    "status": str(row["status"]),
                    "probability": int(row["probability"]),
                    "expected_amount": Decimal(row["expected_amount"]),
                    "expected_gross_profit": Decimal(row["expected_gross_profit"]),
                    "created_date": row["created_date"],
                    "expected_close_date": row["expected_close_date"],
                    "closed_date": row["closed_date"],
                }
            )
        elif domain == "delivery":
            customer = customer_map.get(str(row["customer_record_id"]))
            manager = people_map.get(str(row["manager_person_record_id"]))
            if customer is None:
                raise IngestionError("source_reference_invalid", "交付项目关联了未知客户")
            output.append(
                {
                    **common,
                    "customer_id": customer.id,
                    "manager_person_id": manager.id if manager else None,
                    "opportunity_source_record_id": str(row["opportunity_record_id"]),
                    "project_code": str(row["project_code"]),
                    "project_name": str(row["project_name"]),
                    "status": str(row["status"]),
                    "risk_level": str(row["risk_level"]),
                    "completion_percent": int(row["completion_percent"]),
                    "contract_amount": Decimal(row["contract_amount"]),
                    "gross_margin_rate": Decimal(row["gross_margin_rate"]),
                    "planned_start_date": row["planned_start_date"],
                    "planned_end_date": row["planned_end_date"],
                    "actual_end_date": row["actual_end_date"],
                    "current_milestone": row["current_milestone"],
                    "delay_days": int(row["delay_days"]),
                }
            )
        elif domain == "collection":
            customer = customer_map.get(str(row["customer_record_id"]))
            if customer is None:
                raise IngestionError("source_reference_invalid", "回款记录关联了未知客户")
            receivable = Decimal(row["receivable_amount"])
            collected = Decimal(row["collected_amount"])
            if collected > receivable:
                raise IngestionError("financial_invariant_failed", "已回款金额不能大于应收金额")
            output.append(
                {
                    **common,
                    "customer_id": customer.id,
                    "project_source_record_id": str(row["project_record_id"]),
                    "invoice_amount": Decimal(row["invoice_amount"]),
                    "receivable_amount": receivable,
                    "collected_amount": collected,
                    "outstanding_amount": receivable - collected,
                    "planned_collection_date": row["planned_collection_date"],
                    "actual_collection_date": row["actual_collection_date"],
                    "overdue_days": int(row["overdue_days"]),
                    "aging_bucket": str(row["aging_bucket"]),
                    "status": str(row["status"]),
                }
            )
        elif domain == "target":
            output.append(
                {
                    **common,
                    "metric_code": str(row["metric_code"]),
                    "metric_name": str(row["metric_name"]),
                    "period_type": str(row["period_type"]),
                    "period_start": row["period_start"],
                    "period_end": row["period_end"],
                    "target_value": Decimal(row["target_value"]),
                    "unit": str(row["unit"]),
                }
            )
        else:
            raise IngestionError("source_domain_invalid", f"未知标准化数据域: {domain}")
    return output


def _domain_source_type(data_source: DataSource, rows: list[dict[str, Any]]) -> str:
    if data_source.source_type == "customer_sanitized_database":
        return data_source.source_type
    systems = {str(row["source_system"]) for row in rows if not row["is_deleted"]}
    return next(iter(systems)) if len(systems) == 1 else data_source.source_type


def _set_domain_failure(
    *,
    enterprise_id: uuid.UUID,
    data_source_id: uuid.UUID,
    domain: str,
    source_type: str,
    source_display_name: str,
    code: str,
    message: str,
) -> None:
    with SessionLocal.begin() as db:
        status = db.scalar(
            select(DataDomainStatus).where(
                DataDomainStatus.enterprise_id == enterprise_id,
                DataDomainStatus.domain == domain,
            )
        )
        if status is None:
            status = DataDomainStatus(
                enterprise_id=enterprise_id,
                data_source_id=data_source_id,
                domain=domain,
                status="failed",
                source_type=source_type,
                source_display_name=source_display_name,
            )
            db.add(status)
        status.status = "stale" if status.active_sync_run_id else "failed"
        status.last_error_code = code
        status.last_error_message = message[:2000]


def _activate_domain(
    *,
    enterprise_id: uuid.UUID,
    data_source: DataSource,
    sync_run_id: uuid.UUID,
    domain: str,
    rows: list[dict[str, Any]],
    organization_map: dict[str, OrganizationUnit],
    people_map: dict[str, DimPerson],
    customer_map: dict[str, DimCustomer],
    dataset_version: str,
    source_data_as_of: datetime,
    fingerprint: str,
) -> dict[str, Any]:
    model = DOMAIN_MODELS[domain]
    source_type = _domain_source_type(data_source, rows)
    with SessionLocal.begin() as db:
        checkpoint = db.scalar(
            select(SourceCheckpoint).where(
                SourceCheckpoint.data_source_id == data_source.id,
                SourceCheckpoint.domain == domain,
            )
        )
        current_status = db.scalar(
            select(DataDomainStatus).where(
                DataDomainStatus.enterprise_id == enterprise_id,
                DataDomainStatus.domain == domain,
            )
        )
        if (
            checkpoint
            and checkpoint.checksum == fingerprint
            and current_status
            and current_status.active_sync_run_id
        ):
            current_status.status = "fresh"
            current_status.last_error_code = None
            current_status.last_error_message = None
            return {
                "domain": domain,
                "status": "unchanged",
                "records": current_status.record_count,
                "source_data_as_of": (
                    current_status.source_data_as_of.isoformat()
                    if current_status.source_data_as_of
                    else None
                ),
            }
        fact_rows = _fact_rows(
            domain,
            rows,
            enterprise_id=enterprise_id,
            data_source_id=data_source.id,
            sync_run_id=sync_run_id,
            organization_map=organization_map,
            people_map=people_map,
            customer_map=customer_map,
            dataset_version=dataset_version,
        )
        if fact_rows:
            db.execute(insert(model), fact_rows)
        db.execute(
            update(model)
            .where(
                model.enterprise_id == enterprise_id,
                model.is_current.is_(True),
            )
            .values(is_current=False)
        )
        db.execute(update(model).where(model.sync_run_id == sync_run_id).values(is_current=True))
        if current_status is None:
            current_status = DataDomainStatus(
                enterprise_id=enterprise_id,
                data_source_id=data_source.id,
                domain=domain,
                source_type=source_type,
                source_display_name=data_source.display_name,
            )
            db.add(current_status)
        current_status.previous_sync_run_id = current_status.active_sync_run_id
        current_status.active_sync_run_id = sync_run_id
        current_status.status = "fresh"
        current_status.source_data_as_of = source_data_as_of
        current_status.last_success_at = utc_now()
        current_status.record_count = len(fact_rows)
        current_status.dataset_version = dataset_version
        current_status.source_type = source_type
        current_status.source_display_name = data_source.display_name
        current_status.last_error_code = None
        current_status.last_error_message = None
        if checkpoint is None:
            checkpoint = SourceCheckpoint(data_source_id=data_source.id, domain=domain)
            db.add(checkpoint)
        checkpoint.source_updated_at = source_data_as_of
        checkpoint.source_batch_id = dataset_version
        checkpoint.checksum = fingerprint
        return {
            "domain": domain,
            "status": "activated",
            "records": len(fact_rows),
            "source_data_as_of": source_data_as_of.isoformat(),
        }


def _activate_live_domains_atomically(
    *,
    enterprise_id: uuid.UUID,
    data_source: DataSource,
    sync_run_id: uuid.UUID,
    rows_by_domain: dict[str, list[dict[str, Any]]],
    organization_map: dict[str, OrganizationUnit],
    people_map: dict[str, DimPerson],
    customer_map: dict[str, DimCustomer],
    dataset_version: str,
    fallback_source_data_as_of: datetime,
) -> dict[str, dict[str, Any]]:
    """Switch the three live Feishu facts in one product-database transaction."""

    domains = ("opportunity", "delivery", "collection")
    fingerprints = {domain: source_domain_fingerprint(rows_by_domain[domain]) for domain in domains}
    source_times = {
        domain: max(
            (row["source_updated_at"] for row in rows_by_domain[domain]),
            default=fallback_source_data_as_of,
        )
        for domain in domains
    }
    with SessionLocal.begin() as db:
        checkpoints = {
            item.domain: item
            for item in db.scalars(
                select(SourceCheckpoint).where(
                    SourceCheckpoint.data_source_id == data_source.id,
                    SourceCheckpoint.domain.in_(domains),
                )
            ).all()
        }
        statuses = {
            item.domain: item
            for item in db.scalars(
                select(DataDomainStatus).where(
                    DataDomainStatus.enterprise_id == enterprise_id,
                    DataDomainStatus.domain.in_(domains),
                )
            ).all()
        }
        if all(
            checkpoints.get(domain)
            and checkpoints[domain].checksum == fingerprints[domain]
            and statuses.get(domain)
            and statuses[domain].active_sync_run_id
            for domain in domains
        ):
            output: dict[str, dict[str, Any]] = {}
            for domain in domains:
                status = statuses[domain]
                status.status = "fresh"
                status.last_error_code = None
                status.last_error_message = None
                output[domain] = {
                    "domain": domain,
                    "status": "unchanged",
                    "records": status.record_count,
                    "source_data_as_of": (
                        status.source_data_as_of.isoformat() if status.source_data_as_of else None
                    ),
                }
            return output

        materialized: dict[str, list[dict[str, Any]]] = {}
        for domain in domains:
            materialized[domain] = _fact_rows(
                domain,
                rows_by_domain[domain],
                enterprise_id=enterprise_id,
                data_source_id=data_source.id,
                sync_run_id=sync_run_id,
                organization_map=organization_map,
                people_map=people_map,
                customer_map=customer_map,
                dataset_version=dataset_version,
            )

        output = {}
        for domain in domains:
            model = DOMAIN_MODELS[domain]
            fact_rows = materialized[domain]
            if fact_rows:
                db.execute(insert(model), fact_rows)
            db.execute(
                update(model)
                .where(model.enterprise_id == enterprise_id, model.is_current.is_(True))
                .values(is_current=False)
            )
            db.execute(
                update(model).where(model.sync_run_id == sync_run_id).values(is_current=True)
            )

            status = statuses.get(domain)
            if status is None:
                status = DataDomainStatus(
                    enterprise_id=enterprise_id,
                    data_source_id=data_source.id,
                    domain=domain,
                    source_type=data_source.source_type,
                    source_display_name=data_source.display_name,
                )
                db.add(status)
                statuses[domain] = status
            status.previous_sync_run_id = status.active_sync_run_id
            status.active_sync_run_id = sync_run_id
            status.status = "fresh"
            status.source_data_as_of = source_times[domain]
            status.last_success_at = utc_now()
            status.record_count = len(fact_rows)
            status.dataset_version = dataset_version
            status.source_type = data_source.source_type
            status.source_display_name = data_source.display_name
            status.last_error_code = None
            status.last_error_message = None

            checkpoint = checkpoints.get(domain)
            if checkpoint is None:
                checkpoint = SourceCheckpoint(data_source_id=data_source.id, domain=domain)
                db.add(checkpoint)
                checkpoints[domain] = checkpoint
            checkpoint.source_updated_at = source_times[domain]
            checkpoint.source_batch_id = dataset_version
            checkpoint.checksum = fingerprints[domain]
            output[domain] = {
                "domain": domain,
                "status": "activated",
                "records": len(fact_rows),
                "source_data_as_of": source_times[domain].isoformat(),
            }

        # The approved live source contains no target table. Retire the old
        # generated target facts so they cannot be mixed into live answers.
        db.execute(
            update(FactTarget)
            .where(FactTarget.enterprise_id == enterprise_id, FactTarget.is_current.is_(True))
            .values(is_current=False)
        )
        target_status = db.scalar(
            select(DataDomainStatus).where(
                DataDomainStatus.enterprise_id == enterprise_id,
                DataDomainStatus.domain == "target",
            )
        )
        if target_status is not None:
            target_status.previous_sync_run_id = target_status.active_sync_run_id
            target_status.active_sync_run_id = None
            target_status.status = "not_configured"
            target_status.record_count = 0
            target_status.dataset_version = dataset_version
            target_status.source_type = data_source.source_type
            target_status.source_display_name = data_source.display_name
            target_status.last_error_code = None
            target_status.last_error_message = None
        return output


def _decimal(value: Decimal | None) -> float:
    return float(value or Decimal("0"))


def rebuild_daily_snapshots(
    *,
    enterprise_id: uuid.UUID,
    reference_date: date,
    dataset_version: str,
    source_data_as_of: datetime,
) -> int:
    with SessionLocal.begin() as db:
        domain_statuses = db.scalars(
            select(DataDomainStatus).where(
                DataDomainStatus.enterprise_id == enterprise_id,
                DataDomainStatus.domain.in_(tuple(DOMAIN_TO_SOURCE)),
            )
        ).all()
        freshness_by_domain = {
            status.domain: {
                "status": status.status,
                "source_data_as_of": (
                    status.source_data_as_of.isoformat() if status.source_data_as_of else None
                ),
                "dataset_version": status.dataset_version,
                "source_type": status.source_type,
                "source_display_name": status.source_display_name,
            }
            for status in domain_statuses
        }
        active_times = [
            status.source_data_as_of
            for status in domain_statuses
            if status.source_data_as_of is not None
        ]
        snapshot_source_data_as_of = min(active_times, default=source_data_as_of)
        active_versions = {
            status.dataset_version for status in domain_statuses if status.dataset_version
        }
        snapshot_dataset_version = (
            next(iter(active_versions))
            if len(active_versions) == 1
            else "mixed-domain-versions"
            if active_versions
            else dataset_version
        )
        units = db.scalars(
            select(OrganizationUnit).where(
                OrganizationUnit.enterprise_id == enterprise_id,
                OrganizationUnit.is_active.is_(True),
                OrganizationUnit.data_connected.is_(True),
            )
        ).all()
        db.execute(
            delete(DailySnapshot).where(
                DailySnapshot.enterprise_id == enterprise_id,
                DailySnapshot.snapshot_date == reference_date,
            )
        )
        scopes: list[uuid.UUID | None] = [None, *[unit.id for unit in units]]
        for organization_unit_id in scopes:
            filters = [
                FactOpportunity.enterprise_id == enterprise_id,
                FactOpportunity.is_current.is_(True),
            ]
            delivery_filters = [
                FactDelivery.enterprise_id == enterprise_id,
                FactDelivery.is_current.is_(True),
            ]
            collection_filters = [
                FactFinanceCollection.enterprise_id == enterprise_id,
                FactFinanceCollection.is_current.is_(True),
            ]
            target_filters = [
                FactTarget.enterprise_id == enterprise_id,
                FactTarget.is_current.is_(True),
            ]
            if organization_unit_id is not None:
                filters.append(FactOpportunity.organization_unit_id == organization_unit_id)
                delivery_filters.append(FactDelivery.organization_unit_id == organization_unit_id)
                collection_filters.append(
                    FactFinanceCollection.organization_unit_id == organization_unit_id
                )
                target_filters.append(FactTarget.organization_unit_id == organization_unit_id)
            opportunity_metrics = db.execute(
                select(
                    func.count(FactOpportunity.id),
                    func.coalesce(func.sum(FactOpportunity.expected_amount), 0),
                    func.coalesce(
                        func.sum(
                            FactOpportunity.expected_amount * FactOpportunity.probability / 100
                        ),
                        0,
                    ),
                ).where(*filters)
            ).one()
            delivery_metrics = db.execute(
                select(
                    func.count(FactDelivery.id),
                    func.count(FactDelivery.id).filter(FactDelivery.risk_level != "normal"),
                    func.count(FactDelivery.id).filter(FactDelivery.status == "delayed"),
                ).where(*delivery_filters)
            ).one()
            collection_metrics = db.execute(
                select(
                    func.coalesce(func.sum(FactFinanceCollection.receivable_amount), 0),
                    func.coalesce(func.sum(FactFinanceCollection.collected_amount), 0),
                    func.coalesce(func.sum(FactFinanceCollection.outstanding_amount), 0),
                    func.coalesce(
                        func.sum(FactFinanceCollection.outstanding_amount).filter(
                            FactFinanceCollection.overdue_days > 0
                        ),
                        0,
                    ),
                ).where(*collection_filters)
            ).one()
            current_month_start = date(reference_date.year, reference_date.month, 1)
            target_metrics = {
                code: _decimal(value)
                for code, value in db.execute(
                    select(FactTarget.metric_code, func.sum(FactTarget.target_value))
                    .where(
                        *target_filters,
                        FactTarget.period_type == "month",
                        FactTarget.period_start == current_month_start,
                    )
                    .group_by(FactTarget.metric_code)
                ).all()
            }
            anomalies: list[dict[str, Any]] = []
            if int(delivery_metrics[2] or 0):
                anomalies.append(
                    {
                        "domain": "delivery",
                        "severity": "attention",
                        "title": f"{int(delivery_metrics[2])} 个项目处于延期状态",
                    }
                )
            if _decimal(collection_metrics[3]) > 0:
                anomalies.append(
                    {
                        "domain": "collection",
                        "severity": "attention",
                        "title": "存在逾期未回款",
                        "value": _decimal(collection_metrics[3]),
                        "unit": "元",
                    }
                )
            db.add(
                DailySnapshot(
                    enterprise_id=enterprise_id,
                    organization_unit_id=organization_unit_id,
                    snapshot_date=reference_date,
                    source_data_as_of=snapshot_source_data_as_of,
                    dataset_version=snapshot_dataset_version,
                    metrics_json={
                        "_domain_freshness": freshness_by_domain,
                        "opportunity_count": int(opportunity_metrics[0] or 0),
                        "pipeline_amount": _decimal(opportunity_metrics[1]),
                        "weighted_pipeline_amount": _decimal(opportunity_metrics[2]),
                        "delivery_count": int(delivery_metrics[0] or 0),
                        "delivery_attention_count": int(delivery_metrics[1] or 0),
                        "delivery_delayed_count": int(delivery_metrics[2] or 0),
                        "receivable_amount": _decimal(collection_metrics[0]),
                        "collected_amount": _decimal(collection_metrics[1]),
                        "outstanding_amount": _decimal(collection_metrics[2]),
                        "overdue_amount": _decimal(collection_metrics[3]),
                        "targets": target_metrics,
                    },
                    anomalies_json=anomalies,
                )
            )
        return len(scopes)


def _run_data_sync_v3(
    *,
    enterprise_id: uuid.UUID,
    data_source_id: uuid.UUID,
    sync_run_id: uuid.UUID,
    source_snapshot: dict[str, Any],
    resolved_source: ResolvedSourceConnection,
    validate_only: bool,
    settings: Settings,
) -> dict[str, Any]:
    source_batch: dict[str, Any] | None = None
    try:
        with SessionLocal.begin() as db:
            # Use a transaction-scoped advisory lock instead of SELECT FOR
            # UPDATE on the DataSource row.  The ingestion role deliberately
            # has no permission to mutate source configuration, while the
            # advisory lock still serializes the complete source-read ->
            # product-activate step for this source.
            if db.bind is not None and db.bind.dialect.name == "postgresql":
                db.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
                    {"scope": f"operating-data-v3-activation:{data_source_id}"},
                )
            data_source = db.scalar(
                select(DataSource)
                .where(
                    DataSource.id == data_source_id,
                    DataSource.enterprise_id == enterprise_id,
                    DataSource.is_enabled.is_(True),
                )
            )
            if data_source is None:
                raise IngestionError("data_source_not_found", "同步期间数据源被移除")
            with connect_source(
                resolved_source.database_url,
                application_name="executive-ai-ingestion-v3",
            ) as source_connection:
                inspection = require_valid_source_v3_contract(
                    source_connection,
                    expected_version=resolved_source.schema_version,
                    schema=resolved_source.schema,
                )
                if resolved_source.connection_mode == "external" and not inspection.tls_active:
                    raise SourceContractError(
                        "source_tls_required",
                        "客户外部脱敏源库必须使用 TLS 连接",
                    )
                source_batch = latest_ready_source_v3_batch(
                    source_connection, schema=resolved_source.schema
                )
                rows_by_domain = materialize_source_v3_batch(
                    source_connection,
                    batch_id=str(source_batch["batch_id"]),
                    schema=resolved_source.schema,
                    page_size=settings.source_query_page_size,
                )
            result = activate_source_v3_batch(
                db,
                enterprise_id=enterprise_id,
                data_source=data_source,
                sync_run_id=sync_run_id,
                source_batch=source_batch,
                rows_by_domain=rows_by_domain,
                validate_only=validate_only,
            )
        result.update(
            {
                "sync_run_id": str(sync_run_id),
                "schema_version": SOURCE_V3_SCHEMA_VERSION,
                "source_data_as_of": source_batch["source_data_as_of"].isoformat(),
            }
        )
        return result
    except Exception as exc:
        code, message, details = v3_failure_payload(exc)
        if not validate_only:
            for domain in ("opportunity", "delivery", "collection"):
                _set_domain_failure(
                    enterprise_id=enterprise_id,
                    data_source_id=data_source_id,
                    domain=domain,
                    source_type=str(source_snapshot["source_type"]),
                    source_display_name=str(source_snapshot["display_name"]),
                    code=code,
                    message=message,
                )
        with SessionLocal.begin() as db:
            sync_run = db.get(DataSyncRun, sync_run_id)
            if sync_run is not None:
                sync_run.status = "rejected" if validate_only else "failed"
                sync_run.atomic_activation_status = "not_requested" if validate_only else "failed"
                sync_run.completed_at = utc_now()
                sync_run.error_code = code
                sync_run.error_message = message[:2000]
                sync_run.cross_table_validation_json = {
                    "valid": False,
                    "error_code": code,
                    "details": details,
                }
                if source_batch:
                    sync_run.dataset_version = str(source_batch.get("dataset_version") or "")
                    sync_run.source_schema_version = SOURCE_V3_SCHEMA_VERSION
                    sync_run.source_batch_id = str(source_batch.get("batch_id") or "")
                    sync_run.source_data_as_of = source_batch.get("source_data_as_of")
                    sync_run.source_schema_hashes_json = dict(
                        source_batch.get("table_schema_sha256") or {}
                    )
                    sync_run.source_record_counts_json = dict(
                        source_batch.get("record_counts") or {}
                    )
                    sync_run.source_content_hashes_json = dict(
                        source_batch.get("table_content_sha256") or {}
                    )
        if isinstance(exc, (IngestionError, SourceContractError, OperatingDataV3Error)):
            raise
        raise IngestionError(code, message) from exc


def run_data_sync(
    *,
    enterprise_id: uuid.UUID,
    data_source_id: uuid.UUID,
    job_id: uuid.UUID | None,
    trigger_type: str,
    validate_only: bool = False,
    settings: Settings | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    with SessionLocal.begin() as db:
        data_source = db.scalar(
            select(DataSource).where(
                DataSource.id == data_source_id,
                DataSource.enterprise_id == enterprise_id,
                DataSource.is_enabled.is_(True),
            )
        )
        if data_source is None:
            raise IngestionError("data_source_not_found", "数据源不存在或未启用")
        resolved_source = resolve_source_connection(db, data_source, settings)
        sync_run = DataSyncRun(
            enterprise_id=enterprise_id,
            data_source_id=data_source.id,
            job_id=job_id,
            trigger_type=trigger_type,
            status="running",
            started_at=utc_now(),
        )
        db.add(sync_run)
        db.flush()
        sync_run_id = sync_run.id
        source_snapshot = {
            "id": data_source.id,
            "enterprise_id": data_source.enterprise_id,
            "source_type": data_source.source_type,
            "display_name": data_source.display_name,
        }
    if resolved_source.schema_version == SOURCE_V3_SCHEMA_VERSION:
        return _run_data_sync_v3(
            enterprise_id=enterprise_id,
            data_source_id=data_source_id,
            sync_run_id=sync_run_id,
            source_snapshot=source_snapshot,
            resolved_source=resolved_source,
            validate_only=validate_only,
            settings=settings,
        )
    domain_results: dict[str, Any] = {}
    total_read = 0
    total_written = 0
    source_batch: dict[str, Any] | None = None
    try:
        with connect_source(
            resolved_source.database_url,
            application_name="executive-ai-ingestion",
        ) as source_connection:
            inspection = require_valid_source_contract(
                source_connection,
                expected_version=resolved_source.schema_version,
                schema=resolved_source.schema,
            )
            if resolved_source.connection_mode == "external" and not inspection.tls_active:
                raise SourceContractError(
                    "source_tls_required",
                    "客户外部脱敏源库必须使用 TLS 连接",
                )
            source_batch = latest_ready_batch(source_connection, schema=resolved_source.schema)
            dataset_version = str(source_batch["dataset_version"])
            source_data_as_of = source_batch["source_data_as_of"]
            reference_date = source_batch["reference_date"]
            organizations = _materialize_source(
                source_connection,
                "organizations",
                settings=settings,
                schema=resolved_source.schema,
            )
            people = _materialize_source(
                source_connection,
                "people",
                settings=settings,
                schema=resolved_source.schema,
            )
            customers = _materialize_source(
                source_connection,
                "customers",
                settings=settings,
                schema=resolved_source.schema,
            )
            total_read += len(organizations) + len(people) + len(customers)
            with SessionLocal.begin() as db:
                organization_map = _upsert_organizations(
                    db,
                    enterprise_id=enterprise_id,
                    rows=organizations,
                )
                people_map = _upsert_people(
                    db,
                    enterprise_id=enterprise_id,
                    data_source_id=data_source_id,
                    organization_map=organization_map,
                    rows=people,
                    dataset_version=dataset_version,
                    synced_at=utc_now(),
                )
                customer_map = _upsert_customers(
                    db,
                    enterprise_id=enterprise_id,
                    data_source_id=data_source_id,
                    organization_map=organization_map,
                    people_map=people_map,
                    rows=customers,
                    dataset_version=dataset_version,
                    synced_at=utc_now(),
                )
            with SessionLocal() as db:
                organization_map = {
                    item.code: item
                    for item in db.scalars(
                        select(OrganizationUnit).where(
                            OrganizationUnit.enterprise_id == enterprise_id,
                            OrganizationUnit.is_active.is_(True),
                        )
                    ).all()
                }
                people_map = {
                    item.source_record_id: item
                    for item in db.scalars(
                        select(DimPerson).where(
                            DimPerson.enterprise_id == enterprise_id,
                            DimPerson.data_source_id == data_source_id,
                        )
                    ).all()
                }
                customer_map = {
                    item.source_record_id: item
                    for item in db.scalars(
                        select(DimCustomer).where(
                            DimCustomer.enterprise_id == enterprise_id,
                            DimCustomer.data_source_id == data_source_id,
                        )
                    ).all()
                }
            if source_snapshot["source_type"] == "feishu_three_table":
                live_rows = {
                    domain: _materialize_source(
                        source_connection,
                        source_domain,
                        settings=settings,
                        schema=resolved_source.schema,
                    )
                    for domain, source_domain in (
                        ("opportunity", "opportunities"),
                        ("delivery", "deliveries"),
                        ("collection", "collections"),
                    )
                }
                total_read += sum(len(rows) for rows in live_rows.values())
                with SessionLocal() as db:
                    data_source = db.scalar(
                        select(DataSource).where(
                            DataSource.id == source_snapshot["id"],
                            DataSource.enterprise_id == source_snapshot["enterprise_id"],
                            DataSource.is_enabled.is_(True),
                        )
                    )
                    if data_source is None:
                        raise IngestionError("data_source_not_found", "同步期间数据源被移除")
                    domain_results = _activate_live_domains_atomically(
                        enterprise_id=enterprise_id,
                        data_source=data_source,
                        sync_run_id=sync_run_id,
                        rows_by_domain=live_rows,
                        organization_map=organization_map,
                        people_map=people_map,
                        customer_map=customer_map,
                        dataset_version=dataset_version,
                        fallback_source_data_as_of=source_data_as_of,
                    )
                total_written += sum(
                    int(result["records"])
                    for result in domain_results.values()
                    if result["status"] == "activated"
                )
            else:
                for domain, source_domain in DOMAIN_TO_SOURCE.items():
                    try:
                        rows = _materialize_source(
                            source_connection,
                            source_domain,
                            settings=settings,
                            schema=resolved_source.schema,
                        )
                        total_read += len(rows)
                        fingerprint = source_domain_fingerprint(rows)
                        with SessionLocal() as db:
                            data_source = db.scalar(
                                select(DataSource).where(
                                    DataSource.id == source_snapshot["id"],
                                    DataSource.enterprise_id == source_snapshot["enterprise_id"],
                                    DataSource.is_enabled.is_(True),
                                )
                            )
                            if data_source is None:
                                raise IngestionError(
                                    "data_source_not_found", "同步期间数据源被移除"
                                )
                            result = _activate_domain(
                                enterprise_id=enterprise_id,
                                data_source=data_source,
                                sync_run_id=sync_run_id,
                                domain=domain,
                                rows=rows,
                                organization_map=organization_map,
                                people_map=people_map,
                                customer_map=customer_map,
                                dataset_version=dataset_version,
                                source_data_as_of=max(
                                    (row["source_updated_at"] for row in rows),
                                    default=source_data_as_of,
                                ),
                                fingerprint=fingerprint,
                            )
                        domain_results[domain] = result
                        if result["status"] == "activated":
                            total_written += int(result["records"])
                    except Exception as exc:
                        code = getattr(exc, "code", "domain_sync_failed")
                        domain_results[domain] = {
                            "domain": domain,
                            "status": "failed",
                            "error_code": code,
                            "error_message": str(exc),
                        }
                        _set_domain_failure(
                            enterprise_id=enterprise_id,
                            data_source_id=data_source_id,
                            domain=domain,
                            source_type=str(source_snapshot["source_type"]),
                            source_display_name=str(source_snapshot["display_name"]),
                            code=code,
                            message=str(exc),
                        )
            successful = [
                result
                for result in domain_results.values()
                if result["status"] in {"activated", "unchanged"}
            ]
            if successful:
                rebuild_daily_snapshots(
                    enterprise_id=enterprise_id,
                    reference_date=reference_date,
                    dataset_version=dataset_version,
                    source_data_as_of=source_data_as_of,
                )
            expected_domain_count = (
                3
                if source_snapshot["source_type"] == "feishu_three_table"
                else len(DOMAIN_TO_SOURCE)
            )
            overall_status = (
                "completed"
                if len(successful) == expected_domain_count
                else "partial"
                if successful
                else "failed"
            )
            with SessionLocal.begin() as db:
                sync_run = db.get(DataSyncRun, sync_run_id)
                if sync_run is None:
                    raise IngestionError("sync_run_missing", "同步运行记录不存在")
                sync_run.status = overall_status
                sync_run.dataset_version = dataset_version
                sync_run.source_schema_version = inspection.schema_version
                sync_run.source_batch_id = str(source_batch["batch_id"])
                sync_run.source_data_as_of = source_data_as_of
                sync_run.completed_at = utc_now()
                sync_run.records_read = total_read
                sync_run.records_written = total_written
                sync_run.records_rejected = sum(
                    1 for result in domain_results.values() if result["status"] == "failed"
                )
                sync_run.domain_results_json = domain_results
                if overall_status != "completed":
                    sync_run.error_code = (
                        "partial_domain_failure" if successful else "all_domains_failed"
                    )
                    sync_run.error_message = "一个或多个数据域同步失败"
            return {
                "sync_run_id": str(sync_run_id),
                "status": overall_status,
                "records_read": total_read,
                "records_written": total_written,
                "dataset_version": dataset_version,
                "source_data_as_of": source_data_as_of.isoformat(),
                "domains": domain_results,
            }
    except Exception as exc:
        code = getattr(exc, "code", "source_sync_failed")
        if source_snapshot["source_type"] == "feishu_three_table":
            for domain in ("opportunity", "delivery", "collection"):
                _set_domain_failure(
                    enterprise_id=enterprise_id,
                    data_source_id=data_source_id,
                    domain=domain,
                    source_type=str(source_snapshot["source_type"]),
                    source_display_name=str(source_snapshot["display_name"]),
                    code=code,
                    message=str(exc),
                )
        with SessionLocal.begin() as db:
            sync_run = db.get(DataSyncRun, sync_run_id)
            if sync_run is not None:
                sync_run.status = "failed"
                sync_run.completed_at = utc_now()
                sync_run.records_read = total_read
                sync_run.records_written = total_written
                sync_run.domain_results_json = domain_results
                sync_run.error_code = code
                sync_run.error_message = str(exc)[:2000]
                if source_batch:
                    sync_run.source_batch_id = str(source_batch.get("batch_id"))
        if isinstance(exc, (IngestionError, SourceContractError)):
            raise
        raise IngestionError(code, str(exc)) from exc


def _job_requests_validation_only(payload: dict[str, Any]) -> bool:
    """Accept both current and legacy validation-only job contracts.

    Validation is fail-safe: either supported field can request it, so a mixed
    payload can never turn an explicitly non-activating job into an activation.
    """

    explicit = payload.get("validation_only")
    explicit_validation = explicit is True or (
        isinstance(explicit, str) and explicit.strip().lower() in {"true", "1", "yes"}
    )
    legacy_validation = str(payload.get("operation") or "").strip().lower() == "validate"
    return explicit_validation or legacy_validation


def run_data_sync_job(job: Job, settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    validate_only = _job_requests_validation_only(job.payload_json)
    try:
        data_source_id = uuid.UUID(str(job.payload_json["data_source_id"]))
    except (KeyError, ValueError) as exc:
        raise IngestionError("data_source_id_invalid", "同步任务缺少有效数据源 ID") from exc
    with SessionLocal() as db:
        data_source = db.scalar(
            select(DataSource).where(
                DataSource.id == data_source_id,
                DataSource.enterprise_id == job.enterprise_id,
                DataSource.is_enabled.is_(True),
            )
        )
        if data_source is None:
            raise IngestionError("data_source_not_found", "数据源不存在或未启用")
        resolved_source = resolve_source_connection(db, data_source, settings)
        source_type = data_source.source_type
    feishu_result = None
    if (
        settings.app_env == "local-demo"
        and source_type == "feishu_three_table"
        and settings.feishu_app_id
        and settings.feishu_runtime_secret
        and settings.source_writer_database_url
    ):
        writer_url = settings.source_writer_database_url.get_secret_value()
        if _database_target_identity(writer_url) != _database_target_identity(
            resolved_source.database_url
        ):
            raise IngestionError(
                "source_writer_target_mismatch",
                "飞书三表写入库与当前 DataSource 读取库不一致",
            )
        try:
            bindings = fixed_bindings_from_settings(settings)
            snapshot = fetch_fixed_live_snapshot_from_settings(
                settings,
                app_secret=settings.feishu_runtime_secret.get_secret_value(),
            )
            feishu_result = write_live_snapshot_to_source(
                snapshot,
                source_writer_database_url=writer_url,
                dataset_version=f"feishu-live-{snapshot.content_sha256[:16]}",
                schema=resolved_source.schema,
                schema_version=resolved_source.schema_version,
                bindings=bindings,
            )
        except Exception as exc:
            code = str(getattr(exc, "code", "feishu_live_sync_failed"))
            rejection_persisted = False
            rejection_persist_error: str | None = None
            if (
                resolved_source.schema_version == SOURCE_V3_SCHEMA_VERSION
                and settings.source_writer_database_url
            ):
                rejected_at = utc_now()
                rejected_batch_id = f"feishu-v3-rejected-{job.id}"
                rejected_hash = hashlib.sha256(
                    f"{job.id}:{code}".encode()
                ).hexdigest()
                try:
                    with connect_source(
                        settings.source_writer_database_url.get_secret_value(),
                        application_name="executive-ai-feishu-v3-rejection-audit",
                        read_only=False,
                    ) as source_connection:
                        require_valid_source_v3_contract(
                            source_connection,
                            expected_version=SOURCE_V3_SCHEMA_VERSION,
                            schema=resolved_source.schema,
                            require_read_only=False,
                        )
                        record_rejected_source_v3_batch(
                            source_connection,
                            batch_id=rejected_batch_id,
                            dataset_version=f"rejected-{rejected_at:%Y%m%d}",
                            source_data_as_of=rejected_at,
                            content_sha256=rejected_hash,
                            issues=(
                                {
                                    "severity": "error",
                                    "domain": str(
                                        (getattr(exc, "details", {}) or {}).get("domain")
                                        or "batch"
                                    ),
                                    "source_record_id": (
                                        (getattr(exc, "details", {}) or {}).get("record_id")
                                    ),
                                    "field_name": (
                                        (getattr(exc, "details", {}) or {}).get("field_name")
                                    ),
                                    "error_code": code,
                                    "message": str(exc),
                                    "details": getattr(exc, "details", {}) or {},
                                },
                            ),
                            schema=resolved_source.schema,
                        )
                    rejection_persisted = True
                except Exception as persist_exc:  # preserve the primary source failure
                    rejection_persist_error = type(persist_exc).__name__
            with SessionLocal.begin() as db:
                db.add(
                    DataSyncRun(
                        enterprise_id=job.enterprise_id,
                        data_source_id=data_source_id,
                        job_id=job.id,
                        trigger_type=str(job.payload_json.get("trigger_type", "manual")),
                        status="rejected",
                        source_schema_version=resolved_source.schema_version,
                        started_at=utc_now(),
                        completed_at=utc_now(),
                        records_rejected=1,
                        activation_mode="all_three_atomic",
                        atomic_activation_status="failed",
                        cross_table_validation_json={
                            "valid": False,
                            "stage": "feishu_extract_or_validate",
                            "error_code": code,
                            "details": getattr(exc, "details", {}),
                            "source_rejection_persisted": rejection_persisted,
                            "source_rejection_persist_error": rejection_persist_error,
                        },
                        error_code=code,
                        error_message=str(exc)[:2000],
                    )
                )
            for domain in ("opportunity", "delivery", "collection"):
                _set_domain_failure(
                    enterprise_id=job.enterprise_id,
                    data_source_id=data_source_id,
                    domain=domain,
                    source_type=source_type,
                    source_display_name=data_source.display_name,
                    code=code,
                    message=str(exc),
                )
            raise IngestionError(code, str(exc)) from exc
    if (
        settings.app_env == "local-demo"
        and source_type in {"simulated_generator", "simulated_feishu"}
        and settings.feishu_app_id
        and settings.feishu_runtime_secret
        and settings.feishu_bitable_app_token
        and settings.feishu_bitable_table_id
        and settings.source_writer_database_url
    ):
        writer_url = settings.source_writer_database_url.get_secret_value()
        if _database_target_identity(writer_url) != _database_target_identity(
            resolved_source.database_url
        ):
            raise IngestionError(
                "source_writer_target_mismatch",
                "飞书模拟数据写入库与当前 DataSource 读取库不一致",
            )
        with FeishuBitableClient(
            app_id=settings.feishu_app_id,
            app_secret=settings.feishu_runtime_secret.get_secret_value(),
            app_token=settings.feishu_bitable_app_token,
            table_id=settings.feishu_bitable_table_id,
        ) as client:
            feishu_result = sync_feishu_opportunities_to_source(
                client,
                source_writer_database_url=(settings.source_writer_database_url.get_secret_value()),
                dataset_version=settings.demo_dataset_version,
                reference_date=date.fromisoformat(settings.demo_reference_date),
                schema=resolved_source.schema,
            )
    result = run_data_sync(
        enterprise_id=job.enterprise_id,
        data_source_id=data_source_id,
        job_id=job.id,
        trigger_type=str(job.payload_json.get("trigger_type", "manual")),
        validate_only=validate_only,
        settings=settings,
    )
    if (
        resolved_source.schema_version == SOURCE_V3_SCHEMA_VERSION
        and not validate_only
        and settings.source_writer_database_url
        and result.get("source_batch_id")
    ):
        mark_source_v3_batch_activated(
            source_writer_database_url=settings.source_writer_database_url.get_secret_value(),
            batch_id=str(result["source_batch_id"]),
            schema=resolved_source.schema,
        )
    if feishu_result is not None:
        result["feishu_import"] = feishu_result
    return result
