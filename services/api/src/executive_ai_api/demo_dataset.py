from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from zoneinfo import ZoneInfo

DEMO_SOURCE_SCHEMA_VERSION = "2.0"
DEFAULT_DATASET_VERSION = "phase2-demo-v1"
DEFAULT_REFERENCE_DATE = date(2026, 7, 26)

DIVISIONS = (
    ("east", "华东事业部"),
    ("south", "华南事业部"),
    ("north", "华北事业部"),
    ("west", "西南事业部"),
    ("strategic", "战略客户事业部"),
    ("innovation", "创新业务事业部"),
)
INDUSTRIES = ("先进制造", "能源", "医药健康", "零售消费", "交通物流", "金融服务")
REGIONS = ("华东", "华南", "华北", "西南", "全国", "海外")
CUSTOMER_PREFIXES = (
    "远川",
    "云海",
    "北陆",
    "星瀚",
    "澄明",
    "嘉衡",
    "启岳",
    "融川",
    "观澜",
    "新岚",
)
CUSTOMER_SUFFIXES = ("智造", "能源", "医药", "供应链", "科技", "实业")
MILESTONES = ("项目启动", "方案确认", "开发联调", "客户验收", "上线移交")
MONEY = Decimal("0.01")


def _json_default(value: object) -> str:
    if isinstance(value, (date, datetime, Decimal)):
        return str(value)
    raise TypeError(f"unsupported deterministic JSON value: {type(value).__name__}")


class StableValues:
    def __init__(self, dataset_version: str, enterprise_id: str, reference_date: date) -> None:
        self.seed = f"{dataset_version}|{enterprise_id}|{reference_date.isoformat()}"

    def integer(self, namespace: str, object_id: int | str, minimum: int, maximum: int) -> int:
        if maximum < minimum:
            raise ValueError("maximum must be greater than or equal to minimum")
        payload = f"{self.seed}|{namespace}|{object_id}".encode()
        value = int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")
        return minimum + value % (maximum - minimum + 1)

    def choice(self, namespace: str, object_id: int | str, values: tuple[Any, ...]) -> Any:
        return values[self.integer(namespace, object_id, 0, len(values) - 1)]


@dataclass(frozen=True)
class DemoDataset:
    schema_version: str
    dataset_version: str
    enterprise_id: str
    reference_date: date
    seed: str
    batch_id: str
    source_data_as_of: datetime
    organization_units: list[dict[str, Any]]
    people: list[dict[str, Any]]
    customers: list[dict[str, Any]]
    opportunities: list[dict[str, Any]]
    deliveries: list[dict[str, Any]]
    collections: list[dict[str, Any]]
    targets: list[dict[str, Any]]
    validation: dict[str, Any]
    content_sha256: str

    @property
    def record_counts(self) -> dict[str, int]:
        return {
            "organization_units": len(self.organization_units),
            "people": len(self.people),
            "customers": len(self.customers),
            "opportunities": len(self.opportunities),
            "deliveries": len(self.deliveries),
            "collections": len(self.collections),
            "targets": len(self.targets),
        }

    def manifest(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "dataset_version": self.dataset_version,
            "enterprise_id": self.enterprise_id,
            "reference_date": self.reference_date.isoformat(),
            "seed": self.seed,
            "batch_id": self.batch_id,
            "source_data_as_of": self.source_data_as_of.isoformat(),
            "record_counts": self.record_counts,
            "content_sha256": self.content_sha256,
            "validation": self.validation,
        }


def _common(
    *,
    source_system: str,
    source_record_id: str,
    source_updated_at: datetime,
    batch_id: str,
) -> dict[str, Any]:
    return {
        "source_system": source_system,
        "source_record_id": source_record_id,
        "source_updated_at": source_updated_at,
        "load_batch_id": batch_id,
        "is_deleted": False,
    }


def _month_start(reference_date: date, months_ago: int) -> date:
    month_index = reference_date.year * 12 + reference_date.month - 1 - months_ago
    return date(month_index // 12, month_index % 12 + 1, 1)


def _month_end(month_start: date) -> date:
    next_month = _month_start(month_start, -1)
    return next_month - timedelta(days=1)


def _aging_bucket(days: int) -> str:
    if days <= 0:
        return "未逾期"
    if days <= 30:
        return "1-30天"
    if days <= 60:
        return "31-60天"
    if days <= 90:
        return "61-90天"
    return "90天以上"


def _money(value: Decimal | float | int | str) -> Decimal:
    return Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)


def build_demo_dataset(
    *,
    enterprise_id: str,
    dataset_version: str = DEFAULT_DATASET_VERSION,
    reference_date: date = DEFAULT_REFERENCE_DATE,
) -> DemoDataset:
    values = StableValues(dataset_version, enterprise_id, reference_date)
    source_data_as_of = datetime.combine(
        reference_date,
        time(hour=2),
        tzinfo=ZoneInfo("Asia/Shanghai"),
    ).astimezone(UTC)
    batch_seed = hashlib.sha256(values.seed.encode()).hexdigest()[:16]
    batch_id = f"demo-{dataset_version}-{reference_date.isoformat()}-{batch_seed}"

    organization_units: list[dict[str, Any]] = []
    for index, (code, name) in enumerate(DIVISIONS, start=1):
        organization_units.append(
            {
                **_common(
                    source_system="simulated_generator",
                    source_record_id=f"org-{code}",
                    source_updated_at=source_data_as_of,
                    batch_id=batch_id,
                ),
                "organization_code": code,
                "parent_organization_code": None,
                "display_name": name,
                "unit_type": "division",
                "sort_order": index * 10,
            }
        )

    people: list[dict[str, Any]] = []
    for index in range(1, 46):
        organization_code = DIVISIONS[(index - 1) % len(DIVISIONS)][0]
        people.append(
            {
                **_common(
                    source_system="simulated_generator",
                    source_record_id=f"person-{index:03d}",
                    source_updated_at=source_data_as_of
                    - timedelta(days=values.integer("person-updated", index, 0, 14)),
                    batch_id=batch_id,
                ),
                "organization_code": organization_code,
                "display_name": f"业务负责人 {index:02d}",
                "role_title": "事业部负责人" if index <= 6 else "客户与项目负责人",
                "is_active": index % 19 != 0,
            }
        )

    people_by_org: dict[str, list[dict[str, Any]]] = {code: [] for code, _ in DIVISIONS}
    for person in people:
        people_by_org[person["organization_code"]].append(person)

    customers: list[dict[str, Any]] = []
    for index in range(1, 601):
        organization_code = DIVISIONS[(index - 1) % len(DIVISIONS)][0]
        owners = people_by_org[organization_code]
        owner = owners[values.integer("customer-owner", index, 0, len(owners) - 1)]
        prefix = CUSTOMER_PREFIXES[(index - 1) % len(CUSTOMER_PREFIXES)]
        suffix = CUSTOMER_SUFFIXES[values.integer("customer-suffix", index, 0, 5)]
        customers.append(
            {
                **_common(
                    source_system="simulated_generator",
                    source_record_id=f"customer-{index:04d}",
                    source_updated_at=source_data_as_of
                    - timedelta(days=values.integer("customer-updated", index, 0, 30)),
                    batch_id=batch_id,
                ),
                "organization_code": organization_code,
                "owner_person_record_id": owner["source_record_id"],
                "display_name": f"客户·{prefix}{suffix}·{index:03d}",
                "industry": INDUSTRIES[(index - 1) % len(INDUSTRIES)],
                "region": REGIONS[(index - 1) % len(REGIONS)],
                "customer_since": reference_date
                - timedelta(days=values.integer("customer-since", index, 180, 2400)),
            }
        )

    customers_by_org: dict[str, list[dict[str, Any]]] = {code: [] for code, _ in DIVISIONS}
    for customer in customers:
        customers_by_org[customer["organization_code"]].append(customer)

    opportunities: list[dict[str, Any]] = []
    probability_by_stage = {
        "线索确认": 10,
        "需求澄清": 25,
        "方案交流": 45,
        "商务提案": 65,
        "合同谈判": 85,
        "赢单": 100,
        "输单": 0,
        "暂停": 20,
    }
    active_stages = ("线索确认", "需求澄清", "方案交流", "商务提案", "合同谈判")
    for index in range(1, 3001):
        organization_code = DIVISIONS[(index - 1) % len(DIVISIONS)][0]
        customer_pool = customers_by_org[organization_code]
        customer = customer_pool[values.integer("opportunity-customer", index, 0, 99)]
        owner_pool = people_by_org[organization_code]
        owner = owner_pool[values.integer("opportunity-owner", index, 0, len(owner_pool) - 1)]
        cycle = (index - 1) % 20
        if cycle < 7:
            stage, status = "赢单", "won"
        elif cycle < 10:
            stage, status = "输单", "lost"
        elif cycle == 10:
            stage, status = "暂停", "paused"
        else:
            stage = active_stages[(cycle - 11) % len(active_stages)]
            status = "active"
        created_date = reference_date - timedelta(
            days=values.integer("opportunity-created", index, 20, 730)
        )
        if status in {"won", "lost"}:
            closed_date = min(
                reference_date - timedelta(days=values.integer("closed-recency", index, 1, 540)),
                created_date + timedelta(days=values.integer("sales-cycle", index, 25, 180)),
            )
            if closed_date < created_date:
                closed_date = created_date + timedelta(days=15)
            expected_close_date = closed_date
        else:
            closed_date = None
            expected_close_date = reference_date + timedelta(
                days=values.integer("expected-close", index, -30, 150)
            )
        amount = _money(values.integer("opportunity-amount", index, 30, 1500) * 10_000)
        margin_rate = Decimal(values.integer("opportunity-margin", index, 18, 42)) / 100
        opportunities.append(
            {
                **_common(
                    source_system="simulated_feishu",
                    source_record_id=f"opportunity-{index:05d}",
                    source_updated_at=source_data_as_of
                    - timedelta(days=values.integer("opportunity-updated", index, 0, 20)),
                    batch_id=batch_id,
                ),
                "organization_code": organization_code,
                "customer_record_id": customer["source_record_id"],
                "owner_person_record_id": owner["source_record_id"],
                "opportunity_code": f"SA-{reference_date.year}-{index:05d}",
                "title": f"{customer['display_name']} 数字化项目",
                "stage": stage,
                "status": status,
                "probability": probability_by_stage[stage],
                "expected_amount": amount,
                "expected_gross_profit": _money(amount * margin_rate),
                "created_date": created_date,
                "expected_close_date": expected_close_date,
                "closed_date": closed_date,
            }
        )

    won_opportunities = [item for item in opportunities if item["status"] == "won"]
    deliveries: list[dict[str, Any]] = []
    for index, opportunity in enumerate(won_opportunities[:800], start=1):
        closed_date = opportunity["closed_date"]
        if not isinstance(closed_date, date):
            raise AssertionError("won opportunity must have a closed date")
        planned_start = closed_date + timedelta(
            days=values.integer("project-kickoff", index, 5, 30)
        )
        planned_end = planned_start + timedelta(
            days=values.integer("project-duration", index, 90, 420)
        )
        elapsed = (reference_date - planned_start).days
        duration = max(1, (planned_end - planned_start).days)
        raw_progress = max(0, min(100, int(elapsed / duration * 100)))
        cycle = (index - 1) % 20
        if planned_end < reference_date and cycle < 6:
            status = "completed"
            risk_level = "normal"
            actual_end = planned_end + timedelta(days=values.integer("actual-end", index, -10, 10))
            completion = 100
            delay_days = max(0, (actual_end - planned_end).days)
        elif planned_end < reference_date or cycle in {6, 7}:
            status = "delayed"
            risk_level = "attention" if cycle % 2 else "high"
            actual_end = None
            completion = min(
                96, max(35, raw_progress - values.integer("delay-progress", index, 5, 22))
            )
            delay_days = max(1, (reference_date - planned_end).days)
        else:
            status = "in_progress" if planned_start <= reference_date else "planned"
            risk_level = "attention" if cycle in {8, 9, 10} else "normal"
            actual_end = None
            completion = raw_progress if status == "in_progress" else 0
            delay_days = 0
        amount = opportunity["expected_amount"]
        margin_rate = Decimal(values.integer("delivery-margin", index, 17, 38)) / 100
        manager_pool = people_by_org[opportunity["organization_code"]]
        manager = manager_pool[values.integer("delivery-manager", index, 0, len(manager_pool) - 1)]
        milestone_index = min(len(MILESTONES) - 1, completion * len(MILESTONES) // 101)
        deliveries.append(
            {
                **_common(
                    source_system="simulated_generator",
                    source_record_id=f"delivery-{index:04d}",
                    source_updated_at=source_data_as_of
                    - timedelta(days=values.integer("delivery-updated", index, 0, 12)),
                    batch_id=batch_id,
                ),
                "organization_code": opportunity["organization_code"],
                "opportunity_record_id": opportunity["source_record_id"],
                "customer_record_id": opportunity["customer_record_id"],
                "manager_person_record_id": manager["source_record_id"],
                "project_code": f"PRJ-{reference_date.year}-{index:04d}",
                "project_name": opportunity["title"].replace("数字化项目", "交付项目"),
                "status": status,
                "risk_level": risk_level,
                "completion_percent": completion,
                "contract_amount": amount,
                "gross_margin_rate": margin_rate,
                "planned_start_date": planned_start,
                "planned_end_date": planned_end,
                "actual_end_date": actual_end,
                "current_milestone": MILESTONES[milestone_index],
                "delay_days": delay_days,
            }
        )

    collections: list[dict[str, Any]] = []
    for project_index, project in enumerate(deliveries, start=1):
        contract_cents = int(Decimal(project["contract_amount"]) * 100)
        base_cents, remainder = divmod(contract_cents, 15)
        duration = max(1, (project["planned_end_date"] - project["planned_start_date"]).days)
        for installment in range(1, 16):
            record_index = (project_index - 1) * 15 + installment
            cents = base_cents + (remainder if installment == 15 else 0)
            receivable = Decimal(cents) / 100
            planned_date = project["planned_start_date"] + timedelta(
                days=round(duration * installment / 15)
            )
            payment_pattern = values.integer("collection-pattern", record_index, 0, 99)
            if planned_date > reference_date:
                collected = Decimal("0")
                actual_date = None
                status = "planned"
            elif payment_pattern < 72:
                collected = receivable
                actual_date = planned_date + timedelta(
                    days=values.integer("collection-actual", record_index, -8, 12)
                )
                status = "collected"
            elif payment_pattern < 84:
                collected = _money(receivable * Decimal("0.60"))
                actual_date = planned_date + timedelta(
                    days=values.integer("collection-partial", record_index, 0, 20)
                )
                status = "partial"
            else:
                collected = Decimal("0")
                actual_date = None
                status = "overdue"
            outstanding = _money(receivable - collected)
            overdue_days = (
                max(0, (reference_date - planned_date).days)
                if outstanding > 0 and planned_date < reference_date
                else 0
            )
            if status == "planned" and overdue_days > 0:
                status = "overdue"
            collections.append(
                {
                    **_common(
                        source_system="simulated_generator",
                        source_record_id=f"collection-{record_index:06d}",
                        source_updated_at=source_data_as_of
                        - timedelta(days=values.integer("collection-updated", record_index, 0, 10)),
                        batch_id=batch_id,
                    ),
                    "organization_code": project["organization_code"],
                    "project_record_id": project["source_record_id"],
                    "customer_record_id": project["customer_record_id"],
                    "invoice_amount": receivable,
                    "receivable_amount": receivable,
                    "collected_amount": collected,
                    "planned_collection_date": planned_date,
                    "actual_collection_date": actual_date,
                    "overdue_days": overdue_days,
                    "aging_bucket": _aging_bucket(overdue_days),
                    "status": status,
                }
            )

    targets: list[dict[str, Any]] = []
    metric_specs = (
        ("signed_revenue", "签约收入", "元", 9_000_000),
        ("collection", "回款", "元", 7_500_000),
        ("gross_profit", "毛利", "元", 2_600_000),
        ("weighted_pipeline", "加权商机", "元", 18_000_000),
    )
    target_index = 0
    for org_index, (organization_code, _) in enumerate(DIVISIONS, start=1):
        for months_ago in range(23, -1, -1):
            period_start = _month_start(reference_date, months_ago)
            period_end = _month_end(period_start)
            for metric_code, metric_name, unit, base_value in metric_specs:
                target_index += 1
                seasonality = Decimal("0.82") + Decimal(period_start.month % 4) * Decimal("0.07")
                growth = Decimal("1") + Decimal(23 - months_ago) * Decimal("0.006")
                org_factor = Decimal("0.78") + Decimal(org_index) * Decimal("0.07")
                target_value = _money(Decimal(base_value) * seasonality * growth * org_factor)
                targets.append(
                    {
                        **_common(
                            source_system="simulated_generator",
                            source_record_id=f"target-{target_index:04d}",
                            source_updated_at=source_data_as_of,
                            batch_id=batch_id,
                        ),
                        "organization_code": organization_code,
                        "metric_code": metric_code,
                        "metric_name": metric_name,
                        "period_type": "month",
                        "period_start": period_start,
                        "period_end": period_end,
                        "target_value": target_value,
                        "unit": unit,
                    }
                )
        for quarter_offset in range(4):
            target_index += 1
            quarter_month_index = (
                reference_date.year * 12 + reference_date.month - 1 - quarter_offset * 3
            )
            quarter_start_month = (quarter_month_index // 3) * 3
            period_start = date(quarter_start_month // 12, quarter_start_month % 12 + 1, 1)
            period_end = _month_end(_month_start(period_start, -2))
            targets.append(
                {
                    **_common(
                        source_system="simulated_generator",
                        source_record_id=f"target-{target_index:04d}",
                        source_updated_at=source_data_as_of,
                        batch_id=batch_id,
                    ),
                    "organization_code": organization_code,
                    "metric_code": "quarterly_revenue",
                    "metric_name": "季度签约收入",
                    "period_type": "quarter",
                    "period_start": period_start,
                    "period_end": period_end,
                    "target_value": _money(Decimal(32_000_000 + org_index * 1_700_000)),
                    "unit": "元",
                }
            )

    validation = validate_demo_dataset(
        organization_units=organization_units,
        people=people,
        customers=customers,
        opportunities=opportunities,
        deliveries=deliveries,
        collections=collections,
        targets=targets,
    )
    normalized = json.dumps(
        {
            "organization_units": organization_units,
            "people": people,
            "customers": customers,
            "opportunities": opportunities,
            "deliveries": deliveries,
            "collections": collections,
            "targets": targets,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    ).encode()
    content_sha256 = hashlib.sha256(normalized).hexdigest()
    return DemoDataset(
        schema_version=DEMO_SOURCE_SCHEMA_VERSION,
        dataset_version=dataset_version,
        enterprise_id=enterprise_id,
        reference_date=reference_date,
        seed=values.seed,
        batch_id=batch_id,
        source_data_as_of=source_data_as_of,
        organization_units=organization_units,
        people=people,
        customers=customers,
        opportunities=opportunities,
        deliveries=deliveries,
        collections=collections,
        targets=targets,
        validation=validation,
        content_sha256=content_sha256,
    )


def validate_demo_dataset(
    *,
    organization_units: list[dict[str, Any]],
    people: list[dict[str, Any]],
    customers: list[dict[str, Any]],
    opportunities: list[dict[str, Any]],
    deliveries: list[dict[str, Any]],
    collections: list[dict[str, Any]],
    targets: list[dict[str, Any]],
) -> dict[str, Any]:
    expected_counts = {
        "organization_units": 6,
        "people": 45,
        "customers": 600,
        "opportunities": 3000,
        "deliveries": 800,
        "collections": 12000,
        "targets": 600,
    }
    actual_counts = {
        "organization_units": len(organization_units),
        "people": len(people),
        "customers": len(customers),
        "opportunities": len(opportunities),
        "deliveries": len(deliveries),
        "collections": len(collections),
        "targets": len(targets),
    }
    errors: list[str] = []
    if actual_counts != expected_counts:
        errors.append(f"unexpected record counts: {actual_counts}")
    org_codes = {row["organization_code"] for row in organization_units}
    person_ids = {row["source_record_id"] for row in people}
    customer_ids = {row["source_record_id"] for row in customers}
    opportunity_ids = {row["source_record_id"] for row in opportunities}
    won_opportunity_ids = {
        row["source_record_id"] for row in opportunities if row["status"] == "won"
    }
    delivery_ids = {row["source_record_id"] for row in deliveries}
    for row in people:
        if row["organization_code"] not in org_codes:
            errors.append(f"person has unknown organization: {row['source_record_id']}")
    for row in customers:
        if row["organization_code"] not in org_codes:
            errors.append(f"customer has unknown organization: {row['source_record_id']}")
        if row["owner_person_record_id"] not in person_ids:
            errors.append(f"customer has unknown owner: {row['source_record_id']}")
    for row in opportunities:
        if row["customer_record_id"] not in customer_ids:
            errors.append(f"opportunity has unknown customer: {row['source_record_id']}")
        if row["owner_person_record_id"] not in person_ids:
            errors.append(f"opportunity has unknown owner: {row['source_record_id']}")
        if row["expected_gross_profit"] > row["expected_amount"]:
            errors.append(f"opportunity gross profit exceeds amount: {row['source_record_id']}")
    for row in deliveries:
        if row["opportunity_record_id"] not in won_opportunity_ids:
            errors.append(f"delivery is not linked to a won opportunity: {row['source_record_id']}")
        if row["customer_record_id"] not in customer_ids:
            errors.append(f"delivery has unknown customer: {row['source_record_id']}")
    for row in collections:
        if row["project_record_id"] not in delivery_ids:
            errors.append(f"collection has unknown project: {row['source_record_id']}")
        if row["collected_amount"] > row["receivable_amount"]:
            errors.append(f"collection exceeds receivable: {row['source_record_id']}")
        if row["receivable_amount"] > row["invoice_amount"]:
            errors.append(f"receivable exceeds invoice: {row['source_record_id']}")
    for row in targets:
        if row["organization_code"] not in org_codes:
            errors.append(f"target has unknown organization: {row['source_record_id']}")
        if row["period_end"] < row["period_start"]:
            errors.append(f"target has invalid period: {row['source_record_id']}")
    if len(opportunity_ids) != len(opportunities):
        errors.append("duplicate opportunity source_record_id")
    return {
        "valid": not errors,
        "errors": errors[:100],
        "checks": {
            "counts": actual_counts == expected_counts,
            "referential_integrity": not any("unknown" in item for item in errors),
            "financial_invariants": not any("exceeds" in item for item in errors),
            "date_invariants": not any("invalid period" in item for item in errors),
        },
    }
