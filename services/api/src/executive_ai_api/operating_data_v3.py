from __future__ import annotations

import hashlib
import re
import unicodedata
import uuid
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any

from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session

from .metric_policy import ensure_default_opportunity_weight_policy
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
    FactOpportunityParticipant,
    FactOpportunityProduct,
    FactTarget,
    OrganizationUnit,
    SourceCheckpoint,
)
from .security import utc_now
from .source_contract import SourceContractError
from .source_contract_v3 import (
    SOURCE_V3_DOMAINS,
    SOURCE_V3_SCHEMA_VERSION,
    iter_source_v3_rows,
    source_v3_domain_fingerprint,
)


class OperatingDataV3Error(RuntimeError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        self.code = code
        self.details = details or {}
        super().__init__(message)


LOCAL_DEMO_FIRST_CUTOVER_COUNTS = {
    "opportunity": 100,
    "delivery": 18,
    "collection": 54,
}
LOCAL_DEMO_FIRST_CUTOVER_AMOUNTS_CNY = {
    "signed_amount": Decimal("5336000"),
    "contract_amount": Decimal("5336000"),
    "receivable_amount": Decimal("5336000"),
    "collected_amount": Decimal("2385000"),
    "outstanding_amount": Decimal("2951000"),
}


def materialize_source_v3_batch(
    connection: Any,
    *,
    batch_id: str,
    schema: str,
    page_size: int,
) -> dict[str, list[dict[str, Any]]]:
    rows: dict[str, list[dict[str, Any]]] = {}
    for domain in SOURCE_V3_DOMAINS:
        materialized: list[dict[str, Any]] = []
        for page in iter_source_v3_rows(
            connection,
            domain,
            batch_id=batch_id,
            schema=schema,
            page_size=page_size,
        ):
            materialized.extend(page)
        rows[domain] = materialized
    return rows


def _normalize_name(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    return re.sub(r"\s+", " ", text).casefold()


def _stable_source_id(prefix: str, value: object) -> str:
    normalized = _normalize_name(value)
    return f"{prefix}-{hashlib.sha256(normalized.encode('utf-8')).hexdigest()[:24]}"


def _as_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, date):
        return datetime.combine(value, time.min, tzinfo=UTC)
    return None


def _as_decimal(value: object) -> Decimal:
    return Decimal(str(value or 0))


def validate_local_demo_first_cutover_fingerprint(
    validation: dict[str, Any],
) -> dict[str, Any]:
    counts = dict(validation.get("record_counts") or {})
    if counts != LOCAL_DEMO_FIRST_CUTOVER_COUNTS:
        raise OperatingDataV3Error(
            "local_demo_first_cutover_count_mismatch",
            "首次演示数据切换必须精确通过 100/18/54 记录门禁",
            {"expected": LOCAL_DEMO_FIRST_CUTOVER_COUNTS, "actual": counts},
        )
    actual_amounts = {
        key: Decimal(str((validation.get("amount_checks") or {}).get(key, "NaN")))
        for key in LOCAL_DEMO_FIRST_CUTOVER_AMOUNTS_CNY
    }
    if actual_amounts != LOCAL_DEMO_FIRST_CUTOVER_AMOUNTS_CNY:
        raise OperatingDataV3Error(
            "local_demo_first_cutover_amount_mismatch",
            "首次演示数据切换金额指纹不匹配",
            {
                "unit": "CNY",
                "expected": {
                    key: str(value)
                    for key, value in LOCAL_DEMO_FIRST_CUTOVER_AMOUNTS_CNY.items()
                },
                "actual": {key: str(value) for key, value in actual_amounts.items()},
            },
        )
    return {
        "status": "passed",
        "record_counts": dict(LOCAL_DEMO_FIRST_CUTOVER_COUNTS),
        "amounts_cny": {
            key: str(value) for key, value in LOCAL_DEMO_FIRST_CUTOVER_AMOUNTS_CNY.items()
        },
    }


def _as_percent(value: object) -> int:
    number = _as_decimal(value)
    if number <= 1:
        number *= 100
    return int(number.quantize(Decimal("1")))


def _clean_values(value: object) -> list[str]:
    if value in (None, ""):
        return []
    values = value if isinstance(value, (list, tuple, set)) else [value]
    output: list[str] = []
    for raw in values:
        text = str(raw).strip()
        if text and text not in output:
            output.append(text)
    return output


def validate_business_v3_batch(
    rows_by_domain: dict[str, list[dict[str, Any]]],
    source_batch: dict[str, Any],
) -> dict[str, Any]:
    missing = [domain for domain in SOURCE_V3_DOMAINS if not rows_by_domain.get(domain)]
    if missing:
        raise OperatingDataV3Error(
            "source_domain_empty",
            "ODS 3.0 三个数据域都必须包含记录",
            {"missing_domains": missing},
        )

    expected_counts = source_batch.get("record_counts") or {}
    actual_counts = {domain: len(rows_by_domain[domain]) for domain in SOURCE_V3_DOMAINS}
    source_systems = {
        str(row.get("source_system") or "").strip()
        for domain in SOURCE_V3_DOMAINS
        for row in rows_by_domain[domain]
    }
    if len(source_systems) != 1 or "" in source_systems:
        raise OperatingDataV3Error(
            "source_system_mismatch",
            "同一原子批次的三张表必须来自同一个明确的数据源",
            {"source_systems": sorted(source_systems)},
        )
    for domain, actual in actual_counts.items():
        expected = expected_counts.get(domain)
        if expected is None:
            expected = expected_counts.get(f"{domain}s")
        if expected is not None and int(expected) != actual:
            raise OperatingDataV3Error(
                "source_record_count_mismatch",
                "ODS 3.0 批次声明数量与实际记录不一致",
                {"domain": domain, "expected": int(expected), "actual": actual},
            )

    opportunities = {str(row["opportunity_code"]): row for row in rows_by_domain["opportunity"]}
    deliveries = {str(row["project_code"]): row for row in rows_by_domain["delivery"]}
    if len(opportunities) != actual_counts["opportunity"]:
        raise OperatingDataV3Error("opportunity_id_duplicate", "商机 ID 重复")
    if len(deliveries) != actual_counts["delivery"]:
        raise OperatingDataV3Error("project_id_duplicate", "项目 ID 重复")

    collection_ids: set[str] = set()
    nodes_by_project: dict[str, int] = {}
    receivable_by_project: dict[str, Decimal] = {}
    total_receivable = Decimal("0")
    total_collected = Decimal("0")
    total_outstanding = Decimal("0")

    for project_code, project in deliveries.items():
        opportunity = opportunities.get(str(project["opportunity_code"]))
        if opportunity is None:
            raise OperatingDataV3Error("delivery_orphan", "项目关联了未知商机")
        if str(opportunity["status_code"]) != "won":
            raise OperatingDataV3Error("delivery_opportunity_not_won", "项目必须关联赢单商机")
        if str(project["customer_name"]) != str(opportunity["customer_name"]):
            raise OperatingDataV3Error("delivery_customer_mismatch", "项目与商机客户不一致")
        if str(project["organization_code"]) != str(opportunity["organization_code"]):
            raise OperatingDataV3Error("delivery_organization_mismatch", "项目与商机事业部不一致")
        if _as_decimal(project["contract_amount"]) != _as_decimal(opportunity["signed_amount"]):
            raise OperatingDataV3Error(
                "signed_contract_amount_mismatch", "赢单商机签约金额与项目合同额不一致"
            )
        nodes_by_project[project_code] = 0
        receivable_by_project[project_code] = Decimal("0")

    for row in rows_by_domain["collection"]:
        collection_code = str(row["collection_code"])
        if collection_code in collection_ids:
            raise OperatingDataV3Error("collection_id_duplicate", "回款记录 ID 重复")
        collection_ids.add(collection_code)
        project_code = str(row["project_code"])
        project = deliveries.get(project_code)
        opportunity = opportunities.get(str(row["opportunity_code"]))
        if project is None or opportunity is None:
            raise OperatingDataV3Error("collection_orphan", "回款关联了未知项目或商机")
        if str(project["opportunity_code"]) != str(row["opportunity_code"]):
            raise OperatingDataV3Error("collection_relation_mismatch", "回款的项目与商机关系不一致")
        if str(row["customer_name"]) != str(project["customer_name"]):
            raise OperatingDataV3Error("collection_customer_mismatch", "回款与项目客户不一致")
        if str(row["organization_code"]) != str(project["organization_code"]):
            raise OperatingDataV3Error("collection_organization_mismatch", "回款与项目事业部不一致")
        receivable = _as_decimal(row["receivable_amount"])
        collected = _as_decimal(row["collected_amount"])
        outstanding = _as_decimal(row["outstanding_amount"])
        if receivable != collected + outstanding:
            raise OperatingDataV3Error(
                "collection_amount_invariant_failed", "应收金额必须等于已回款金额与未回款金额之和"
            )
        nodes_by_project[project_code] += 1
        receivable_by_project[project_code] += receivable
        total_receivable += receivable
        total_collected += collected
        total_outstanding += outstanding

    for project_code, project in deliveries.items():
        if nodes_by_project[project_code] != 3:
            raise OperatingDataV3Error(
                "payment_node_count_invalid", f"项目 {project_code} 必须包含 3 个回款节点"
            )
        if receivable_by_project[project_code] != _as_decimal(project["contract_amount"]):
            raise OperatingDataV3Error(
                "project_receivable_mismatch", f"项目 {project_code} 合同额与应收节点合计不一致"
            )

    signed_total = sum(
        (
            _as_decimal(row["signed_amount"])
            for row in opportunities.values()
            if row["status_code"] == "won"
        ),
        Decimal("0"),
    )
    contract_total = sum(
        (_as_decimal(row["contract_amount"]) for row in deliveries.values()), Decimal("0")
    )
    if signed_total != contract_total or contract_total != total_receivable:
        raise OperatingDataV3Error(
            "three_table_amount_mismatch", "签约金额、项目合同额与回款应收额必须一致"
        )
    if total_receivable != total_collected + total_outstanding:
        raise OperatingDataV3Error("batch_amount_invariant_failed", "三表汇总金额不平")

    return {
        "valid": True,
        "source_system": next(iter(source_systems)),
        "record_counts": actual_counts,
        "relationship_checks": {
            "delivery_opportunity": "passed",
            "collection_delivery_opportunity": "passed",
            "three_payment_nodes_per_project": "passed",
        },
        "amount_checks": {
            "signed_amount": str(signed_total),
            "contract_amount": str(contract_total),
            "receivable_amount": str(total_receivable),
            "collected_amount": str(total_collected),
            "outstanding_amount": str(total_outstanding),
        },
    }


def _dimension_quality_warnings(
    db: Session,
    *,
    enterprise_id: uuid.UUID,
    opportunity_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Surface stable-business-key identity changes without silently merging them."""

    existing = {
        str(code): str(customer_name)
        for code, customer_name in db.execute(
            select(FactOpportunity.opportunity_code, DimCustomer.display_name)
            .join(DimCustomer, DimCustomer.id == FactOpportunity.customer_id)
            .where(
                FactOpportunity.enterprise_id == enterprise_id,
                FactOpportunity.is_current.is_(True),
            )
        ).all()
    }
    warnings: list[dict[str, Any]] = []
    for row in opportunity_rows:
        opportunity_code = str(row["opportunity_code"])
        previous = existing.get(opportunity_code)
        current = str(row["customer_name"]).strip()
        if previous and _normalize_name(previous) != _normalize_name(current):
            warnings.append(
                {
                    "severity": "warning",
                    "code": "customer_display_name_changed",
                    "domain": "opportunity",
                    "source_record_id": str(row["source_record_id"]),
                    "opportunity_code": opportunity_code,
                    "previous_display_name": previous,
                    "current_display_name": current,
                    "message": "同一商机的客户展示名称发生实质变化，已建立新客户维度且未静默合并",
                }
            )
    return warnings


def _upsert_v3_dimensions(
    db: Session,
    *,
    enterprise_id: uuid.UUID,
    data_source_id: uuid.UUID,
    rows_by_domain: dict[str, list[dict[str, Any]]],
    dataset_version: str,
) -> tuple[dict[str, OrganizationUnit], dict[str, DimPerson], dict[str, DimCustomer]]:
    now = utc_now()
    all_rows = [row for domain in SOURCE_V3_DOMAINS for row in rows_by_domain[domain]]
    source_systems = {str(row["source_system"]) for row in all_rows}
    if len(source_systems) != 1:
        raise OperatingDataV3Error("source_system_mismatch", "三表数据源标识不一致")
    source_system = next(iter(source_systems))
    organizations: dict[str, tuple[str, datetime]] = {}
    for row in all_rows:
        code = str(row["organization_code"])
        updated_at = row["source_updated_at"]
        previous = organizations.get(code)
        if previous is None or updated_at > previous[1]:
            organizations[code] = (str(row["organization_name"]), updated_at)

    existing_orgs = {
        item.code: item
        for item in db.scalars(
            select(OrganizationUnit).where(OrganizationUnit.enterprise_id == enterprise_id)
        ).all()
    }
    active_codes = set(organizations)
    for index, (code, (name, _)) in enumerate(sorted(organizations.items()), start=1):
        item = existing_orgs.get(code)
        if item is None:
            item = OrganizationUnit(enterprise_id=enterprise_id, code=code, name=name)
            db.add(item)
            db.flush()
            existing_orgs[code] = item
        item.name = name
        item.unit_type = "division"
        item.sort_order = index * 10
        item.data_connected = True
        item.enabled_for_analysis = True
        item.is_active = True
        item.config_json = {**(item.config_json or {}), "ods_contract": "3.0"}
    for code, item in existing_orgs.items():
        if code not in active_codes:
            item.data_connected = False
            item.enabled_for_analysis = False
            item.is_active = False
    db.flush()
    organization_map = {code: existing_orgs[code] for code in active_codes}

    person_specs: dict[str, dict[str, Any]] = {}

    def add_person(name: object, organization_code: str, role: str, updated_at: datetime) -> None:
        text = str(name or "").strip()
        if not text:
            return
        normalized = _normalize_name(text)
        spec = person_specs.setdefault(
            normalized,
            {
                "display_name": text,
                "organization_codes": set(),
                "roles": set(),
                "source_updated_at": updated_at,
            },
        )
        spec["organization_codes"].add(organization_code)
        spec["roles"].add(role)
        if updated_at > spec["source_updated_at"]:
            spec["source_updated_at"] = updated_at

    for row in rows_by_domain["opportunity"]:
        add_person(
            row["sales_owner"], row["organization_code"], "sales_owner", row["source_updated_at"]
        )
        for name in _clean_values(row["presales_owners"]):
            add_person(name, row["organization_code"], "presales_owner", row["source_updated_at"])
    for row in rows_by_domain["delivery"]:
        add_person(
            row["project_manager"],
            row["organization_code"],
            "project_manager",
            row["source_updated_at"],
        )
        for name in _clean_values(row["delivery_owners"]):
            add_person(name, row["organization_code"], "delivery_owner", row["source_updated_at"])
    for row in rows_by_domain["collection"]:
        add_person(
            row["collection_owner"],
            row["organization_code"],
            "collection_owner",
            row["source_updated_at"],
        )

    existing_people = {
        item.source_record_id: item
        for item in db.scalars(
            select(DimPerson).where(
                DimPerson.enterprise_id == enterprise_id,
                DimPerson.data_source_id == data_source_id,
            )
        ).all()
    }
    people_by_name: dict[str, DimPerson] = {}
    active_person_ids: set[str] = set()
    for normalized, spec in person_specs.items():
        source_id = _stable_source_id("person", normalized)
        active_person_ids.add(source_id)
        item = existing_people.get(source_id)
        if item is None:
            item = DimPerson(
                enterprise_id=enterprise_id,
                data_source_id=data_source_id,
                source_record_id=source_id,
                display_name=spec["display_name"],
                source_system=source_system,
                source_updated_at=spec["source_updated_at"],
                synced_at=now,
            )
            db.add(item)
            existing_people[source_id] = item
        item.organization_unit_id = (
            organization_map[next(iter(spec["organization_codes"]))].id
            if len(spec["organization_codes"]) == 1
            else None
        )
        item.display_name = spec["display_name"]
        item.normalized_name = normalized
        item.identity_fingerprint = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        item.role_types_json = sorted(spec["roles"])
        item.role_title = "、".join(sorted(spec["roles"]))
        item.is_active = True
        item.dataset_version = dataset_version
        item.source_system = source_system
        item.source_updated_at = spec["source_updated_at"]
        item.synced_at = now
        people_by_name[normalized] = item
    for source_id, item in existing_people.items():
        if source_id not in active_person_ids:
            item.is_active = False
    db.flush()

    customer_specs: dict[str, dict[str, Any]] = {}
    for row in rows_by_domain["opportunity"]:
        normalized = _normalize_name(row["customer_name"])
        spec = customer_specs.setdefault(
            normalized,
            {
                "display_name": str(row["customer_name"]).strip(),
                "organization_codes": set(),
                "industry": row.get("industry"),
                "customer_value_level": row.get("customer_value_level"),
                "sales_owners": set(),
                "source_updated_at": row["source_updated_at"],
            },
        )
        spec["organization_codes"].add(str(row["organization_code"]))
        if str(row.get("sales_owner") or "").strip():
            spec["sales_owners"].add(str(row["sales_owner"]).strip())
        if row["source_updated_at"] > spec["source_updated_at"]:
            spec["source_updated_at"] = row["source_updated_at"]
        if str(spec["industry"] or "") != str(row.get("industry") or "") or str(
            spec["customer_value_level"] or ""
        ) != str(row.get("customer_value_level") or ""):
            raise OperatingDataV3Error(
                "customer_identity_conflict",
                f"客户 {row['customer_name']} 在同一批次出现不同行业或价值等级，禁止静默合并",
            )

    existing_customers = {
        item.source_record_id: item
        for item in db.scalars(
            select(DimCustomer).where(
                DimCustomer.enterprise_id == enterprise_id,
                DimCustomer.data_source_id == data_source_id,
            )
        ).all()
    }
    customers_by_name: dict[str, DimCustomer] = {}
    for normalized, spec in customer_specs.items():
        source_id = _stable_source_id("customer", normalized)
        item = existing_customers.get(source_id)
        if item is None:
            item = DimCustomer(
                enterprise_id=enterprise_id,
                data_source_id=data_source_id,
                source_record_id=source_id,
                display_name=spec["display_name"],
                source_system=source_system,
                source_updated_at=spec["source_updated_at"],
                synced_at=now,
            )
            db.add(item)
            existing_customers[source_id] = item
        item.organization_unit_id = (
            organization_map[next(iter(spec["organization_codes"]))].id
            if len(spec["organization_codes"]) == 1
            else None
        )
        owner_names = sorted(spec["sales_owners"])
        owner = (
            people_by_name.get(_normalize_name(owner_names[0]))
            if len(owner_names) == 1
            else None
        )
        item.owner_person_id = owner.id if owner else None
        item.display_name = spec["display_name"]
        item.normalized_name = normalized
        item.identity_fingerprint = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        item.aliases_json = []
        item.customer_value_level = spec["customer_value_level"]
        item.industry = spec["industry"]
        item.source_system = source_system
        item.dataset_version = dataset_version
        item.source_updated_at = spec["source_updated_at"]
        item.synced_at = now
        customers_by_name[normalized] = item
    db.flush()
    return organization_map, people_by_name, customers_by_name


def activate_source_v3_batch(
    db: Session,
    *,
    enterprise_id: uuid.UUID,
    data_source: DataSource,
    sync_run_id: uuid.UUID,
    source_batch: dict[str, Any],
    rows_by_domain: dict[str, list[dict[str, Any]]],
    validate_only: bool,
) -> dict[str, Any]:
    """Validate and optionally switch all three business domains in one transaction."""

    validation = validate_business_v3_batch(rows_by_domain, source_batch)
    source_validation = source_batch.get("validation_result") or {}
    if isinstance(source_validation, dict) and isinstance(source_validation.get("warnings"), list):
        validation["warnings"] = list(source_validation["warnings"])
    validation["quality_warnings"] = _dimension_quality_warnings(
        db,
        enterprise_id=enterprise_id,
        opportunity_rows=rows_by_domain["opportunity"],
    )
    sync_run = db.get(DataSyncRun, sync_run_id)
    if sync_run is None:
        raise OperatingDataV3Error("sync_run_missing", "同步运行记录不存在")
    batch_id = str(source_batch["batch_id"])
    dataset_version = str(source_batch["dataset_version"])
    schema_hashes = dict(source_batch.get("table_schema_sha256") or {})
    content_hashes = dict(source_batch.get("table_content_sha256") or {})
    counts = validation["record_counts"]
    policy = ensure_default_opportunity_weight_policy(db, enterprise_id=enterprise_id)
    statuses = {
        item.domain: item
        for item in db.scalars(
            select(DataDomainStatus).where(
                DataDomainStatus.enterprise_id == enterprise_id,
                DataDomainStatus.domain.in_((*SOURCE_V3_DOMAINS, "target")),
            )
        ).all()
    }

    # The local demonstration cutover is intentionally pinned once to the
    # user-approved 100/18/54 fingerprint.  After the first successful V3
    # activation, normal full-snapshot additions/removals are allowed.
    has_v3_activation = any(
        statuses.get(domain) and statuses[domain].current_source_batch_id
        for domain in SOURCE_V3_DOMAINS
    )
    if data_source.key == "demo-sanitized-source" and not has_v3_activation:
        validation["first_activation_gate"] = validate_local_demo_first_cutover_fingerprint(
            validation
        )

    sync_run.dataset_version = dataset_version
    sync_run.source_schema_version = SOURCE_V3_SCHEMA_VERSION
    sync_run.source_batch_id = batch_id
    sync_run.source_data_as_of = source_batch["source_data_as_of"]
    sync_run.source_schema_hashes_json = schema_hashes
    sync_run.source_record_counts_json = counts
    sync_run.source_content_hashes_json = content_hashes
    sync_run.cross_table_validation_json = validation
    sync_run.activation_mode = "all_three_atomic"
    sync_run.experience_weight_policy_id = policy.id

    if validate_only:
        sync_run.status = "validated"
        sync_run.atomic_activation_status = "not_requested"
        sync_run.records_read = sum(counts.values())
        sync_run.records_written = 0
        sync_run.completed_at = utc_now()
        return {
            "status": "validated",
            "source_batch_id": batch_id,
            "record_counts": counts,
            "validation": validation,
            "experience_weight_policy_version": policy.version,
        }

    if (
        all(
            statuses.get(domain)
            and statuses[domain].current_source_batch_id == batch_id
            and statuses[domain].active_sync_run_id is not None
            and statuses[domain].data_source_id == data_source.id
            for domain in SOURCE_V3_DOMAINS
        )
        and len({statuses[domain].active_sync_run_id for domain in SOURCE_V3_DOMAINS}) == 1
    ):
        sync_run.status = "completed"
        sync_run.atomic_activation_status = "unchanged"
        sync_run.records_read = sum(counts.values())
        sync_run.completed_at = utc_now()
        return {
            "status": "unchanged",
            "source_batch_id": batch_id,
            "record_counts": counts,
            "validation": validation,
            "experience_weight_policy_version": policy.version,
        }

    sync_run.activation_started_at = utc_now()
    sync_run.atomic_activation_status = "activating"
    organization_map, people_by_name, customers_by_name = _upsert_v3_dimensions(
        db,
        enterprise_id=enterprise_id,
        data_source_id=data_source.id,
        rows_by_domain=rows_by_domain,
        dataset_version=dataset_version,
    )

    opportunity_rows: list[dict[str, Any]] = []
    opportunity_ids: dict[str, uuid.UUID] = {}
    participant_specs: list[tuple[uuid.UUID, str, str, int]] = []
    product_specs: list[tuple[uuid.UUID, str, int]] = []
    for row in rows_by_domain["opportunity"]:
        opportunity_id = uuid.uuid4()
        code = str(row["opportunity_code"])
        opportunity_ids[code] = opportunity_id
        customer = customers_by_name[_normalize_name(row["customer_name"])]
        sales_owner = people_by_name.get(_normalize_name(row["sales_owner"]))
        archived_at = _as_datetime(row.get("archived_at"))
        opportunity_rows.append(
            {
                "id": opportunity_id,
                "enterprise_id": enterprise_id,
                "data_source_id": data_source.id,
                "sync_run_id": sync_run_id,
                "organization_unit_id": organization_map[str(row["organization_code"])].id,
                "customer_id": customer.id,
                "owner_person_id": sales_owner.id if sales_owner else None,
                "source_record_id": str(row["source_record_id"]),
                "upstream_record_id": row.get("source_native_record_id"),
                "opportunity_code": code,
                "title": str(row["title"]),
                "stage": str(row["stage_label"]),
                "status": str(row["status_code"]),
                "stage_label": str(row["stage_label"]),
                "status_code": str(row["status_code"]),
                "reliability_level": str(row["reliability_level"]),
                "customer_value_level": str(row["customer_value_level"]),
                "industry": str(row["industry"]),
                "probability": None,
                "expected_amount": _as_decimal(row["expected_amount"]),
                "signed_amount": (
                    _as_decimal(row["signed_amount"])
                    if row.get("signed_amount") is not None
                    else None
                ),
                "expected_gross_profit": None,
                "created_date": row["entered_date"],
                "expected_close_date": row["expected_close_date"],
                "closed_date": None,
                "is_archived": bool(row["is_archived"]),
                "archived_at": archived_at,
                "latest_progress": row.get("latest_progress"),
                "source_system": str(row["source_system"]),
                "source_updated_at": row["source_updated_at"],
                "dataset_version": dataset_version,
                "is_current": False,
            }
        )
        if sales_owner:
            participant_specs.append(
                (opportunity_id, _normalize_name(row["sales_owner"]), "sales_owner", 0)
            )
        for index, name in enumerate(_clean_values(row["presales_owners"]), start=1):
            participant_specs.append(
                (opportunity_id, _normalize_name(name), "presales_owner", index)
            )
        for index, product in enumerate(_clean_values(row["products_services"])):
            product_specs.append((opportunity_id, product, index))

    db.execute(insert(FactOpportunity), opportunity_rows)
    participant_rows = [
        {
            "enterprise_id": enterprise_id,
            "sync_run_id": sync_run_id,
            "opportunity_id": opportunity_id,
            "person_id": people_by_name[name].id,
            "participant_role": role,
            "sort_order": sort_order,
        }
        for opportunity_id, name, role, sort_order in participant_specs
        if name in people_by_name
    ]
    if participant_rows:
        db.execute(insert(FactOpportunityParticipant), participant_rows)
    product_rows = [
        {
            "enterprise_id": enterprise_id,
            "sync_run_id": sync_run_id,
            "opportunity_id": opportunity_id,
            "product_name": product,
            "normalized_product_name": _normalize_name(product),
            "sort_order": sort_order,
        }
        for opportunity_id, product, sort_order in product_specs
    ]
    if product_rows:
        db.execute(insert(FactOpportunityProduct), product_rows)

    delivery_rows: list[dict[str, Any]] = []
    delivery_ids: dict[str, uuid.UUID] = {}
    for row in rows_by_domain["delivery"]:
        delivery_id = uuid.uuid4()
        project_code = str(row["project_code"])
        delivery_ids[project_code] = delivery_id
        manager = people_by_name.get(_normalize_name(row["project_manager"]))
        delivery_names = _clean_values(row["delivery_owners"])
        if len(delivery_names) != 1:
            raise OperatingDataV3Error(
                "delivery_owner_cardinality_invalid",
                f"项目 {project_code} 当前必须且只能有一个交付负责人",
            )
        delivery_owner = (
            people_by_name.get(_normalize_name(delivery_names[0])) if delivery_names else None
        )
        customer = customers_by_name[_normalize_name(row["customer_name"])]
        delivery_rows.append(
            {
                "id": delivery_id,
                "enterprise_id": enterprise_id,
                "data_source_id": data_source.id,
                "sync_run_id": sync_run_id,
                "organization_unit_id": organization_map[str(row["organization_code"])].id,
                "customer_id": customer.id,
                "manager_person_id": manager.id if manager else None,
                "delivery_owner_person_id": delivery_owner.id if delivery_owner else None,
                "opportunity_fact_id": opportunity_ids[str(row["opportunity_code"])],
                "source_record_id": str(row["source_record_id"]),
                "opportunity_source_record_id": str(row["opportunity_code"]),
                "project_code": project_code,
                "project_name": str(row["project_name"]),
                "status": str(row["status_code"]),
                "risk_level": str(row["risk_level"]),
                "completion_percent": _as_percent(row["completion_rate"]),
                "contract_amount": _as_decimal(row["contract_amount"]),
                "recognized_revenue": _as_decimal(row["recognized_revenue"]),
                "gross_margin_rate": _as_decimal(row["gross_margin_rate"]),
                "planned_start_date": row["planned_start_date"],
                "planned_end_date": row["planned_end_date"],
                "actual_start_date": row.get("actual_start_date"),
                "actual_end_date": row.get("actual_end_date"),
                "current_milestone": row.get("current_milestone"),
                "delay_days": int(row.get("delay_days") or 0),
                "latest_progress": row.get("latest_progress"),
                "source_system": str(row["source_system"]),
                "source_updated_at": row["source_updated_at"],
                "dataset_version": dataset_version,
                "is_current": False,
            }
        )
    db.execute(insert(FactDelivery), delivery_rows)

    collection_rows: list[dict[str, Any]] = []
    for row in rows_by_domain["collection"]:
        customer = customers_by_name[_normalize_name(row["customer_name"])]
        owner = people_by_name.get(_normalize_name(row["collection_owner"]))
        collection_rows.append(
            {
                "enterprise_id": enterprise_id,
                "data_source_id": data_source.id,
                "sync_run_id": sync_run_id,
                "organization_unit_id": organization_map[str(row["organization_code"])].id,
                "customer_id": customer.id,
                "opportunity_fact_id": opportunity_ids[str(row["opportunity_code"])],
                "delivery_fact_id": delivery_ids[str(row["project_code"])],
                "collection_owner_person_id": owner.id if owner else None,
                "source_record_id": str(row["source_record_id"]),
                "project_source_record_id": str(row["project_code"]),
                "invoice_amount": None,
                "receivable_amount": _as_decimal(row["receivable_amount"]),
                "collected_amount": _as_decimal(row["collected_amount"]),
                "outstanding_amount": _as_decimal(row["outstanding_amount"]),
                "planned_collection_date": row["planned_collection_date"],
                "actual_collection_date": row.get("actual_collection_date"),
                "overdue_days": int(row.get("overdue_days") or 0),
                "aging_bucket": str(row["aging_bucket"]),
                "status": str(row["status_label"]),
                "payment_type": str(row["payment_type"]),
                "payment_milestone": str(row["payment_milestone"]),
                "invoice_status": str(row["invoice_status"]),
                "invoice_number": row.get("invoice_number"),
                "latest_follow_up": row.get("latest_follow_up"),
                "source_system": str(row["source_system"]),
                "source_updated_at": row["source_updated_at"],
                "dataset_version": dataset_version,
                "is_current": False,
            }
        )
    db.execute(insert(FactFinanceCollection), collection_rows)

    for model in (FactOpportunity, FactDelivery, FactFinanceCollection):
        db.execute(
            update(model)
            .where(
                model.enterprise_id == enterprise_id,
                model.sync_run_id != sync_run_id,
                model.is_current.is_(True),
            )
            .values(is_current=False)
        )
        db.execute(update(model).where(model.sync_run_id == sync_run_id).values(is_current=True))
    db.execute(
        update(FactTarget)
        .where(FactTarget.enterprise_id == enterprise_id, FactTarget.is_current.is_(True))
        .values(is_current=False)
    )

    source_times = {
        domain: max(
            (
                row.get("data_updated_at") or row["source_updated_at"]
                for row in rows_by_domain[domain]
            ),
            default=source_batch["source_data_as_of"],
        )
        for domain in SOURCE_V3_DOMAINS
    }
    domain_results: dict[str, Any] = {}
    for domain in SOURCE_V3_DOMAINS:
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
        status.record_count = counts[domain]
        status.dataset_version = dataset_version
        status.source_type = data_source.source_type
        status.source_display_name = data_source.display_name
        status.current_source_batch_id = batch_id
        status.contract_version = SOURCE_V3_SCHEMA_VERSION
        status.status_reason = None
        status.last_error_code = None
        status.last_error_message = None
        checkpoint = db.scalar(
            select(SourceCheckpoint).where(
                SourceCheckpoint.data_source_id == data_source.id,
                SourceCheckpoint.domain == domain,
            )
        )
        if checkpoint is None:
            checkpoint = SourceCheckpoint(data_source_id=data_source.id, domain=domain)
            db.add(checkpoint)
        checkpoint.source_updated_at = source_times[domain]
        checkpoint.source_batch_id = batch_id
        checkpoint.checksum = source_v3_domain_fingerprint(rows_by_domain[domain])
        domain_results[domain] = {
            "domain": domain,
            "status": "activated",
            "records": counts[domain],
            "source_batch_id": batch_id,
            "source_data_as_of": source_times[domain].isoformat(),
        }

    target = statuses.get("target")
    if target is None:
        target = DataDomainStatus(
            enterprise_id=enterprise_id,
            data_source_id=data_source.id,
            domain="target",
            source_type=data_source.source_type,
            source_display_name=data_source.display_name,
        )
        db.add(target)
    target.previous_sync_run_id = target.active_sync_run_id
    target.active_sync_run_id = None
    target.status = "not_configured"
    target.record_count = 0
    target.dataset_version = None
    target.current_source_batch_id = None
    target.contract_version = SOURCE_V3_SCHEMA_VERSION
    target.status_reason = "目标数据尚未接入"
    target.last_error_code = None
    target.last_error_message = None

    freshness = {
        domain: {
            "status": statuses[domain].status,
            "source_batch_id": batch_id,
            "source_data_as_of": source_times[domain].isoformat(),
            "contract_version": SOURCE_V3_SCHEMA_VERSION,
        }
        for domain in SOURCE_V3_DOMAINS
    }
    weights = {key: Decimal(str(value)) for key, value in policy.weights_json.items()}
    units = list(organization_map.values())
    for organization_id in [None, *[item.id for item in units]]:
        opportunities = [
            row
            for row in opportunity_rows
            if organization_id is None or row["organization_unit_id"] == organization_id
        ]
        deliveries = [
            row
            for row in delivery_rows
            if organization_id is None or row["organization_unit_id"] == organization_id
        ]
        collections = [
            row
            for row in collection_rows
            if organization_id is None or row["organization_unit_id"] == organization_id
        ]
        active_pipeline = sum(
            (row["expected_amount"] for row in opportunities if row["status_code"] == "active"),
            Decimal("0"),
        )
        weighted_pipeline = sum(
            (
                row["expected_amount"] * weights.get(str(row["reliability_level"]), Decimal("0"))
                for row in opportunities
                if row["status_code"] == "active"
            ),
            Decimal("0"),
        )
        signed_amount = sum(
            (
                row["signed_amount"] or Decimal("0")
                for row in opportunities
                if row["status_code"] == "won"
            ),
            Decimal("0"),
        )
        contract_amount = sum((row["contract_amount"] for row in deliveries), Decimal("0"))
        recognized_revenue = sum(
            (row["recognized_revenue"] or Decimal("0") for row in deliveries),
            Decimal("0"),
        )
        recognized_gross_profit = sum(
            (
                (row["recognized_revenue"] or Decimal("0")) * row["gross_margin_rate"]
                for row in deliveries
            ),
            Decimal("0"),
        )
        receivable_amount = sum((row["receivable_amount"] for row in collections), Decimal("0"))
        collected_amount = sum((row["collected_amount"] for row in collections), Decimal("0"))
        outstanding_amount = sum((row["outstanding_amount"] for row in collections), Decimal("0"))
        overdue_amount = sum(
            (row["outstanding_amount"] for row in collections if int(row["overdue_days"]) > 0),
            Decimal("0"),
        )
        anomalies: list[dict[str, Any]] = []
        delayed_count = sum(1 for row in deliveries if row["status"] == "delayed")
        if delayed_count:
            anomalies.append(
                {
                    "domain": "delivery",
                    "severity": "attention",
                    "title": f"{delayed_count} 个项目处于延期状态",
                }
            )
        if overdue_amount > 0:
            anomalies.append(
                {
                    "domain": "collection",
                    "severity": "attention",
                    "title": "存在逾期未回款",
                    "value": float(overdue_amount),
                    "unit": "元",
                }
            )
        db.add(
            DailySnapshot(
                enterprise_id=enterprise_id,
                organization_unit_id=organization_id,
                snapshot_date=source_batch["reference_date"],
                source_data_as_of=source_batch["source_data_as_of"],
                dataset_version=dataset_version,
                source_batch_id=batch_id,
                metrics_json={
                    "_domain_freshness": freshness,
                    "_experience_weight_policy": {
                        "version": policy.version,
                        "weights": policy.weights_json,
                        "label": policy.label,
                    },
                    "opportunity_count": len(opportunities),
                    "active_pipeline_amount": float(active_pipeline),
                    "experience_weighted_pipeline_amount": float(weighted_pipeline),
                    "signed_amount": float(signed_amount),
                    "delivery_count": len(deliveries),
                    "delivery_attention_count": sum(
                        1 for row in deliveries if row["risk_level"] not in {"normal", "正常"}
                    ),
                    "delivery_delayed_count": delayed_count,
                    "contract_amount": float(contract_amount),
                    "recognized_revenue": float(recognized_revenue),
                    "recognized_gross_profit": float(recognized_gross_profit),
                    "recognized_gross_margin_rate": (
                        float(recognized_gross_profit / recognized_revenue)
                        if recognized_revenue
                        else 0.0
                    ),
                    "receivable_amount": float(receivable_amount),
                    "collected_amount": float(collected_amount),
                    "outstanding_amount": float(outstanding_amount),
                    "overdue_amount": float(overdue_amount),
                    "targets": {},
                },
                anomalies_json=anomalies,
            )
        )

    sync_run.status = "completed"
    sync_run.atomic_activation_status = "activated"
    sync_run.activated_at = utc_now()
    sync_run.completed_at = sync_run.activated_at
    sync_run.records_read = sum(counts.values())
    sync_run.records_written = sum(counts.values())
    sync_run.domain_results_json = domain_results
    return {
        "status": "completed",
        "source_batch_id": batch_id,
        "record_counts": counts,
        "validation": validation,
        "domains": domain_results,
        "experience_weight_policy_version": policy.version,
    }


def v3_failure_payload(exc: Exception) -> tuple[str, str, dict[str, Any]]:
    if isinstance(exc, (OperatingDataV3Error, SourceContractError)):
        return exc.code, str(exc), getattr(exc, "details", {})
    return "operating_data_v3_failed", str(exc), {}
