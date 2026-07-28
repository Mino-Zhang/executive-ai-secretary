from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from psycopg import sql

from .config import Settings, get_settings
from .feishu import FeishuBitableClient, FeishuError
from .source_contract import (
    SOURCE_COLUMNS,
    SOURCE_TABLES,
    _upsert_rows,
    connect_source,
    require_valid_source_contract,
)
from .source_contract_v3 import (
    SOURCE_V3_SCHEMA,
    SOURCE_V3_SCHEMA_VERSION,
    write_source_v3_snapshot,
)


class FeishuLiveSourceError(FeishuError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        self.code = code
        self.details = details or {}
        super().__init__(message)


@dataclass(frozen=True)
class FieldBinding:
    key: str
    field_id: str
    field_name: str
    field_type: int
    required: bool = True


@dataclass(frozen=True)
class TableBinding:
    domain: str
    app_token: str
    table_id: str
    display_name: str
    fields: tuple[FieldBinding, ...]


@dataclass(frozen=True)
class FeishuLiveSnapshot:
    fetched_at: datetime
    opportunities: tuple[dict[str, Any], ...]
    deliveries: tuple[dict[str, Any], ...]
    collections: tuple[dict[str, Any], ...]
    content_sha256: str
    validation: dict[str, Any]

    @property
    def record_counts(self) -> dict[str, int]:
        return {
            "opportunities": len(self.opportunities),
            "deliveries": len(self.deliveries),
            "collections": len(self.collections),
        }


LIVE_SOURCE_SYSTEM = "simulated_feishu_live"
RELIABILITY_WEIGHTS = {"high": 20, "medium": 10, "low": 5, "高": 20, "中": 10, "低": 5}
ORGANIZATION_CODE_OVERRIDES = {
    "华东事业部": "east",
    "华南事业部": "south",
    "华北事业部": "north",
    "西南事业部": "west",
    "战略客户事业部": "strategic",
    "创新业务事业部": "innovation",
}


OPPORTUNITY_FIELDS = (
    FieldBinding("opportunity_id", "fldq9f0Yog", "商机ID", 1),
    FieldBinding("source_record_id", "fld1PfV5qr", "源记录ID", 1),
    FieldBinding("organization_name", "fld6pFQtlt", "事业部", 3),
    FieldBinding("opportunity_name", "fld22tBBBG", "商机名称", 1),
    FieldBinding("customer_name", "fld3copPwO", "客户名称", 4),
    FieldBinding("customer_value_level", "fld7pfiy3Z", "客户价值等级", 3),
    FieldBinding("sales_owner", "fld7BakpjX", "销售负责人", 1),
    FieldBinding("presales_owners", "fldVUZAffM", "售前负责人", 3),
    FieldBinding("reliability", "fld6kw7KbB", "商机靠谱度", 3),
    FieldBinding("stage", "fld6bX7h1s", "当前阶段", 3),
    FieldBinding("expected_amount", "fld222yzjH", "预估金额", 2),
    FieldBinding("signed_amount", "fld6Zqobaa", "签约金额", 2, required=False),
    FieldBinding("expected_close_date", "fld66ykJe2", "预计签单时间", 5),
    FieldBinding("products_services", "fld5e7wxPV", "涉及产品|服务", 1),
    FieldBinding("entered_date", "fld1b1jRDv", "进单时间", 5),
    FieldBinding("latest_progress", "fld34zVGf1", "最近进展", 1),
    FieldBinding("industry", "fld7AMC2As", "所处行业", 1),
    FieldBinding("is_archived", "fldaviJVez", "是否已同步归档", 3),
    FieldBinding("archived_date", "fld2O1UgMl", "归档时间", 5, required=False),
)

DELIVERY_FIELDS = (
    FieldBinding("project_id", "fld3NRxjnD", "项目ID", 1),
    FieldBinding("opportunity_id", "fld1RIkAQM", "商机ID", 1),
    FieldBinding("opportunity_name", "fld7bTam7W", "商机名称", 1),
    FieldBinding("customer_name", "fld45ZZSYS", "客户名称", 1),
    FieldBinding("organization_name", "fld6gkndcu", "事业部", 3),
    FieldBinding("project_name", "fldBZNbzGl", "项目名称", 1),
    FieldBinding("project_manager", "fldGAQmDPp", "项目经理", 3),
    FieldBinding("delivery_owner", "fld2b5FJmg", "交付负责人", 3),
    FieldBinding("status", "fld4yGEaLF", "项目状态", 3),
    FieldBinding("risk_level", "fld10OMIyU", "风险等级", 3),
    FieldBinding("contract_amount", "fld4ivJg6X", "合同金额", 2),
    FieldBinding("recognized_revenue", "fld7u9bvBr", "已确认收入", 2),
    FieldBinding("gross_margin_rate", "fld7wRVA2g", "毛利率", 2),
    FieldBinding("planned_start_date", "fld4ruYZUg", "计划开始日期", 5),
    FieldBinding("planned_end_date", "fld2wWP3S2", "计划结束日期", 5),
    FieldBinding("actual_start_date", "fld2HYUbUq", "实际开始日期", 5, required=False),
    FieldBinding("actual_end_date", "fld5iHiZe5", "实际结束日期", 5, required=False),
    FieldBinding("current_milestone", "fld1PcNyBj", "当前里程碑", 3),
    FieldBinding("completion_rate", "fld3nWM5dd", "完成进度", 2),
    FieldBinding("delay_days", "fld1Kow919", "延期天数", 2),
    FieldBinding("latest_progress", "fld6tKBCYU", "最近进展", 4),
    FieldBinding("source_updated_date", "fld70QsQzE", "数据更新时间", 5),
)

COLLECTION_FIELDS = (
    FieldBinding("collection_id", "fld7AFUP9F", "回款记录ID", 1),
    FieldBinding("project_id", "fldIA7o9zh", "项目ID", 1),
    FieldBinding("opportunity_id", "fld3WxOttp", "商机ID", 1),
    FieldBinding("customer_name", "fld42WPY27", "客户名称", 1),
    FieldBinding("organization_name", "fld6jyBIh4", "事业部", 3),
    FieldBinding("payment_type", "fld4cDMUE2", "款项类型", 3),
    FieldBinding("payment_milestone", "fld3Dters0", "付款节点", 3),
    FieldBinding("receivable_amount", "fld3GhYVPR", "应收金额", 2),
    FieldBinding("planned_collection_date", "fld3I3dm2U", "计划回款日期", 5),
    FieldBinding("actual_collection_date", "fld3RUGqVH", "实际回款日期", 5, required=False),
    FieldBinding("collected_amount", "fld36un60O", "已回款金额", 2),
    FieldBinding("outstanding_amount", "fld5E9R3qy", "未回款金额", 2),
    FieldBinding("status", "fld7DMZyY6", "回款状态", 3),
    FieldBinding("overdue_days", "fld7pebbS4", "逾期天数", 2),
    FieldBinding("aging_bucket", "fld1y1Uk0w", "账龄区间", 3),
    FieldBinding("invoice_status", "fld6H3zmcX", "开票状态", 3),
    FieldBinding("invoice_number", "fld5OFDsue", "发票号码", 1, required=False),
    FieldBinding("collection_owner", "fld3nakAJh", "回款责任人", 3),
    FieldBinding("latest_follow_up", "fld6p644E5", "最近跟进", 4),
    FieldBinding("source_updated_date", "fld3IfK0ZA", "数据更新时间", 5),
)

FIELD_COMPATIBILITY_WARNINGS = (
    {
        "domain": "opportunity",
        "field_name": "客户名称",
        "code": "multiselect_single_value_compatibility",
        "message": "客户名称当前为多选字段；运行时要求每条商机只有一个客户值。",
    },
    {
        "domain": "delivery",
        "field_name": "最近进展",
        "code": "multiselect_text_compatibility",
        "message": "项目最近进展当前为多选字段；运行时按原顺序合并，建议后续改为长文本。",
    },
    {
        "domain": "collection",
        "field_name": "最近跟进",
        "code": "multiselect_text_compatibility",
        "message": "回款最近跟进当前为多选字段；运行时按原顺序合并，建议后续改为长文本。",
    },
)


def fixed_bindings_from_settings(settings: Settings) -> tuple[TableBinding, ...]:
    values = (
        (
            "opportunity",
            settings.feishu_opportunity_app_token,
            settings.feishu_opportunity_table_id,
            "飞书导入_商机总览",
            OPPORTUNITY_FIELDS,
        ),
        (
            "delivery",
            settings.feishu_delivery_app_token,
            settings.feishu_delivery_table_id,
            "飞书导入_项目交付",
            DELIVERY_FIELDS,
        ),
        (
            "collection",
            settings.feishu_collection_app_token,
            settings.feishu_collection_table_id,
            "飞书导入_财务回款",
            COLLECTION_FIELDS,
        ),
    )
    missing = [domain for domain, app, table, _, _ in values if not app or not table]
    if missing:
        raise FeishuLiveSourceError(
            "feishu_binding_incomplete",
            "三张飞书多维表格必须同时配置 App Token 与 Table ID",
            {"missing_domains": missing},
        )
    return tuple(
        TableBinding(domain, str(app), str(table), display_name, fields)
        for domain, app, table, display_name, fields in values
    )


def _field_name_map(binding: TableBinding, actual_fields: list[dict[str, Any]]) -> dict[str, str]:
    by_id = {str(item.get("field_id")): item for item in actual_fields}
    missing: list[str] = []
    drift: list[dict[str, Any]] = []
    output: dict[str, str] = {}
    for expected in binding.fields:
        actual = by_id.get(expected.field_id)
        if actual is None:
            missing.append(expected.field_id)
            continue
        actual_name = str(actual.get("field_name") or "")
        actual_type = int(actual.get("type") or 0)
        # Field IDs are the stable contract. Administrators may rename a field
        # in Feishu without breaking synchronization; a type change remains a
        # hard schema drift because it changes normalization semantics.
        if actual_type != expected.field_type:
            drift.append(
                {
                    "field_id": expected.field_id,
                    "expected_name": expected.field_name,
                    "actual_name": actual_name,
                    "expected_type": expected.field_type,
                    "actual_type": actual_type,
                }
            )
        output[expected.key] = actual_name
    if missing or drift:
        raise FeishuLiveSourceError(
            "feishu_schema_drift",
            f"{binding.display_name} 字段契约已变化，已拒绝同步",
            {"domain": binding.domain, "missing_field_ids": missing, "drift": drift},
        )
    return output


def _normalize_text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, list):
        values = [str(item).strip() for item in value if str(item).strip()]
        return "、".join(values) or None
    return str(value).strip() or None


def _normalize_customer(value: Any) -> str | None:
    if isinstance(value, list):
        values = [str(item).strip() for item in value if str(item).strip()]
        if len(values) == 1:
            return values[0]
        # A Base import can split a single bilingual customer name on a comma
        # even when that comma is inside parentheses, for example
        # ``Orange（Singapore, APAC）``.  Reconstruct only when every list
        # boundary is demonstrably inside one balanced parenthetical phrase.
        # Genuine multi-customer selections remain invalid.
        if values and _all_boundaries_are_parenthetical(values):
            return "，".join(values)
        if len(values) != 1:
            raise FeishuLiveSourceError(
                "feishu_customer_cardinality_invalid",
                "商机客户名称必须且只能包含一个客户",
                {"values": values},
            )
    return _normalize_text(value)


def _all_boundaries_are_parenthetical(values: list[str]) -> bool:
    depth = 0
    saw_opening = False
    for index, value in enumerate(values):
        for character in value:
            if character in {"（", "("}:
                depth += 1
                saw_opening = True
            elif character in {"）", ")"}:
                depth -= 1
                if depth < 0:
                    return False
        if index < len(values) - 1 and depth <= 0:
            return False
    return saw_opening and depth == 0


def _normalize_decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise FeishuLiveSourceError("feishu_number_invalid", "飞书金额或数值字段格式无效") from exc


def _normalize_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value) / 1000, UTC).date()
    text = str(value).strip()
    if text.isdigit():
        return datetime.fromtimestamp(int(text) / 1000, UTC).date()
    try:
        return date.fromisoformat(text[:10])
    except ValueError as exc:
        raise FeishuLiveSourceError("feishu_date_invalid", "飞书日期字段格式无效") from exc


def _normalize_record(
    binding: TableBinding,
    field_names: dict[str, str],
    record: dict[str, Any],
) -> dict[str, Any]:
    fields = record.get("fields") or {}
    modified_time = int(record.get("last_modified_time") or 0)
    output: dict[str, Any] = {
        "source_native_record_id": str(record.get("record_id") or record.get("id") or ""),
        "source_modified_at": (
            datetime.fromtimestamp(modified_time / 1000, UTC)
            if modified_time
            else None
        ),
    }
    for expected in binding.fields:
        raw = fields.get(field_names[expected.key])
        if expected.key == "customer_name" and binding.domain == "opportunity":
            value = _normalize_customer(raw)
        elif expected.field_type == 2:
            value = _normalize_decimal(raw)
        elif expected.field_type == 5:
            value = _normalize_date(raw)
        else:
            value = _normalize_text(raw)
        if expected.required and value in (None, ""):
            raise FeishuLiveSourceError(
                "feishu_required_value_missing",
                f"{binding.display_name} 存在必填字段空值",
                {
                    "domain": binding.domain,
                    "record_id": output["source_native_record_id"],
                    "field_id": expected.field_id,
                    "field_name": expected.field_name,
                },
            )
        output[expected.key] = value
    return output


def _unique_index(
    rows: tuple[dict[str, Any], ...], key: str, label: str
) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for row in rows:
        value = str(row.get(key) or "")
        if not value or value in output:
            raise FeishuLiveSourceError(
                "feishu_business_id_invalid",
                f"{label}为空或重复",
                {"field": key, "value": value},
            )
        output[value] = row
    return output


def validate_live_snapshot(
    opportunities: tuple[dict[str, Any], ...],
    deliveries: tuple[dict[str, Any], ...],
    collections: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    opportunity_by_id = _unique_index(opportunities, "opportunity_id", "商机ID")
    delivery_by_id = _unique_index(deliveries, "project_id", "项目ID")
    _unique_index(collections, "collection_id", "回款记录ID")

    collection_count_by_project: dict[str, int] = {}
    receivable_by_project: dict[str, Decimal] = {}
    for project in deliveries:
        opportunity = opportunity_by_id.get(str(project["opportunity_id"]))
        if opportunity is None:
            raise FeishuLiveSourceError("feishu_relation_invalid", "项目关联了未知商机")
        if project["customer_name"] != opportunity["customer_name"]:
            raise FeishuLiveSourceError("feishu_relation_invalid", "项目与商机客户不一致")
        if project["organization_name"] != opportunity["organization_name"]:
            raise FeishuLiveSourceError("feishu_relation_invalid", "项目与商机事业部不一致")

    total_receivable = Decimal("0")
    total_collected = Decimal("0")
    total_outstanding = Decimal("0")
    for collection in collections:
        project_id = str(collection["project_id"])
        project = delivery_by_id.get(project_id)
        opportunity = opportunity_by_id.get(str(collection["opportunity_id"]))
        if project is None or opportunity is None:
            raise FeishuLiveSourceError("feishu_relation_invalid", "回款关联了未知项目或商机")
        if str(project["opportunity_id"]) != str(collection["opportunity_id"]):
            raise FeishuLiveSourceError("feishu_relation_invalid", "回款的项目与商机关系不一致")
        if collection["customer_name"] != project["customer_name"]:
            raise FeishuLiveSourceError("feishu_relation_invalid", "回款与项目客户不一致")
        if collection["organization_name"] != project["organization_name"]:
            raise FeishuLiveSourceError("feishu_relation_invalid", "回款与项目事业部不一致")
        receivable = collection["receivable_amount"] or Decimal("0")
        collected = collection["collected_amount"] or Decimal("0")
        outstanding = collection["outstanding_amount"] or Decimal("0")
        if receivable != collected + outstanding:
            raise FeishuLiveSourceError(
                "feishu_financial_invariant_failed", "应收金额必须等于已回款与未回款之和"
            )
        collection_count_by_project[project_id] = collection_count_by_project.get(project_id, 0) + 1
        receivable_by_project[project_id] = (
            receivable_by_project.get(project_id, Decimal("0")) + receivable
        )
        total_receivable += receivable
        total_collected += collected
        total_outstanding += outstanding

    for project_id, project in delivery_by_id.items():
        if collection_count_by_project.get(project_id) != 3:
            raise FeishuLiveSourceError(
                "feishu_payment_schedule_invalid", "每个项目必须包含三个回款节点"
            )
        if receivable_by_project.get(project_id) != project["contract_amount"]:
            raise FeishuLiveSourceError(
                "feishu_financial_invariant_failed", "项目合同金额与回款节点应收合计不一致"
            )
        opportunity = opportunity_by_id[str(project["opportunity_id"])]
        if opportunity["signed_amount"] != project["contract_amount"]:
            raise FeishuLiveSourceError(
                "feishu_financial_invariant_failed", "赢单商机签约金额与项目合同金额不一致"
            )

    if total_receivable != total_collected + total_outstanding:
        raise FeishuLiveSourceError(
            "feishu_financial_invariant_failed", "三表汇总金额不满足财务恒等关系"
        )
    return {
        "valid": True,
        "record_counts": {
            "opportunities": len(opportunities),
            "deliveries": len(deliveries),
            "collections": len(collections),
        },
        "totals": {
            "receivable_amount": str(total_receivable),
            "collected_amount": str(total_collected),
            "outstanding_amount": str(total_outstanding),
        },
    }


ClientFactory = Callable[[TableBinding], FeishuBitableClient]


def fetch_fixed_live_snapshot(
    bindings: tuple[TableBinding, ...],
    *,
    client_factory: ClientFactory,
) -> FeishuLiveSnapshot:
    rows_by_domain: dict[str, tuple[dict[str, Any], ...]] = {}
    business_id_by_domain = {
        "opportunity": "opportunity_id",
        "delivery": "project_id",
        "collection": "collection_id",
    }
    for binding in bindings:
        with client_factory(binding) as client:
            field_names = _field_name_map(binding, client.list_fields())
            normalized = tuple(
                _normalize_record(binding, field_names, record) for record in client.iter_records()
            )
            business_id = business_id_by_domain[binding.domain]
            # Feishu does not promise a stable page/record order.  Normalize
            # every domain before hashing so an unchanged full snapshot keeps
            # the same batch identity even when pagination order changes.
            rows_by_domain[binding.domain] = tuple(
                sorted(normalized, key=lambda row: str(row.get(business_id) or ""))
            )
    opportunities = rows_by_domain.get("opportunity", ())
    deliveries = rows_by_domain.get("delivery", ())
    collections = rows_by_domain.get("collection", ())
    validation = validate_live_snapshot(opportunities, deliveries, collections)
    validation["warnings"] = [dict(item) for item in FIELD_COMPATIBILITY_WARNINGS]
    # Hash only fields that can affect product facts. Feishu's automatic
    # last-modified timestamp is operational metadata; including it would
    # create a new batch when an unbound field changes or when the metadata is
    # unavailable and a connector falls back to its fetch time.
    canonical = {
        "opportunities": tuple(
            {key: value for key, value in row.items() if key != "source_modified_at"}
            for row in opportunities
        ),
        "deliveries": tuple(
            {key: value for key, value in row.items() if key != "source_modified_at"}
            for row in deliveries
        ),
        "collections": tuple(
            {key: value for key, value in row.items() if key != "source_modified_at"}
            for row in collections
        ),
    }
    content_sha256 = hashlib.sha256(
        json.dumps(canonical, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    return FeishuLiveSnapshot(
        fetched_at=datetime.now(UTC),
        opportunities=opportunities,
        deliveries=deliveries,
        collections=collections,
        content_sha256=content_sha256,
        validation=validation,
    )


def fetch_fixed_live_snapshot_from_settings(
    settings: Settings,
    *,
    app_secret: str,
) -> FeishuLiveSnapshot:
    if not settings.feishu_app_id or not app_secret:
        raise FeishuLiveSourceError(
            "feishu_credentials_missing", "飞书只读应用 App ID 与运行凭证必须完整配置"
        )
    bindings = fixed_bindings_from_settings(settings)

    def client_factory(binding: TableBinding) -> FeishuBitableClient:
        return FeishuBitableClient(
            app_id=str(settings.feishu_app_id),
            app_secret=app_secret,
            app_token=binding.app_token,
            table_id=binding.table_id,
        )

    return fetch_fixed_live_snapshot(bindings, client_factory=client_factory)


def _stable_source_id(prefix: str, *parts: object) -> str:
    payload = "\x1f".join(str(part).strip() for part in parts)
    return f"{prefix}-{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:24]}"


def _organization_code(name: object) -> str:
    text = str(name).strip()
    return ORGANIZATION_CODE_OVERRIDES.get(text) or _stable_source_id("division", text)


def _person_names(value: object) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    return tuple(
        part.strip() for part in str(value).replace("，", "、").split("、") if part.strip()
    )


def _source_timestamp(row: dict[str, Any], date_key: str | None = None) -> datetime:
    if date_key and isinstance(row.get(date_key), date):
        return datetime.combine(row[date_key], datetime.min.time(), tzinfo=UTC)
    value = row.get("source_modified_at")
    return value if isinstance(value, datetime) else datetime.now(UTC)


def _opportunity_status(stage: object) -> str:
    value = str(stage).strip()
    if value == "赢单":
        return "won"
    if value == "归档":
        return "archived"
    if value == "搁置":
        return "paused"
    return "open"


def _percent(value: Decimal | None, *, fraction: bool) -> Decimal | int:
    number = value or Decimal("0")
    if fraction:
        if number > 1:
            number /= Decimal("100")
        return max(Decimal("0"), min(Decimal("1"), number))
    if number <= 1:
        number *= Decimal("100")
    return max(0, min(100, int(number.to_integral_value())))


def live_snapshot_to_source_rows(
    snapshot: FeishuLiveSnapshot,
    *,
    batch_id: str,
) -> dict[str, list[dict[str, Any]]]:
    """Map the approved three-table Feishu contract to the sanitized ODS contract.

    Stable synthetic dimension identifiers are one-way hashes. They are not a
    reverse-identity mapping and contain no original customer or person name.
    """

    organizations: dict[str, dict[str, Any]] = {}
    people: dict[str, dict[str, Any]] = {}
    customers: dict[str, dict[str, Any]] = {}

    def common(source_id: str, updated_at: datetime) -> dict[str, Any]:
        return {
            "source_system": LIVE_SOURCE_SYSTEM,
            "source_record_id": source_id,
            "source_updated_at": updated_at,
            "load_batch_id": batch_id,
            "is_deleted": False,
        }

    def ensure_organization(name: object, updated_at: datetime) -> str:
        display_name = str(name).strip()
        code = _organization_code(display_name)
        organizations[code] = {
            **common(_stable_source_id("org", display_name), updated_at),
            "organization_code": code,
            "parent_organization_code": None,
            "display_name": display_name,
            "unit_type": "division",
            "sort_order": len(ORGANIZATION_CODE_OVERRIDES),
        }
        return code

    def ensure_person(name: str, org_code: str, role_title: str, updated_at: datetime) -> str:
        source_id = _stable_source_id("person", org_code, name)
        existing = people.get(source_id)
        roles = set(str(existing.get("role_title") or "").split("、")) if existing else set()
        roles.discard("")
        roles.add(role_title)
        people[source_id] = {
            **common(
                source_id,
                max(updated_at, existing["source_updated_at"]) if existing else updated_at,
            ),
            "organization_code": org_code,
            "display_name": name,
            "role_title": "、".join(sorted(roles)),
            "is_active": True,
        }
        return source_id

    def ensure_customer(
        name: object,
        org_code: str,
        owner_id: str | None,
        industry: object,
        updated_at: datetime,
        customer_since: date | None,
    ) -> str:
        display_name = str(name).strip()
        source_id = _stable_source_id("customer", org_code, display_name)
        customers[source_id] = {
            **common(source_id, updated_at),
            "organization_code": org_code,
            "owner_person_record_id": owner_id,
            "display_name": display_name,
            "industry": str(industry).strip() if industry else None,
            "region": None,
            "customer_since": customer_since,
        }
        return source_id

    opportunities: list[dict[str, Any]] = []
    opportunity_customer: dict[str, str] = {}
    for row in snapshot.opportunities:
        updated_at = _source_timestamp(row)
        org_code = ensure_organization(row["organization_name"], updated_at)
        sales_names = _person_names(row.get("sales_owner"))
        owner_id = (
            ensure_person(sales_names[0], org_code, "销售负责人", updated_at)
            if sales_names
            else None
        )
        for name in _person_names(row.get("presales_owners")):
            ensure_person(name, org_code, "售前负责人", updated_at)
        customer_id = ensure_customer(
            row["customer_name"],
            org_code,
            owner_id,
            row.get("industry"),
            updated_at,
            row.get("entered_date"),
        )
        opportunity_id = str(row["opportunity_id"])
        opportunity_customer[opportunity_id] = customer_id
        reliability = str(row.get("reliability") or "").strip().lower()
        probability = RELIABILITY_WEIGHTS.get(reliability)
        if probability is None:
            raise FeishuLiveSourceError(
                "feishu_reliability_invalid",
                "商机靠谱度只允许高、中、低",
                {"opportunity_id": opportunity_id, "value": row.get("reliability")},
            )
        opportunities.append(
            {
                **common(opportunity_id, updated_at),
                "organization_code": org_code,
                "customer_record_id": customer_id,
                "owner_person_record_id": owner_id,
                "opportunity_code": opportunity_id,
                "title": str(row["opportunity_name"]),
                "stage": str(row["stage"]),
                "status": _opportunity_status(row["stage"]),
                "probability": probability,
                "expected_amount": row["expected_amount"] or Decimal("0"),
                "expected_gross_profit": Decimal("0"),
                "created_date": row["entered_date"],
                "expected_close_date": row["expected_close_date"],
                "closed_date": row.get("archived_date")
                if row["stage"] in {"赢单", "归档"}
                else None,
            }
        )

    deliveries: list[dict[str, Any]] = []
    delivery_customer: dict[str, str] = {}
    for row in snapshot.deliveries:
        updated_at = _source_timestamp(row, "source_updated_date")
        org_code = ensure_organization(row["organization_name"], updated_at)
        customer_id = opportunity_customer[str(row["opportunity_id"])]
        delivery_customer[str(row["project_id"])] = customer_id
        manager_names = _person_names(row.get("project_manager"))
        manager_id = (
            ensure_person(manager_names[0], org_code, "项目经理", updated_at)
            if manager_names
            else None
        )
        for name in _person_names(row.get("delivery_owner")):
            ensure_person(name, org_code, "交付负责人", updated_at)
        status = str(row["status"])
        deliveries.append(
            {
                **common(str(row["project_id"]), updated_at),
                "organization_code": org_code,
                "opportunity_record_id": str(row["opportunity_id"]),
                "customer_record_id": customer_id,
                "manager_person_record_id": manager_id,
                "project_code": str(row["project_id"]),
                "project_name": str(row["project_name"]),
                "status": "delayed"
                if status == "延期关注"
                else "completed"
                if status == "已完成"
                else "pending"
                if status == "待启动"
                else "active",
                "risk_level": "normal" if row["risk_level"] == "正常" else str(row["risk_level"]),
                "completion_percent": _percent(row.get("completion_rate"), fraction=False),
                "contract_amount": row["contract_amount"] or Decimal("0"),
                "gross_margin_rate": _percent(row.get("gross_margin_rate"), fraction=True),
                "planned_start_date": row["planned_start_date"],
                "planned_end_date": row["planned_end_date"],
                "actual_end_date": row.get("actual_end_date"),
                "current_milestone": row.get("current_milestone"),
                "delay_days": int(row.get("delay_days") or 0),
            }
        )

    collections: list[dict[str, Any]] = []
    for row in snapshot.collections:
        updated_at = _source_timestamp(row, "source_updated_date")
        org_code = ensure_organization(row["organization_name"], updated_at)
        for name in _person_names(row.get("collection_owner")):
            ensure_person(name, org_code, "回款负责人", updated_at)
        receivable = row["receivable_amount"] or Decimal("0")
        collections.append(
            {
                **common(str(row["collection_id"]), updated_at),
                "organization_code": org_code,
                "project_record_id": str(row["project_id"]),
                "customer_record_id": delivery_customer[str(row["project_id"])],
                "invoice_amount": receivable,
                "receivable_amount": receivable,
                "collected_amount": row["collected_amount"] or Decimal("0"),
                "planned_collection_date": row["planned_collection_date"],
                "actual_collection_date": row.get("actual_collection_date"),
                "overdue_days": int(row.get("overdue_days") or 0),
                "aging_bucket": str(row["aging_bucket"]),
                "status": str(row["status"]),
            }
        )

    return {
        "organizations": list(organizations.values()),
        "people": list(people.values()),
        "customers": list(customers.values()),
        "opportunities": opportunities,
        "deliveries": deliveries,
        "collections": collections,
        "targets": [],
    }


def write_live_snapshot_to_source(
    snapshot: FeishuLiveSnapshot,
    *,
    source_writer_database_url: str,
    dataset_version: str,
    schema: str = SOURCE_V3_SCHEMA,
    schema_version: str | None = None,
    bindings: tuple[TableBinding, ...] | None = None,
) -> dict[str, Any]:
    """Atomically publish one validated three-table snapshot to the source DB."""

    if not snapshot.validation.get("valid"):
        raise FeishuLiveSourceError("feishu_snapshot_invalid", "三表校验未通过")
    if schema_version == SOURCE_V3_SCHEMA_VERSION or (
        schema_version is None and schema == SOURCE_V3_SCHEMA
    ):
        if not bindings:
            raise FeishuLiveSourceError(
                "feishu_bindings_missing",
                "ODS 3.0 写入必须同时保存三张表的稳定字段绑定",
            )
        return write_source_v3_snapshot(
            snapshot,
            bindings,
            source_writer_database_url=source_writer_database_url,
            dataset_version=dataset_version,
            schema=schema,
        )

    # Compatibility path for the frozen 2.0 demo source. Production and the
    # customer template use the immutable ODS 3.0 branch above.
    batch_id = f"feishu-live-{snapshot.content_sha256[:24]}"
    rows_by_domain = live_snapshot_to_source_rows(snapshot, batch_id=batch_id)
    record_counts = {domain: len(rows) for domain, rows in rows_by_domain.items()}
    reference_date = max(
        (
            row.get("source_updated_date")
            for rows in (snapshot.deliveries, snapshot.collections)
            for row in rows
            if isinstance(row.get("source_updated_date"), date)
        ),
        default=snapshot.fetched_at.date(),
    )
    with connect_source(
        source_writer_database_url,
        application_name="executive-ai-feishu-three-table-importer",
        read_only=False,
    ) as connection:
        require_valid_source_contract(connection, schema=schema, require_read_only=False)
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        INSERT INTO {}.source_batches (
                            batch_id, source_system, dataset_version, reference_date,
                            source_data_as_of, status, record_counts, content_sha256,
                            validation_result, completed_at
                        ) VALUES (
                            %s, %s, %s, %s, %s, 'ready', %s::jsonb, %s, %s::jsonb, now()
                        ) ON CONFLICT (batch_id) DO UPDATE SET
                            source_data_as_of = EXCLUDED.source_data_as_of,
                            status = 'ready', record_counts = EXCLUDED.record_counts,
                            content_sha256 = EXCLUDED.content_sha256,
                            validation_result = EXCLUDED.validation_result,
                            completed_at = now()
                        """
                    ).format(sql.Identifier(schema)),
                    (
                        batch_id,
                        LIVE_SOURCE_SYSTEM,
                        dataset_version,
                        reference_date,
                        snapshot.fetched_at,
                        json.dumps(record_counts, ensure_ascii=False),
                        snapshot.content_sha256,
                        json.dumps(snapshot.validation, ensure_ascii=False),
                    ),
                )
                for table in SOURCE_TABLES.values():
                    cursor.execute(
                        sql.SQL(
                            "UPDATE {}.{} SET is_deleted = true WHERE source_system <> %s"
                        ).format(sql.Identifier(schema), sql.Identifier(table)),
                        (LIVE_SOURCE_SYSTEM,),
                    )
            for domain, rows in rows_by_domain.items():
                _upsert_rows(
                    connection,
                    schema=schema,
                    table=SOURCE_TABLES[domain],
                    columns=SOURCE_COLUMNS[domain],
                    rows=rows,
                )
                active_ids = [str(row["source_record_id"]) for row in rows]
                with connection.cursor() as cursor:
                    if active_ids:
                        cursor.execute(
                            sql.SQL(
                                "UPDATE {}.{} SET is_deleted = true "
                                "WHERE source_system = %s AND NOT (source_record_id = ANY(%s))"
                            ).format(sql.Identifier(schema), sql.Identifier(SOURCE_TABLES[domain])),
                            (LIVE_SOURCE_SYSTEM, active_ids),
                        )
                    else:
                        cursor.execute(
                            sql.SQL(
                                "UPDATE {}.{} SET is_deleted = true WHERE source_system = %s"
                            ).format(sql.Identifier(schema), sql.Identifier(SOURCE_TABLES[domain])),
                            (LIVE_SOURCE_SYSTEM,),
                        )
    return {
        "batch_id": batch_id,
        "dataset_version": dataset_version,
        "source_data_as_of": snapshot.fetched_at.isoformat(),
        "record_counts": record_counts,
        "content_sha256": snapshot.content_sha256,
        "validation": snapshot.validation,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate the fixed three-table Feishu source")
    parser.add_argument("action", choices=("validate",))
    parser.parse_args()
    settings = get_settings()
    secret = (
        settings.feishu_runtime_secret.get_secret_value() if settings.feishu_runtime_secret else ""
    )
    snapshot = fetch_fixed_live_snapshot_from_settings(settings, app_secret=secret)
    print(
        json.dumps(
            {
                "valid": True,
                "fetched_at": snapshot.fetched_at.isoformat(),
                "record_counts": snapshot.record_counts,
                "content_sha256": snapshot.content_sha256,
                "validation": snapshot.validation,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
