from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .capabilities import CapabilityClaims, CapabilityError
from .config import get_settings
from .data_freshness import effective_domain_status
from .mcp_registry import effective_tool
from .models import (
    DailySnapshot,
    DataDomainStatus,
    DimCustomer,
    FactDelivery,
    FactFinanceCollection,
    FactOpportunity,
    FactTarget,
    OrganizationUnit,
)


def _number(value: Any) -> float:
    return float(value or 0)


_REVENUE_TARGET_CODES = {"revenue", "signed_revenue", "quarterly_revenue"}


def _period_filters(arguments: dict[str, Any], column: Any) -> list[Any]:
    filters: list[Any] = []
    for key, operator in (("period_start", "start"), ("period_end", "end")):
        raw = arguments.get(key)
        if raw is None or raw == "":
            continue
        try:
            value = date.fromisoformat(str(raw))
        except ValueError as exc:
            raise CapabilityError(f"{key} is malformed") from exc
        filters.append(column >= value if operator == "start" else column <= value)
    return filters


def _list_argument(arguments: dict[str, Any], key: str) -> list[str]:
    value = arguments.get(key)
    if value is None or value == "":
        return []
    if not isinstance(value, list):
        raise CapabilityError(f"{key} is malformed")
    return [str(item).strip() for item in value[:20] if str(item).strip()]


def _target_actual(
    db: Session,
    claims: CapabilityClaims,
    organization_ids: set[uuid.UUID],
    *,
    metric_code: str,
    period_start: date,
    period_end: date,
) -> tuple[float | None, str]:
    """Return a period- and scope-matched actual for a standard target metric."""

    if metric_code == "collection":
        value = db.scalar(
            select(func.sum(FactFinanceCollection.collected_amount)).where(
                FactFinanceCollection.enterprise_id == claims.enterprise_id,
                FactFinanceCollection.organization_unit_id.in_(organization_ids),
                FactFinanceCollection.is_current.is_(True),
                FactFinanceCollection.actual_collection_date >= period_start,
                FactFinanceCollection.actual_collection_date <= period_end,
            )
        )
        return _number(value), "sum(collected_amount) by actual_collection_date in target period"

    if metric_code in _REVENUE_TARGET_CODES:
        value = db.scalar(
            select(func.sum(FactOpportunity.expected_amount)).where(
                FactOpportunity.enterprise_id == claims.enterprise_id,
                FactOpportunity.organization_unit_id.in_(organization_ids),
                FactOpportunity.is_current.is_(True),
                FactOpportunity.status == "won",
                FactOpportunity.closed_date >= period_start,
                FactOpportunity.closed_date <= period_end,
            )
        )
        return _number(value), "sum(won expected_amount) by closed_date in target period"

    if metric_code == "gross_profit":
        value = db.scalar(
            select(func.sum(FactOpportunity.expected_gross_profit)).where(
                FactOpportunity.enterprise_id == claims.enterprise_id,
                FactOpportunity.organization_unit_id.in_(organization_ids),
                FactOpportunity.is_current.is_(True),
                FactOpportunity.status == "won",
                FactOpportunity.closed_date >= period_start,
                FactOpportunity.closed_date <= period_end,
            )
        )
        return _number(value), "sum(won expected_gross_profit) by closed_date in target period"

    if metric_code == "weighted_pipeline":
        value = db.scalar(
            select(
                func.sum(FactOpportunity.expected_amount * FactOpportunity.probability / 100)
            ).where(
                FactOpportunity.enterprise_id == claims.enterprise_id,
                FactOpportunity.organization_unit_id.in_(organization_ids),
                FactOpportunity.is_current.is_(True),
                FactOpportunity.status.in_(["active", "stalled"]),
                FactOpportunity.expected_close_date >= period_start,
                FactOpportunity.expected_close_date <= period_end,
            )
        )
        return _number(value), (
            "sum(expected_amount * probability / 100) for active opportunities "
            "by expected_close_date in target period"
        )

    return None, "no standard actual definition for this metric"


def _aggregate_evidence(
    *,
    domain: str,
    metrics: list[tuple[str, str]],
    organization_ids: set[uuid.UUID],
    grouping: str | None = None,
) -> list[dict[str, Any]]:
    return [
        {
            "domain": domain,
            "metric": metric,
            "calculation": calculation,
            "grouping": grouping,
            "organization_unit_ids": sorted(str(value) for value in organization_ids),
            "filters": {"is_current": True},
        }
        for metric, calculation in metrics
    ]


def _scope(claims: CapabilityClaims, arguments: dict[str, Any]) -> set[uuid.UUID]:
    requested = arguments.get("organization_unit_ids")
    if requested is None:
        return set(claims.organization_unit_ids)
    try:
        values = {uuid.UUID(str(value)) for value in requested}
    except (TypeError, ValueError) as exc:
        raise CapabilityError("organization scope is malformed") from exc
    if not values or not values.issubset(claims.organization_unit_ids):
        raise CapabilityError("requested organization scope is forbidden")
    return values


def _freshness(db: Session, claims: CapabilityClaims, domains: set[str]) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(DataDomainStatus).where(
            DataDomainStatus.enterprise_id == claims.enterprise_id,
            DataDomainStatus.domain.in_(domains),
        )
    ).all()
    return [
        {
            "domain": row.domain,
            "status": effective_domain_status(
                row, get_settings().data_stale_after_hours
            ),
            "source_type": row.source_type,
            "source_display_name": row.source_display_name,
            "source_data_as_of": (
                row.source_data_as_of.isoformat() if row.source_data_as_of else None
            ),
            "dataset_version": row.dataset_version,
            "last_error": row.last_error_message,
        }
        for row in rows
    ]


def _result(
    db: Session,
    claims: CapabilityClaims,
    *,
    tool: str,
    domains: set[str],
    data: dict[str, Any],
    references: list[dict[str, Any]],
    organization_ids: set[uuid.UUID],
) -> dict[str, Any]:
    return {
        "tool": tool,
        "data": data,
        "freshness": _freshness(db, claims, domains),
        "scope": {"organization_unit_ids": sorted(str(value) for value in organization_ids)},
        "evidence": references[:100],
    }


def list_query_scopes(
    db: Session, claims: CapabilityClaims, arguments: dict[str, Any]
) -> dict[str, Any]:
    organization_ids = _scope(claims, arguments)
    rows = db.scalars(
        select(OrganizationUnit)
        .where(
            OrganizationUnit.enterprise_id == claims.enterprise_id,
            OrganizationUnit.id.in_(organization_ids),
            OrganizationUnit.is_active.is_(True),
        )
        .order_by(OrganizationUnit.sort_order, OrganizationUnit.name)
    ).all()
    return _result(
        db,
        claims,
        tool="list_query_scopes",
        domains=set(),
        data={
            "organization_units": [
                {"id": str(row.id), "code": row.code, "name": row.name} for row in rows
            ]
        },
        references=[],
        organization_ids=organization_ids,
    )


def get_overall_business(
    db: Session, claims: CapabilityClaims, arguments: dict[str, Any]
) -> dict[str, Any]:
    organization_ids = _scope(claims, arguments)
    opportunity_filters = _period_filters(arguments, FactOpportunity.expected_close_date)
    delivery_filters = _period_filters(arguments, FactDelivery.planned_end_date)
    collection_filters = _period_filters(arguments, FactFinanceCollection.planned_collection_date)
    opportunity = db.execute(
        select(
            func.count(FactOpportunity.id),
            func.sum(FactOpportunity.expected_amount),
            func.sum(FactOpportunity.expected_amount * FactOpportunity.probability / 100),
        ).where(
            FactOpportunity.enterprise_id == claims.enterprise_id,
            FactOpportunity.organization_unit_id.in_(organization_ids),
            FactOpportunity.is_current.is_(True),
            *opportunity_filters,
        )
    ).one()
    delivery = db.execute(
        select(
            func.count(FactDelivery.id),
            func.count(FactDelivery.id).filter(FactDelivery.risk_level != "normal"),
        ).where(
            FactDelivery.enterprise_id == claims.enterprise_id,
            FactDelivery.organization_unit_id.in_(organization_ids),
            FactDelivery.is_current.is_(True),
            *delivery_filters,
        )
    ).one()
    collection = db.execute(
        select(
            func.sum(FactFinanceCollection.receivable_amount),
            func.sum(FactFinanceCollection.collected_amount),
            func.sum(FactFinanceCollection.outstanding_amount),
            func.sum(FactFinanceCollection.outstanding_amount).filter(
                FactFinanceCollection.overdue_days > 0
            ),
        ).where(
            FactFinanceCollection.enterprise_id == claims.enterprise_id,
            FactFinanceCollection.organization_unit_id.in_(organization_ids),
            FactFinanceCollection.is_current.is_(True),
            *collection_filters,
        )
    ).one()
    return _result(
        db,
        claims,
        tool="get_overall_business",
        domains={"opportunity", "delivery", "collection", "target"},
        data={
            "opportunity_count": int(opportunity[0] or 0),
            "pipeline_amount": _number(opportunity[1]),
            "weighted_pipeline_amount": _number(opportunity[2]),
            "delivery_count": int(delivery[0] or 0),
            "delivery_attention_count": int(delivery[1] or 0),
            "receivable_amount": _number(collection[0]),
            "collected_amount": _number(collection[1]),
            "outstanding_amount": _number(collection[2]),
            "overdue_amount": _number(collection[3]),
        },
        references=[
            *_aggregate_evidence(
                domain="opportunity",
                metrics=[
                    ("opportunity_count", "count(source_record_id)"),
                    ("pipeline_amount", "sum(expected_amount)"),
                    ("weighted_pipeline_amount", "sum(expected_amount * probability / 100)"),
                ],
                organization_ids=organization_ids,
            ),
            *_aggregate_evidence(
                domain="delivery",
                metrics=[
                    ("delivery_count", "count(source_record_id)"),
                    ("delivery_attention_count", "count(risk_level != normal)"),
                ],
                organization_ids=organization_ids,
            ),
            *_aggregate_evidence(
                domain="collection",
                metrics=[
                    ("receivable_amount", "sum(receivable_amount)"),
                    ("collected_amount", "sum(collected_amount)"),
                    ("outstanding_amount", "sum(outstanding_amount)"),
                    ("overdue_amount", "sum(outstanding_amount where overdue_days > 0)"),
                ],
                organization_ids=organization_ids,
            ),
        ],
        organization_ids=organization_ids,
    )


def get_target_completion(
    db: Session, claims: CapabilityClaims, arguments: dict[str, Any]
) -> dict[str, Any]:
    organization_ids = _scope(claims, arguments)
    target_filters = [
        FactTarget.enterprise_id == claims.enterprise_id,
        FactTarget.organization_unit_id.in_(organization_ids),
        FactTarget.is_current.is_(True),
    ]
    period_type = arguments.get("period_type")
    if period_type is not None:
        period_type = str(period_type).strip()
        if not period_type:
            raise CapabilityError("target period type is malformed")
        target_filters.append(FactTarget.period_type == period_type)

    requested_period_start = arguments.get("period_start")
    if requested_period_start:
        try:
            resolved_period_start = date.fromisoformat(str(requested_period_start))
        except ValueError as exc:
            raise CapabilityError("target period start is malformed") from exc
    else:
        resolved_period_start = db.scalar(
            select(func.max(FactTarget.period_start)).where(*target_filters)
        )
    if resolved_period_start is not None:
        target_filters.append(FactTarget.period_start == resolved_period_start)

    statement = select(
        FactTarget.metric_code,
        FactTarget.metric_name,
        FactTarget.unit,
        FactTarget.period_type,
        FactTarget.period_start,
        FactTarget.period_end,
        func.sum(FactTarget.target_value),
    ).where(*target_filters)
    rows = db.execute(
        statement.group_by(
            FactTarget.metric_code,
            FactTarget.metric_name,
            FactTarget.unit,
            FactTarget.period_type,
            FactTarget.period_start,
            FactTarget.period_end,
        ).order_by(FactTarget.period_start.desc(), FactTarget.period_type, FactTarget.metric_code)
    ).all()
    metrics = []
    actual_cache: dict[tuple[str, date, date], tuple[float | None, str]] = {}
    for code, name, unit, row_period_type, row_period_start, row_period_end, target in rows:
        target_value = _number(target)
        cache_key = (str(code), row_period_start, row_period_end)
        actual, actual_calculation = actual_cache.setdefault(
            cache_key,
            _target_actual(
                db,
                claims,
                organization_ids,
                metric_code=str(code),
                period_start=row_period_start,
                period_end=row_period_end,
            ),
        )
        metrics.append(
            {
                "metric_code": code,
                "metric_name": name,
                "unit": unit,
                "period_type": row_period_type,
                "period_start": row_period_start.isoformat(),
                "period_end": row_period_end.isoformat(),
                "target": target_value,
                "actual": actual,
                "completion_rate": actual / target_value
                if actual is not None and target_value
                else None,
                "actual_calculation": actual_calculation,
            }
        )
    domains = {"target"}
    references: list[dict[str, Any]] = []
    for item in metrics:
        period_filters = {
            "is_current": True,
            "period_type": item["period_type"],
            "period_start": item["period_start"],
            "period_end": item["period_end"],
        }
        references.append(
            {
                "domain": "target",
                "metric": f"{item['metric_code']}:target",
                "calculation": "sum(target_value)",
                "grouping": "metric_code, period_type, period_start, period_end",
                "organization_unit_ids": sorted(str(value) for value in organization_ids),
                "filters": period_filters,
            }
        )
        if item["actual"] is None:
            continue
        actual_domain = "collection" if item["metric_code"] == "collection" else "opportunity"
        domains.add(actual_domain)
        references.append(
            {
                "domain": actual_domain,
                "metric": f"{item['metric_code']}:actual",
                "calculation": item["actual_calculation"],
                "grouping": None,
                "organization_unit_ids": sorted(str(value) for value in organization_ids),
                "filters": period_filters,
            }
        )
    return _result(
        db,
        claims,
        tool="get_target_completion",
        domains=domains,
        data={"metrics": metrics},
        references=references,
        organization_ids=organization_ids,
    )


def get_opportunity_funnel(
    db: Session, claims: CapabilityClaims, arguments: dict[str, Any]
) -> dict[str, Any]:
    organization_ids = _scope(claims, arguments)
    filters = _period_filters(arguments, FactOpportunity.expected_close_date)
    statuses = _list_argument(arguments, "statuses")
    if statuses:
        filters.append(FactOpportunity.status.in_(statuses))
    rows = db.execute(
        select(
            FactOpportunity.stage,
            func.count(FactOpportunity.id),
            func.sum(FactOpportunity.expected_amount),
            func.sum(FactOpportunity.expected_amount * FactOpportunity.probability / 100),
        )
        .where(
            FactOpportunity.enterprise_id == claims.enterprise_id,
            FactOpportunity.organization_unit_id.in_(organization_ids),
            FactOpportunity.is_current.is_(True),
            *filters,
        )
        .group_by(FactOpportunity.stage)
        .order_by(func.sum(FactOpportunity.expected_amount).desc())
    ).all()
    return _result(
        db,
        claims,
        tool="get_opportunity_funnel",
        domains={"opportunity"},
        data={
            "stages": [
                {
                    "stage": stage,
                    "count": int(count),
                    "amount": _number(amount),
                    "weighted_amount": _number(weighted),
                }
                for stage, count, amount, weighted in rows
            ]
        },
        references=_aggregate_evidence(
            domain="opportunity",
            metrics=[("stage_funnel", "count and sum by stage")],
            organization_ids=organization_ids,
            grouping="stage",
        ),
        organization_ids=organization_ids,
    )


def get_sales_forecast(
    db: Session, claims: CapabilityClaims, arguments: dict[str, Any]
) -> dict[str, Any]:
    organization_ids = _scope(claims, arguments)
    statuses = _list_argument(arguments, "statuses") or ["active", "stalled"]
    try:
        minimum_probability = min(max(int(arguments.get("min_probability", 0)), 0), 100)
    except (TypeError, ValueError) as exc:
        raise CapabilityError("min_probability is malformed") from exc
    filters = [
        FactOpportunity.status.in_(statuses),
        FactOpportunity.probability >= minimum_probability,
        *_period_filters(arguments, FactOpportunity.expected_close_date),
    ]
    limit = min(max(int(arguments.get("limit", 50)), 1), 100)
    weighted_forecast = _number(
        db.scalar(
            select(
                func.sum(FactOpportunity.expected_amount * FactOpportunity.probability / 100)
            ).where(
                FactOpportunity.enterprise_id == claims.enterprise_id,
                FactOpportunity.organization_unit_id.in_(organization_ids),
                FactOpportunity.is_current.is_(True),
                *filters,
            )
        )
    )
    rows = db.scalars(
        select(FactOpportunity)
        .where(
            FactOpportunity.enterprise_id == claims.enterprise_id,
            FactOpportunity.organization_unit_id.in_(organization_ids),
            FactOpportunity.is_current.is_(True),
            *filters,
        )
        .order_by((FactOpportunity.expected_amount * FactOpportunity.probability).desc())
        .limit(limit)
    ).all()
    items = [
        {
            "source_record_id": row.source_record_id,
            "title": row.title,
            "probability": row.probability,
            "amount": _number(row.expected_amount),
            "weighted_amount": _number(row.expected_amount) * row.probability / 100,
            "expected_close_date": row.expected_close_date.isoformat(),
        }
        for row in rows
    ]
    return _result(
        db,
        claims,
        tool="get_sales_forecast",
        domains={"opportunity"},
        data={
            "weighted_forecast": weighted_forecast,
            "opportunities": items,
        },
        references=[
            *_aggregate_evidence(
                domain="opportunity",
                metrics=[
                    (
                        "weighted_forecast",
                        "sum(expected_amount * probability / 100) "
                        "where status is active or stalled",
                    )
                ],
                organization_ids=organization_ids,
            ),
            *[
                {"domain": "opportunity", "source_record_id": item["source_record_id"]}
                for item in items
            ],
        ],
        organization_ids=organization_ids,
    )


def get_customer_status(
    db: Session, claims: CapabilityClaims, arguments: dict[str, Any]
) -> dict[str, Any]:
    organization_ids = _scope(claims, arguments)
    limit = min(max(int(arguments.get("limit", 20)), 1), 100)
    filters: list[Any] = []
    customer_query = str(arguments.get("customer_query") or "").strip()
    if customer_query:
        filters.append(DimCustomer.display_name.ilike(f"%{customer_query[:120]}%"))
    if arguments.get("only_overdue") is True:
        filters.append(FactFinanceCollection.overdue_days > 0)
    rows = db.execute(
        select(
            DimCustomer.source_record_id,
            DimCustomer.display_name,
            func.sum(FactFinanceCollection.outstanding_amount),
            func.sum(FactFinanceCollection.outstanding_amount).filter(
                FactFinanceCollection.overdue_days > 0
            ),
        )
        .join(FactFinanceCollection, FactFinanceCollection.customer_id == DimCustomer.id)
        .where(
            DimCustomer.enterprise_id == claims.enterprise_id,
            FactFinanceCollection.enterprise_id == claims.enterprise_id,
            FactFinanceCollection.organization_unit_id.in_(organization_ids),
            FactFinanceCollection.is_current.is_(True),
            *filters,
        )
        .group_by(DimCustomer.id)
        .order_by(func.sum(FactFinanceCollection.outstanding_amount).desc())
        .limit(limit)
    ).all()
    customers = [
        {
            "source_record_id": source_id,
            "name": name,
            "outstanding_amount": _number(outstanding),
            "overdue_amount": _number(overdue),
        }
        for source_id, name, outstanding, overdue in rows
    ]
    return _result(
        db,
        claims,
        tool="get_customer_status",
        domains={"opportunity", "delivery", "collection"},
        data={"customers": customers},
        references=[
            {"domain": "customer", "source_record_id": row["source_record_id"]} for row in customers
        ],
        organization_ids=organization_ids,
    )


def get_delivery_status(
    db: Session, claims: CapabilityClaims, arguments: dict[str, Any]
) -> dict[str, Any]:
    organization_ids = _scope(claims, arguments)
    filters = _period_filters(arguments, FactDelivery.planned_end_date)
    project_query = str(arguments.get("project_query") or "").strip()
    if project_query:
        filters.append(FactDelivery.project_name.ilike(f"%{project_query[:120]}%"))
    statuses = _list_argument(arguments, "statuses")
    if statuses:
        filters.append(FactDelivery.status.in_(statuses))
    risk_levels = _list_argument(arguments, "risk_levels")
    if risk_levels:
        filters.append(FactDelivery.risk_level.in_(risk_levels))
    limit = min(max(int(arguments.get("limit", 50)), 1), 100)
    delivery_totals = db.execute(
        select(
            func.count(FactDelivery.id),
            func.count(FactDelivery.id).filter(FactDelivery.risk_level != "normal"),
        ).where(
            FactDelivery.enterprise_id == claims.enterprise_id,
            FactDelivery.organization_unit_id.in_(organization_ids),
            FactDelivery.is_current.is_(True),
            *filters,
        )
    ).one()
    rows = db.scalars(
        select(FactDelivery)
        .where(
            FactDelivery.enterprise_id == claims.enterprise_id,
            FactDelivery.organization_unit_id.in_(organization_ids),
            FactDelivery.is_current.is_(True),
            *filters,
        )
        .order_by(FactDelivery.delay_days.desc(), FactDelivery.contract_amount.desc())
        .limit(limit)
    ).all()
    projects = [
        {
            "source_record_id": row.source_record_id,
            "project_name": row.project_name,
            "status": row.status,
            "risk_level": row.risk_level,
            "completion_percent": row.completion_percent,
            "milestone": row.current_milestone,
            "delay_days": row.delay_days,
            "contract_amount": _number(row.contract_amount),
        }
        for row in rows
    ]
    return _result(
        db,
        claims,
        tool="get_delivery_status",
        domains={"delivery"},
        data={
            "project_count": int(delivery_totals[0] or 0),
            "attention_count": int(delivery_totals[1] or 0),
            "projects": projects,
        },
        references=[
            *_aggregate_evidence(
                domain="delivery",
                metrics=[
                    ("project_count", "count(source_record_id)"),
                    ("attention_count", "count(risk_level != normal)"),
                ],
                organization_ids=organization_ids,
            ),
            *[
                {"domain": "delivery", "source_record_id": row["source_record_id"]}
                for row in projects
            ],
        ],
        organization_ids=organization_ids,
    )


def get_finance_margin(
    db: Session, claims: CapabilityClaims, arguments: dict[str, Any]
) -> dict[str, Any]:
    organization_ids = _scope(claims, arguments)
    filters = _period_filters(arguments, FactDelivery.planned_end_date)
    contract, gross_profit = db.execute(
        select(
            func.sum(FactDelivery.contract_amount),
            func.sum(FactDelivery.contract_amount * FactDelivery.gross_margin_rate),
        ).where(
            FactDelivery.enterprise_id == claims.enterprise_id,
            FactDelivery.organization_unit_id.in_(organization_ids),
            FactDelivery.is_current.is_(True),
            *filters,
        )
    ).one()
    contract_amount = _number(contract)
    gross_profit_amount = _number(gross_profit)
    return _result(
        db,
        claims,
        tool="get_finance_margin",
        domains={"delivery", "collection"},
        data={
            "contract_amount": contract_amount,
            "gross_profit_amount": gross_profit_amount,
            "gross_margin_rate": gross_profit_amount / contract_amount if contract_amount else 0,
        },
        references=_aggregate_evidence(
            domain="delivery",
            metrics=[
                ("contract_amount", "sum(contract_amount)"),
                ("gross_profit_amount", "sum(contract_amount * gross_margin_rate)"),
                ("gross_margin_rate", "gross_profit_amount / contract_amount"),
            ],
            organization_ids=organization_ids,
        ),
        organization_ids=organization_ids,
    )


def get_collection_aging(
    db: Session, claims: CapabilityClaims, arguments: dict[str, Any]
) -> dict[str, Any]:
    organization_ids = _scope(claims, arguments)
    filters: list[Any] = []
    aging_buckets = _list_argument(arguments, "aging_buckets")
    if aging_buckets:
        filters.append(FactFinanceCollection.aging_bucket.in_(aging_buckets))
    minimum_overdue_days = arguments.get("minimum_overdue_days")
    if minimum_overdue_days is not None:
        try:
            filters.append(
                FactFinanceCollection.overdue_days
                >= min(max(int(minimum_overdue_days), 0), 3650)
            )
        except (TypeError, ValueError) as exc:
            raise CapabilityError("minimum_overdue_days is malformed") from exc
    customer_query = str(arguments.get("customer_query") or "").strip()
    statement = select(
        FactFinanceCollection.aging_bucket,
        func.count(FactFinanceCollection.id),
        func.sum(FactFinanceCollection.outstanding_amount),
    )
    if customer_query:
        statement = statement.join(
            DimCustomer, DimCustomer.id == FactFinanceCollection.customer_id
        )
        filters.append(DimCustomer.display_name.ilike(f"%{customer_query[:120]}%"))
    rows = db.execute(
        statement
        .where(
            FactFinanceCollection.enterprise_id == claims.enterprise_id,
            FactFinanceCollection.organization_unit_id.in_(organization_ids),
            FactFinanceCollection.is_current.is_(True),
            *filters,
        )
        .group_by(FactFinanceCollection.aging_bucket)
    ).all()
    return _result(
        db,
        claims,
        tool="get_collection_aging",
        domains={"collection"},
        data={
            "aging": [
                {"bucket": bucket, "count": int(count), "outstanding_amount": _number(amount)}
                for bucket, count, amount in rows
            ]
        },
        references=_aggregate_evidence(
            domain="collection",
            metrics=[("collection_aging", "count and sum(outstanding_amount) by aging_bucket")],
            organization_ids=organization_ids,
            grouping="aging_bucket",
        ),
        organization_ids=organization_ids,
    )


def get_organization_performance(
    db: Session, claims: CapabilityClaims, arguments: dict[str, Any]
) -> dict[str, Any]:
    organization_ids = _scope(claims, arguments)
    filters = _period_filters(arguments, FactFinanceCollection.planned_collection_date)
    rows = db.execute(
        select(
            OrganizationUnit.id,
            OrganizationUnit.name,
            func.sum(FactFinanceCollection.collected_amount),
            func.sum(FactFinanceCollection.outstanding_amount),
        )
        .join(
            FactFinanceCollection,
            FactFinanceCollection.organization_unit_id == OrganizationUnit.id,
        )
        .where(
            OrganizationUnit.enterprise_id == claims.enterprise_id,
            OrganizationUnit.id.in_(organization_ids),
            FactFinanceCollection.is_current.is_(True),
            *filters,
        )
        .group_by(OrganizationUnit.id)
        .order_by(func.sum(FactFinanceCollection.collected_amount).desc())
    ).all()
    return _result(
        db,
        claims,
        tool="get_organization_performance",
        domains={"opportunity", "delivery", "collection", "target"},
        data={
            "organizations": [
                {
                    "organization_unit_id": str(identifier),
                    "name": name,
                    "collected_amount": _number(collected),
                    "outstanding_amount": _number(outstanding),
                }
                for identifier, name, collected, outstanding in rows
            ]
        },
        references=_aggregate_evidence(
            domain="collection",
            metrics=[("organization_performance", "sum by organization_unit_id")],
            organization_ids=organization_ids,
            grouping="organization_unit_id",
        ),
        organization_ids=organization_ids,
    )


def get_daily_changes(
    db: Session, claims: CapabilityClaims, arguments: dict[str, Any]
) -> dict[str, Any]:
    organization_ids = _scope(claims, arguments)
    try:
        days = min(max(int(arguments.get("days", 2)), 1), 31)
    except (TypeError, ValueError) as exc:
        raise CapabilityError("days is malformed") from exc
    rows = db.scalars(
        select(DailySnapshot)
        .where(
            DailySnapshot.enterprise_id == claims.enterprise_id,
            DailySnapshot.organization_unit_id.in_(organization_ids),
        )
        .order_by(DailySnapshot.snapshot_date.desc())
        .limit(len(organization_ids) * days)
    ).all()
    snapshots = [
        {
            "organization_unit_id": str(row.organization_unit_id),
            "snapshot_date": row.snapshot_date.isoformat(),
            "metrics": row.metrics_json,
            "anomalies": row.anomalies_json,
            "source_data_as_of": row.source_data_as_of.isoformat(),
        }
        for row in rows
    ]
    return _result(
        db,
        claims,
        tool="get_daily_changes",
        domains={"opportunity", "delivery", "collection"},
        data={"snapshots": snapshots},
        references=[
            {
                "domain": "daily_snapshot",
                "organization_unit_id": row["organization_unit_id"],
                "snapshot_date": row["snapshot_date"],
            }
            for row in snapshots
        ],
        organization_ids=organization_ids,
    )


TOOLS: dict[
    str,
    Callable[[Session, CapabilityClaims, dict[str, Any]], dict[str, Any]],
] = {
    "list_query_scopes": list_query_scopes,
    "get_overall_business": get_overall_business,
    "get_target_completion": get_target_completion,
    "get_opportunity_funnel": get_opportunity_funnel,
    "get_sales_forecast": get_sales_forecast,
    "get_customer_status": get_customer_status,
    "get_delivery_status": get_delivery_status,
    "get_finance_margin": get_finance_margin,
    "get_collection_aging": get_collection_aging,
    "get_organization_performance": get_organization_performance,
    "get_daily_changes": get_daily_changes,
}


def execute_business_tool(
    db: Session,
    claims: CapabilityClaims,
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    if tool_name not in claims.tools:
        raise CapabilityError("tool is not allowed by this capability")
    handler = TOOLS.get(tool_name)
    if handler is None:
        raise CapabilityError("unknown tool")
    configuration = effective_tool(db, claims.enterprise_id, tool_name)
    if configuration is None or not configuration["is_enabled"]:
        raise CapabilityError("tool is disabled by enterprise configuration")
    bounded_arguments = dict(arguments)
    if "limit" in bounded_arguments:
        try:
            bounded_arguments["limit"] = min(
                max(int(bounded_arguments["limit"]), 1),
                int(configuration["max_rows"]),
            )
        except (TypeError, ValueError) as exc:
            raise CapabilityError("tool limit is malformed") from exc
    return handler(db, claims, bounded_arguments)
