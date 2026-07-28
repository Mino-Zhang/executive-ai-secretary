from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import date
from typing import Any

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session, aliased

from .capabilities import CapabilityClaims, CapabilityError
from .config import get_settings
from .data_freshness import effective_domain_status
from .mcp_registry import effective_tool, registered_spec
from .models import (
    DailySnapshot,
    DataDomainStatus,
    DataSyncRun,
    DimCustomer,
    DimPerson,
    FactDelivery,
    FactFinanceCollection,
    FactOpportunity,
    FactOpportunityParticipant,
    FactOpportunityProduct,
    OpportunityExperienceWeightPolicy,
    OrganizationUnit,
)


def _number(value: Any) -> float:
    return float(value or 0)


_DEFAULT_EXPERIENCE_WEIGHTS = {"high": 0.20, "medium": 0.10, "low": 0.05}
_RELIABILITY_ALIASES = {
    "高": "high",
    "high": "high",
    "中": "medium",
    "medium": "medium",
    "低": "low",
    "low": "low",
}


def _bounded_limit(arguments: dict[str, Any], *, default: int = 50) -> int:
    try:
        return min(max(int(arguments.get("limit", default)), 1), 100)
    except (TypeError, ValueError) as exc:
        raise CapabilityError("limit is malformed") from exc


def _normalized_reliability(value: Any) -> str:
    return _RELIABILITY_ALIASES.get(str(value or "").strip().lower(), "")


def _normalized_reliability_values(arguments: dict[str, Any]) -> list[str]:
    values = _list_argument(arguments, "reliability_levels")
    normalized = [_normalized_reliability(value) for value in values]
    if any(not value for value in normalized):
        raise CapabilityError("reliability_levels is malformed")
    return list(dict.fromkeys(normalized))


def _status_expression() -> Any:
    return func.coalesce(FactOpportunity.status_code, FactOpportunity.status)


def _stage_expression() -> Any:
    return func.coalesce(FactOpportunity.stage_label, FactOpportunity.stage)


def _signed_amount_expression() -> Any:
    return case(
        (
            _status_expression() == "won",
            func.coalesce(FactOpportunity.signed_amount, FactOpportunity.expected_amount),
        ),
        else_=0,
    )


def _active_experience_weight_policy(
    db: Session, enterprise_id: uuid.UUID
) -> tuple[dict[str, float], dict[str, Any]]:
    policy = db.scalar(
        select(OpportunityExperienceWeightPolicy)
        .where(
            OpportunityExperienceWeightPolicy.enterprise_id == enterprise_id,
            OpportunityExperienceWeightPolicy.is_active.is_(True),
        )
        .order_by(
            OpportunityExperienceWeightPolicy.activated_at.desc(),
            OpportunityExperienceWeightPolicy.created_at.desc(),
        )
        .limit(1)
    )
    if policy is None:
        return dict(_DEFAULT_EXPERIENCE_WEIGHTS), {
            "version": "v1-default",
            "label": "保守经验权重（默认）",
            "weights": dict(_DEFAULT_EXPERIENCE_WEIGHTS),
            "observation_window_days": 90,
            "source": "system_default",
        }
    weights: dict[str, float] = {}
    for level in ("high", "medium", "low"):
        try:
            value = float(policy.weights_json[level])
        except (KeyError, TypeError, ValueError) as exc:
            raise CapabilityError("active experience weight policy is malformed") from exc
        if value < 0 or value > 1:
            raise CapabilityError("active experience weight policy is malformed")
        weights[level] = value
    return weights, {
        "id": str(policy.id),
        "version": policy.version,
        "label": policy.label,
        "weights": weights,
        "observation_window_days": policy.observation_window_days,
        "source": "enterprise_configuration",
    }


def _experience_weight_expression(weights: dict[str, float]) -> Any:
    return case(
        (
            func.lower(FactOpportunity.reliability_level).in_(["高", "high"]),
            weights["high"],
        ),
        (
            func.lower(FactOpportunity.reliability_level).in_(["中", "medium"]),
            weights["medium"],
        ),
        (
            func.lower(FactOpportunity.reliability_level).in_(["低", "low"]),
            weights["low"],
        ),
        else_=0,
    )


def _atomic_batch_identity(
    db: Session, claims: CapabilityClaims, *, required: bool = False
) -> dict[str, Any] | None:
    domains = {"opportunity", "delivery", "collection"}
    rows = db.scalars(
        select(DataDomainStatus).where(
            DataDomainStatus.enterprise_id == claims.enterprise_id,
            DataDomainStatus.domain.in_(domains),
        )
    ).all()
    if not rows and not required:
        return None
    by_domain = {row.domain: row for row in rows}
    sync_run_ids = {
        str(by_domain[domain].active_sync_run_id)
        for domain in domains
        if domain in by_domain and by_domain[domain].active_sync_run_id is not None
    }
    source_batch_ids = {
        str(by_domain[domain].current_source_batch_id)
        for domain in domains
        if domain in by_domain and by_domain[domain].current_source_batch_id
    }
    contract_versions = {
        str(by_domain[domain].contract_version)
        for domain in domains
        if domain in by_domain and by_domain[domain].contract_version
    }
    is_v3 = "3.0" in contract_versions
    if set(by_domain) != domains or len(sync_run_ids) != 1:
        raise CapabilityError("operating data domains are not on one atomic batch")
    if is_v3 and (len(source_batch_ids) != 1 or contract_versions != {"3.0"}):
        raise CapabilityError("operating data domains are not on one atomic batch")
    source_times = [
        by_domain[domain].source_data_as_of
        for domain in domains
        if by_domain[domain].source_data_as_of is not None
    ]
    return {
        "source_batch_id": next(iter(source_batch_ids), None),
        "sync_run_id": next(iter(sync_run_ids)),
        "contract_version": next(iter(contract_versions), "2.0"),
        # The oldest domain timestamp is the conservative common cutoff for a
        # cross-domain answer; individual timestamps remain in freshness.
        "source_data_as_of": min(source_times).isoformat() if source_times else None,
    }


def _opportunity_dimension_filters(
    arguments: dict[str, Any],
) -> tuple[list[Any], dict[str, Any]]:
    filters: list[Any] = []
    applied: dict[str, Any] = {}
    reliability_levels = _normalized_reliability_values(arguments)
    if reliability_levels:
        database_values: list[str] = []
        for level in reliability_levels:
            database_values.extend(
                {
                    "high": ["高", "high"],
                    "medium": ["中", "medium"],
                    "low": ["低", "low"],
                }[level]
            )
        filters.append(func.lower(FactOpportunity.reliability_level).in_(database_values))
        applied["reliability_levels"] = reliability_levels

    for argument_key, column in (
        ("customer_value_levels", FactOpportunity.customer_value_level),
        ("industries", FactOpportunity.industry),
    ):
        values = _list_argument(arguments, argument_key)
        if values:
            filters.append(column.in_(values))
            applied[argument_key] = values

    product_services = _list_argument(arguments, "product_services")
    if product_services:
        product_filters = [
            FactOpportunityProduct.normalized_product_name.ilike(f"%{value[:120]}%")
            for value in product_services
        ]
        filters.append(
            FactOpportunity.id.in_(
                select(FactOpportunityProduct.opportunity_id).where(or_(*product_filters))
            )
        )
        applied["product_services"] = product_services

    for argument_key, role in (
        ("sales_owner_query", "sales"),
        ("presales_owner_query", "pre_sales"),
    ):
        query = str(arguments.get(argument_key) or "").strip()
        if not query:
            continue
        participant_ids = (
            select(FactOpportunityParticipant.opportunity_id)
            .join(DimPerson, DimPerson.id == FactOpportunityParticipant.person_id)
            .where(
                FactOpportunityParticipant.participant_role == role,
                DimPerson.display_name.ilike(f"%{query[:120]}%"),
            )
        )
        if role == "sales":
            owner_ids = select(DimPerson.id).where(DimPerson.display_name.ilike(f"%{query[:120]}%"))
            filters.append(
                or_(
                    FactOpportunity.owner_person_id.in_(owner_ids),
                    FactOpportunity.id.in_(participant_ids),
                )
            )
        else:
            filters.append(FactOpportunity.id.in_(participant_ids))
        applied[argument_key] = query[:120]
    return filters, applied


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
            "status": effective_domain_status(row, get_settings().data_stale_after_hours),
            "source_type": row.source_type,
            "source_display_name": row.source_display_name,
            "source_data_as_of": (
                row.source_data_as_of.isoformat() if row.source_data_as_of else None
            ),
            "dataset_version": row.dataset_version,
            "source_batch_id": row.current_source_batch_id,
            "sync_run_id": str(row.active_sync_run_id) if row.active_sync_run_id else None,
            "contract_version": row.contract_version,
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
    weights, weight_policy = _active_experience_weight_policy(db, claims.enterprise_id)
    weight_expression = _experience_weight_expression(weights)
    status_expression = _status_expression()
    opportunity_filters = _period_filters(arguments, FactOpportunity.expected_close_date)
    delivery_filters = _period_filters(arguments, FactDelivery.planned_end_date)
    collection_filters = _period_filters(arguments, FactFinanceCollection.planned_collection_date)
    opportunity = db.execute(
        select(
            func.count(FactOpportunity.id),
            func.sum(FactOpportunity.expected_amount).filter(status_expression == "active"),
            func.sum(FactOpportunity.expected_amount * weight_expression).filter(
                status_expression == "active"
            ),
            func.sum(_signed_amount_expression()),
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
            func.sum(FactDelivery.contract_amount),
            func.sum(FactDelivery.recognized_revenue),
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
    atomic_batch = _atomic_batch_identity(db, claims)
    return _result(
        db,
        claims,
        tool="get_overall_business",
        domains={"opportunity", "delivery", "collection"},
        data={
            "atomic_batch_id": atomic_batch["source_batch_id"] if atomic_batch else None,
            "source_batch_id": atomic_batch["source_batch_id"] if atomic_batch else None,
            "sync_run_id": atomic_batch["sync_run_id"] if atomic_batch else None,
            "contract_version": atomic_batch["contract_version"] if atomic_batch else None,
            "source_data_as_of": atomic_batch["source_data_as_of"] if atomic_batch else None,
            "opportunity_count": int(opportunity[0] or 0),
            "active_pipeline_amount": _number(opportunity[1]),
            "experience_weighted_pipeline_amount": _number(opportunity[2]),
            "signed_amount": _number(opportunity[3]),
            "experience_weight_policy": weight_policy,
            "delivery_count": int(delivery[0] or 0),
            "delivery_attention_count": int(delivery[1] or 0),
            "contract_amount": _number(delivery[2]),
            "recognized_revenue": _number(delivery[3]),
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
                    ("active_pipeline_amount", "sum(expected_amount where status_code = active)"),
                    (
                        "experience_weighted_pipeline_amount",
                        "sum(expected_amount * configured experience weight) "
                        "where status_code = active",
                    ),
                    (
                        "signed_amount",
                        "sum(coalesce(signed_amount, expected_amount) where status_code = won)",
                    ),
                ],
                organization_ids=organization_ids,
            ),
            *_aggregate_evidence(
                domain="delivery",
                metrics=[
                    ("delivery_count", "count(source_record_id)"),
                    ("delivery_attention_count", "count(risk_level != normal)"),
                    ("contract_amount", "sum(contract_amount)"),
                    ("recognized_revenue", "sum(recognized_revenue)"),
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
            {
                "domain": "metric_policy",
                "metric": "experience_weighted_pipeline_amount",
                "calculation": "configured high/medium/low experience weights",
                "policy": weight_policy,
            },
        ],
        organization_ids=organization_ids,
    )


def get_target_completion(
    db: Session, claims: CapabilityClaims, arguments: dict[str, Any]
) -> dict[str, Any]:
    organization_ids = _scope(claims, arguments)
    return _result(
        db,
        claims,
        tool="get_target_completion",
        domains={"target"},
        data={
            "availability": "not_configured",
            "message": "目标数据尚未接入",
            "metrics": [],
        },
        references=[
            {
                "domain": "target",
                "metric": "availability",
                "calculation": "target domain configuration status",
                "status": "not_configured",
                "organization_unit_ids": sorted(str(value) for value in organization_ids),
            }
        ],
        organization_ids=organization_ids,
    )


def get_opportunity_funnel(
    db: Session, claims: CapabilityClaims, arguments: dict[str, Any]
) -> dict[str, Any]:
    organization_ids = _scope(claims, arguments)
    weights, weight_policy = _active_experience_weight_policy(db, claims.enterprise_id)
    status_expression = _status_expression()
    stage_expression = _stage_expression()
    filters = _period_filters(arguments, FactOpportunity.expected_close_date)
    statuses = _list_argument(arguments, "statuses")
    if statuses:
        filters.append(status_expression.in_(statuses))
    dimension_filters, applied_filters = _opportunity_dimension_filters(arguments)
    filters.extend(dimension_filters)
    weight_expression = _experience_weight_expression(weights)
    rows = db.execute(
        select(
            stage_expression,
            func.count(FactOpportunity.id),
            func.sum(FactOpportunity.expected_amount),
            func.sum(
                case(
                    (
                        status_expression == "active",
                        FactOpportunity.expected_amount * weight_expression,
                    ),
                    else_=0,
                )
            ),
            func.sum(_signed_amount_expression()),
        )
        .where(
            FactOpportunity.enterprise_id == claims.enterprise_id,
            FactOpportunity.organization_unit_id.in_(organization_ids),
            FactOpportunity.is_current.is_(True),
            *filters,
        )
        .group_by(stage_expression)
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
                    "experience_weighted_amount": _number(weighted),
                    "signed_amount": _number(signed),
                }
                for stage, count, amount, weighted, signed in rows
            ],
            "experience_weight_policy": weight_policy,
            "applied_filters": {
                **applied_filters,
                **({"statuses": statuses} if statuses else {}),
            },
        },
        references=[
            {
                **_aggregate_evidence(
                    domain="opportunity",
                    metrics=[
                        (
                            "stage_funnel",
                            "count, sum(expected_amount), experience-weighted active "
                            "amount and won signed amount by stage_label",
                        )
                    ],
                    organization_ids=organization_ids,
                    grouping="stage_label",
                )[0],
                "filters": {
                    "is_current": True,
                    **applied_filters,
                    **({"statuses": statuses} if statuses else {}),
                },
            },
            {
                "domain": "metric_policy",
                "metric": "experience_weighted_amount",
                "calculation": "configured high/medium/low experience weights",
                "policy": weight_policy,
            },
        ],
        organization_ids=organization_ids,
    )


def get_sales_forecast(
    db: Session, claims: CapabilityClaims, arguments: dict[str, Any]
) -> dict[str, Any]:
    organization_ids = _scope(claims, arguments)
    weights, weight_policy = _active_experience_weight_policy(db, claims.enterprise_id)
    status_expression = _status_expression()
    weight_expression = _experience_weight_expression(weights)
    reliability_levels = _normalized_reliability_values(arguments)
    filters = [
        status_expression == "active",
        *_period_filters(arguments, FactOpportunity.expected_close_date),
    ]
    if reliability_levels:
        database_values: list[str] = []
        for level in reliability_levels:
            database_values.extend(
                {
                    "high": ["高", "high"],
                    "medium": ["中", "medium"],
                    "low": ["低", "low"],
                }[level]
            )
        filters.append(func.lower(FactOpportunity.reliability_level).in_(database_values))
    period_filters = _period_filters(arguments, FactOpportunity.expected_close_date)
    limit = _bounded_limit(arguments)
    experience_weighted_forecast = _number(
        db.scalar(
            select(func.sum(FactOpportunity.expected_amount * weight_expression)).where(
                FactOpportunity.enterprise_id == claims.enterprise_id,
                FactOpportunity.organization_unit_id.in_(organization_ids),
                FactOpportunity.is_current.is_(True),
                *filters,
            )
        )
    )
    won_signed_amount = _number(
        db.scalar(
            select(func.sum(_signed_amount_expression())).where(
                FactOpportunity.enterprise_id == claims.enterprise_id,
                FactOpportunity.organization_unit_id.in_(organization_ids),
                FactOpportunity.is_current.is_(True),
                status_expression == "won",
                *period_filters,
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
        .order_by((FactOpportunity.expected_amount * weight_expression).desc())
        .limit(limit)
    ).all()
    items = []
    for row in rows:
        reliability_level = _normalized_reliability(row.reliability_level)
        weight = weights.get(reliability_level, 0)
        items.append(
            {
                "source_record_id": row.source_record_id,
                "title": row.title,
                "reliability_level": reliability_level,
                "experience_weight": weight,
                "amount": _number(row.expected_amount),
                "experience_weighted_amount": _number(row.expected_amount) * weight,
                "expected_close_date": row.expected_close_date.isoformat(),
            }
        )
    return _result(
        db,
        claims,
        tool="get_sales_forecast",
        domains={"opportunity"},
        data={
            "experience_weighted_forecast_amount": experience_weighted_forecast,
            "won_signed_amount": won_signed_amount,
            "experience_weight_policy": weight_policy,
            "opportunities": items,
        },
        references=[
            *_aggregate_evidence(
                domain="opportunity",
                metrics=[
                    (
                        "experience_weighted_forecast_amount",
                        "sum(expected_amount * configured experience weight) "
                        "where status_code = active",
                    ),
                    (
                        "won_signed_amount",
                        "sum(coalesce(signed_amount, expected_amount)) where status_code = won",
                    ),
                ],
                organization_ids=organization_ids,
            ),
            {
                "domain": "metric_policy",
                "metric": "experience_weighted_forecast_amount",
                "calculation": "configured high/medium/low experience weights",
                "policy": weight_policy,
            },
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
    limit = _bounded_limit(arguments, default=20)
    customer_filters: list[Any] = []
    customer_query = str(arguments.get("customer_query") or "").strip()
    if customer_query:
        customer_filters.append(DimCustomer.display_name.ilike(f"%{customer_query[:120]}%"))
    customer_rows = db.scalars(
        select(DimCustomer).where(
            DimCustomer.enterprise_id == claims.enterprise_id,
            *customer_filters,
        )
    ).all()
    if not customer_rows:
        return _result(
            db,
            claims,
            tool="get_customer_status",
            domains={"opportunity", "delivery", "collection"},
            data={"customers": []},
            references=[],
            organization_ids=organization_ids,
        )
    customer_ids = {row.id for row in customer_rows}
    opportunity_rows = db.execute(
        select(
            FactOpportunity.customer_id,
            func.count(FactOpportunity.id),
            func.sum(FactOpportunity.expected_amount).filter(_status_expression() == "active"),
            func.sum(_signed_amount_expression()),
        )
        .where(
            FactOpportunity.enterprise_id == claims.enterprise_id,
            FactOpportunity.organization_unit_id.in_(organization_ids),
            FactOpportunity.customer_id.in_(customer_ids),
            FactOpportunity.is_current.is_(True),
        )
        .group_by(FactOpportunity.customer_id)
    ).all()
    delivery_rows = db.execute(
        select(
            FactDelivery.customer_id,
            func.count(FactDelivery.id),
            func.sum(FactDelivery.contract_amount),
            func.sum(FactDelivery.recognized_revenue),
        )
        .where(
            FactDelivery.enterprise_id == claims.enterprise_id,
            FactDelivery.organization_unit_id.in_(organization_ids),
            FactDelivery.customer_id.in_(customer_ids),
            FactDelivery.is_current.is_(True),
        )
        .group_by(FactDelivery.customer_id)
    ).all()
    collection_rows = db.execute(
        select(
            FactFinanceCollection.customer_id,
            func.sum(FactFinanceCollection.receivable_amount),
            func.sum(FactFinanceCollection.collected_amount),
            func.sum(FactFinanceCollection.outstanding_amount),
            func.sum(FactFinanceCollection.outstanding_amount).filter(
                FactFinanceCollection.overdue_days > 0
            ),
        )
        .where(
            FactFinanceCollection.enterprise_id == claims.enterprise_id,
            FactFinanceCollection.organization_unit_id.in_(organization_ids),
            FactFinanceCollection.customer_id.in_(customer_ids),
            FactFinanceCollection.is_current.is_(True),
        )
        .group_by(FactFinanceCollection.customer_id)
    ).all()
    opportunities = {
        customer_id: (count, active_pipeline, signed)
        for customer_id, count, active_pipeline, signed in opportunity_rows
    }
    deliveries = {
        customer_id: (count, contract, recognized)
        for customer_id, count, contract, recognized in delivery_rows
    }
    collections = {
        customer_id: (receivable, collected, outstanding, overdue)
        for customer_id, receivable, collected, outstanding, overdue in collection_rows
    }
    customers = []
    for customer in customer_rows:
        opportunity = opportunities.get(customer.id, (0, 0, 0))
        delivery = deliveries.get(customer.id, (0, 0, 0))
        collection = collections.get(customer.id, (0, 0, 0, 0))
        row = {
            "source_record_id": customer.source_record_id,
            "name": customer.display_name,
            "industry": customer.industry,
            "opportunity_count": int(opportunity[0] or 0),
            "active_pipeline_amount": _number(opportunity[1]),
            "signed_amount": _number(opportunity[2]),
            "project_count": int(delivery[0] or 0),
            "contract_amount": _number(delivery[1]),
            "recognized_revenue": _number(delivery[2]),
            "receivable_amount": _number(collection[0]),
            "collected_amount": _number(collection[1]),
            "outstanding_amount": _number(collection[2]),
            "overdue_amount": _number(collection[3]),
        }
        if arguments.get("only_overdue") is not True or row["overdue_amount"] > 0:
            customers.append(row)
    customers.sort(
        key=lambda item: (
            item["overdue_amount"],
            item["outstanding_amount"],
            item["signed_amount"],
        ),
        reverse=True,
    )
    customers = customers[:limit]
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
    limit = _bounded_limit(arguments)
    delivery_totals = db.execute(
        select(
            func.count(FactDelivery.id),
            func.count(FactDelivery.id).filter(FactDelivery.risk_level != "normal"),
            func.sum(FactDelivery.contract_amount),
            func.sum(FactDelivery.recognized_revenue),
        ).where(
            FactDelivery.enterprise_id == claims.enterprise_id,
            FactDelivery.organization_unit_id.in_(organization_ids),
            FactDelivery.is_current.is_(True),
            *filters,
        )
    ).one()
    manager = aliased(DimPerson)
    delivery_owner = aliased(DimPerson)
    rows = db.execute(
        select(FactDelivery, manager.display_name, delivery_owner.display_name)
        .outerjoin(manager, manager.id == FactDelivery.manager_person_id)
        .outerjoin(
            delivery_owner,
            delivery_owner.id == FactDelivery.delivery_owner_person_id,
        )
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
            "source_record_id": project.source_record_id,
            "project_name": project.project_name,
            "status": project.status,
            "risk_level": project.risk_level,
            "project_manager": manager_name,
            "delivery_owner": delivery_owner_name,
            "completion_percent": project.completion_percent,
            "milestone": project.current_milestone,
            "delay_days": project.delay_days,
            "contract_amount": _number(project.contract_amount),
            "recognized_revenue": _number(project.recognized_revenue),
            "actual_start_date": (
                project.actual_start_date.isoformat() if project.actual_start_date else None
            ),
            "latest_progress": project.latest_progress,
        }
        for project, manager_name, delivery_owner_name in rows
    ]
    return _result(
        db,
        claims,
        tool="get_delivery_status",
        domains={"delivery"},
        data={
            "project_count": int(delivery_totals[0] or 0),
            "attention_count": int(delivery_totals[1] or 0),
            "contract_amount": _number(delivery_totals[2]),
            "recognized_revenue": _number(delivery_totals[3]),
            "projects": projects,
        },
        references=[
            *_aggregate_evidence(
                domain="delivery",
                metrics=[
                    ("project_count", "count(source_record_id)"),
                    ("attention_count", "count(risk_level != normal)"),
                    ("contract_amount", "sum(contract_amount)"),
                    ("recognized_revenue", "sum(recognized_revenue)"),
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
    contract, recognized_revenue, recognized_gross_profit, contract_expected_gross_profit = (
        db.execute(
            select(
                func.sum(FactDelivery.contract_amount),
                func.sum(FactDelivery.recognized_revenue),
                func.sum(FactDelivery.recognized_revenue * FactDelivery.gross_margin_rate),
                func.sum(FactDelivery.contract_amount * FactDelivery.gross_margin_rate),
            ).where(
                FactDelivery.enterprise_id == claims.enterprise_id,
                FactDelivery.organization_unit_id.in_(organization_ids),
                FactDelivery.is_current.is_(True),
                *filters,
            )
        ).one()
    )
    contract_amount = _number(contract)
    recognized_revenue_amount = _number(recognized_revenue)
    recognized_gross_profit_amount = _number(recognized_gross_profit)
    return _result(
        db,
        claims,
        tool="get_finance_margin",
        domains={"delivery", "collection"},
        data={
            "contract_amount": contract_amount,
            "recognized_revenue": recognized_revenue_amount,
            "recognized_gross_profit_amount": recognized_gross_profit_amount,
            "contract_expected_gross_profit_amount": _number(contract_expected_gross_profit),
            "gross_margin_rate": (
                recognized_gross_profit_amount / recognized_revenue_amount
                if recognized_revenue_amount
                else 0
            ),
        },
        references=_aggregate_evidence(
            domain="delivery",
            metrics=[
                ("contract_amount", "sum(contract_amount)"),
                ("recognized_revenue", "sum(recognized_revenue)"),
                (
                    "recognized_gross_profit_amount",
                    "sum(recognized_revenue * gross_margin_rate)",
                ),
                (
                    "contract_expected_gross_profit_amount",
                    "sum(contract_amount * gross_margin_rate)",
                ),
                (
                    "gross_margin_rate",
                    "recognized_gross_profit_amount / recognized_revenue",
                ),
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
    for argument_key, column in (
        ("payment_types", FactFinanceCollection.payment_type),
        ("payment_milestones", FactFinanceCollection.payment_milestone),
        ("invoice_statuses", FactFinanceCollection.invoice_status),
    ):
        values = _list_argument(arguments, argument_key)
        if values:
            filters.append(column.in_(values))
    minimum_overdue_days = arguments.get("minimum_overdue_days")
    if minimum_overdue_days is not None:
        try:
            filters.append(
                FactFinanceCollection.overdue_days >= min(max(int(minimum_overdue_days), 0), 3650)
            )
        except (TypeError, ValueError) as exc:
            raise CapabilityError("minimum_overdue_days is malformed") from exc
    customer_query = str(arguments.get("customer_query") or "").strip()
    if customer_query:
        filters.append(
            FactFinanceCollection.customer_id.in_(
                select(DimCustomer.id).where(
                    DimCustomer.enterprise_id == claims.enterprise_id,
                    DimCustomer.display_name.ilike(f"%{customer_query[:120]}%"),
                )
            )
        )
    owner_query = str(arguments.get("owner_query") or "").strip()
    if owner_query:
        filters.append(
            FactFinanceCollection.collection_owner_person_id.in_(
                select(DimPerson.id).where(
                    DimPerson.enterprise_id == claims.enterprise_id,
                    DimPerson.display_name.ilike(f"%{owner_query[:120]}%"),
                )
            )
        )
    base_filters = [
        FactFinanceCollection.enterprise_id == claims.enterprise_id,
        FactFinanceCollection.organization_unit_id.in_(organization_ids),
        FactFinanceCollection.is_current.is_(True),
        *filters,
    ]
    statement = select(
        FactFinanceCollection.aging_bucket,
        func.count(FactFinanceCollection.id),
        func.sum(FactFinanceCollection.outstanding_amount),
    )
    rows = db.execute(
        statement.where(*base_filters).group_by(FactFinanceCollection.aging_bucket)
    ).all()
    totals = db.execute(
        select(
            func.sum(FactFinanceCollection.receivable_amount),
            func.sum(FactFinanceCollection.collected_amount),
            func.sum(FactFinanceCollection.outstanding_amount),
            func.sum(FactFinanceCollection.outstanding_amount).filter(
                FactFinanceCollection.overdue_days > 0
            ),
        ).where(*base_filters)
    ).one()
    customer = aliased(DimCustomer)
    owner = aliased(DimPerson)
    item_rows = db.execute(
        select(FactFinanceCollection, customer.display_name, owner.display_name)
        .outerjoin(customer, customer.id == FactFinanceCollection.customer_id)
        .outerjoin(owner, owner.id == FactFinanceCollection.collection_owner_person_id)
        .where(*base_filters)
        .order_by(
            FactFinanceCollection.overdue_days.desc(),
            FactFinanceCollection.outstanding_amount.desc(),
        )
        .limit(_bounded_limit(arguments))
    ).all()
    items = [
        {
            "source_record_id": row.source_record_id,
            "customer_name": customer_name,
            "project_source_record_id": row.project_source_record_id,
            "payment_type": row.payment_type,
            "payment_milestone": row.payment_milestone,
            "receivable_amount": _number(row.receivable_amount),
            "collected_amount": _number(row.collected_amount),
            "outstanding_amount": _number(row.outstanding_amount),
            "planned_collection_date": row.planned_collection_date.isoformat(),
            "actual_collection_date": (
                row.actual_collection_date.isoformat() if row.actual_collection_date else None
            ),
            "overdue_days": row.overdue_days,
            "aging_bucket": row.aging_bucket,
            "status": row.status,
            "invoice_status": row.invoice_status,
            "invoice_number": row.invoice_number,
            "collection_owner": owner_name,
            "latest_follow_up": row.latest_follow_up,
        }
        for row, customer_name, owner_name in item_rows
    ]
    return _result(
        db,
        claims,
        tool="get_collection_aging",
        domains={"collection"},
        data={
            "receivable_amount": _number(totals[0]),
            "collected_amount": _number(totals[1]),
            "outstanding_amount": _number(totals[2]),
            "overdue_amount": _number(totals[3]),
            "aging": [
                {"bucket": bucket, "count": int(count), "outstanding_amount": _number(amount)}
                for bucket, count, amount in rows
            ],
            "items": items,
        },
        references=[
            *_aggregate_evidence(
                domain="collection",
                metrics=[
                    (
                        "collection_aging",
                        "count and sum(outstanding_amount) by aging_bucket",
                    ),
                    ("receivable_amount", "sum(receivable_amount)"),
                    ("collected_amount", "sum(collected_amount)"),
                    ("outstanding_amount", "sum(outstanding_amount)"),
                    (
                        "overdue_amount",
                        "sum(outstanding_amount where overdue_days > 0)",
                    ),
                ],
                organization_ids=organization_ids,
                grouping="aging_bucket",
            ),
            *[
                {
                    "domain": "collection",
                    "source_record_id": item["source_record_id"],
                }
                for item in items
            ],
        ],
        organization_ids=organization_ids,
    )


def get_organization_performance(
    db: Session, claims: CapabilityClaims, arguments: dict[str, Any]
) -> dict[str, Any]:
    organization_ids = _scope(claims, arguments)
    organization_rows = db.execute(
        select(OrganizationUnit.id, OrganizationUnit.name).where(
            OrganizationUnit.enterprise_id == claims.enterprise_id,
            OrganizationUnit.id.in_(organization_ids),
        )
    ).all()
    opportunity_rows = db.execute(
        select(
            FactOpportunity.organization_unit_id,
            func.sum(_signed_amount_expression()),
            func.sum(FactOpportunity.expected_amount).filter(_status_expression() == "active"),
        )
        .where(
            FactOpportunity.enterprise_id == claims.enterprise_id,
            FactOpportunity.organization_unit_id.in_(organization_ids),
            FactOpportunity.is_current.is_(True),
            *_period_filters(arguments, FactOpportunity.expected_close_date),
        )
        .group_by(FactOpportunity.organization_unit_id)
    ).all()
    delivery_rows = db.execute(
        select(
            FactDelivery.organization_unit_id,
            func.sum(FactDelivery.contract_amount),
            func.sum(FactDelivery.recognized_revenue),
        )
        .where(
            FactDelivery.enterprise_id == claims.enterprise_id,
            FactDelivery.organization_unit_id.in_(organization_ids),
            FactDelivery.is_current.is_(True),
            *_period_filters(arguments, FactDelivery.planned_end_date),
        )
        .group_by(FactDelivery.organization_unit_id)
    ).all()
    collection_rows = db.execute(
        select(
            FactFinanceCollection.organization_unit_id,
            func.sum(FactFinanceCollection.receivable_amount),
            func.sum(FactFinanceCollection.collected_amount),
            func.sum(FactFinanceCollection.outstanding_amount),
        )
        .where(
            FactFinanceCollection.enterprise_id == claims.enterprise_id,
            FactFinanceCollection.organization_unit_id.in_(organization_ids),
            FactFinanceCollection.is_current.is_(True),
            *_period_filters(arguments, FactFinanceCollection.planned_collection_date),
        )
        .group_by(FactFinanceCollection.organization_unit_id)
    ).all()
    opportunities = {
        identifier: (signed, pipeline) for identifier, signed, pipeline in opportunity_rows
    }
    deliveries = {
        identifier: (contract, recognized) for identifier, contract, recognized in delivery_rows
    }
    collections = {
        identifier: (receivable, collected, outstanding)
        for identifier, receivable, collected, outstanding in collection_rows
    }
    organizations = []
    for identifier, name in organization_rows:
        opportunity = opportunities.get(identifier, (0, 0))
        delivery = deliveries.get(identifier, (0, 0))
        collection = collections.get(identifier, (0, 0, 0))
        organizations.append(
            {
                "organization_unit_id": str(identifier),
                "name": name,
                "signed_amount": _number(opportunity[0]),
                "active_pipeline_amount": _number(opportunity[1]),
                "contract_amount": _number(delivery[0]),
                "recognized_revenue": _number(delivery[1]),
                "receivable_amount": _number(collection[0]),
                "collected_amount": _number(collection[1]),
                "outstanding_amount": _number(collection[2]),
            }
        )
    organizations.sort(key=lambda item: item["collected_amount"], reverse=True)
    atomic_batch = _atomic_batch_identity(db, claims)
    return _result(
        db,
        claims,
        tool="get_organization_performance",
        domains={"opportunity", "delivery", "collection"},
        data={
            "atomic_batch_id": atomic_batch["source_batch_id"] if atomic_batch else None,
            "source_batch_id": atomic_batch["source_batch_id"] if atomic_batch else None,
            "sync_run_id": atomic_batch["sync_run_id"] if atomic_batch else None,
            "contract_version": atomic_batch["contract_version"] if atomic_batch else None,
            "source_data_as_of": atomic_batch["source_data_as_of"] if atomic_batch else None,
            "organizations": organizations,
        },
        references=[
            *_aggregate_evidence(
                domain="opportunity",
                metrics=[
                    ("signed_amount", "sum signed amount by organization_unit_id"),
                    (
                        "active_pipeline_amount",
                        "sum active expected amount by organization_unit_id",
                    ),
                ],
                organization_ids=organization_ids,
                grouping="organization_unit_id",
            ),
            *_aggregate_evidence(
                domain="delivery",
                metrics=[
                    ("contract_amount", "sum contract amount by organization_unit_id"),
                    (
                        "recognized_revenue",
                        "sum recognized revenue by organization_unit_id",
                    ),
                ],
                organization_ids=organization_ids,
                grouping="organization_unit_id",
            ),
            *_aggregate_evidence(
                domain="collection",
                metrics=[
                    ("receivable_amount", "sum receivable by organization_unit_id"),
                    ("collected_amount", "sum collected by organization_unit_id"),
                    ("outstanding_amount", "sum outstanding by organization_unit_id"),
                ],
                organization_ids=organization_ids,
                grouping="organization_unit_id",
            ),
        ],
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
    successful_runs = db.scalars(
        select(DataSyncRun)
        .where(
            DataSyncRun.enterprise_id == claims.enterprise_id,
            DataSyncRun.status == "completed",
            DataSyncRun.source_schema_version == "3.0",
            DataSyncRun.atomic_activation_status == "activated",
            DataSyncRun.dataset_version.is_not(None),
        )
        .order_by(DataSyncRun.activated_at.desc(), DataSyncRun.completed_at.desc())
        .limit(days)
    ).all()
    source_batch_ids = [
        str(row.source_batch_id) for row in successful_runs if row.source_batch_id
    ]
    if not source_batch_ids:
        return _result(
            db,
            claims,
            tool="get_daily_changes",
            domains={"opportunity", "delivery", "collection"},
            data={
                "availability": "insufficient_comparable_batches",
                "message": "尚无可比较的3.0成功原子批次",
                "snapshots": [],
                "changes": [],
            },
            references=[],
            organization_ids=organization_ids,
        )
    rows = db.scalars(
        select(DailySnapshot)
        .where(
            DailySnapshot.enterprise_id == claims.enterprise_id,
            DailySnapshot.organization_unit_id.in_(organization_ids),
            DailySnapshot.source_batch_id.in_(source_batch_ids),
        )
        .limit(len(organization_ids) * len(source_batch_ids))
    ).all()
    batch_rank = {batch_id: index for index, batch_id in enumerate(source_batch_ids)}
    snapshots = [
        {
            "organization_unit_id": str(row.organization_unit_id),
            "snapshot_date": row.snapshot_date.isoformat(),
            "source_batch_id": row.source_batch_id,
            "dataset_version": row.dataset_version,
            "metrics": row.metrics_json,
            "anomalies": row.anomalies_json,
            "source_data_as_of": row.source_data_as_of.isoformat(),
        }
        for row in rows
    ]
    snapshots.sort(
        key=lambda item: (
            batch_rank[str(item["source_batch_id"])],
            str(item["organization_unit_id"]),
        )
    )
    changes: list[dict[str, Any]] = []
    by_organization: dict[str, list[dict[str, Any]]] = {}
    for snapshot in snapshots:
        by_organization.setdefault(snapshot["organization_unit_id"], []).append(snapshot)
    for organization_id, organization_snapshots in by_organization.items():
        ordered = sorted(
            organization_snapshots,
            key=lambda item: batch_rank[str(item["source_batch_id"])],
        )
        for current, previous in zip(ordered, ordered[1:], strict=False):
            metric_deltas: dict[str, float] = {}
            for key, current_value in current["metrics"].items():
                previous_value = previous["metrics"].get(key)
                if (
                    not key.startswith("_")
                    and isinstance(current_value, (int, float))
                    and isinstance(previous_value, (int, float))
                ):
                    metric_deltas[key] = float(current_value) - float(previous_value)
            changes.append(
                {
                    "organization_unit_id": organization_id,
                    "current_snapshot_date": current["snapshot_date"],
                    "previous_snapshot_date": previous["snapshot_date"],
                    "current_source_batch_id": current["source_batch_id"],
                    "previous_source_batch_id": previous["source_batch_id"],
                    "metric_deltas": metric_deltas,
                }
            )
    return _result(
        db,
        claims,
        tool="get_daily_changes",
        domains={"opportunity", "delivery", "collection"},
        data={
            "availability": "ready" if len(source_batch_ids) >= 2 else "single_batch_only",
            "dataset_versions": [
                str(row.dataset_version) for row in successful_runs if row.dataset_version
            ],
            "source_batch_ids": source_batch_ids,
            "snapshots": snapshots,
            "changes": changes,
        },
        references=[
            {
                "domain": "daily_snapshot",
                "organization_unit_id": row["organization_unit_id"],
                "snapshot_date": row["snapshot_date"],
                "source_batch_id": row["source_batch_id"],
                "dataset_version": row["dataset_version"],
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
    return _execute_registered_tool(db, claims, tool_name, arguments)


def _execute_registered_tool(
    db: Session,
    claims: CapabilityClaims,
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    configuration = effective_tool(db, claims.enterprise_id, tool_name)
    if configuration is None or not configuration["is_enabled"]:
        raise CapabilityError("tool is disabled by enterprise configuration")
    spec = registered_spec(db, claims.enterprise_id, tool_name)
    if spec is None:
        raise CapabilityError("unknown tool")
    bounded_arguments = dict(arguments)
    if "limit" in spec.parameters:
        try:
            bounded_arguments["limit"] = min(
                max(int(bounded_arguments.get("limit", configuration["max_rows"])), 1),
                int(configuration["max_rows"]),
            )
        except (TypeError, ValueError) as exc:
            raise CapabilityError("tool limit is malformed") from exc
    handler = TOOLS.get(tool_name)
    if handler is not None:
        return handler(db, claims, bounded_arguments)

    if spec.source_type != "composite" or not spec.component_tools:
        raise CapabilityError("unknown tool")

    component_results: list[dict[str, Any]] = []
    freshness: list[dict[str, Any]] = []
    freshness_keys: set[tuple[str, str | None, str | None]] = set()
    evidence: list[dict[str, Any]] = []
    for component_name in spec.component_tools:
        component_spec = registered_spec(db, claims.enterprise_id, component_name)
        if component_spec is None or component_spec.source_type != "built_in":
            raise CapabilityError("composite tool has an invalid dependency")
        component_arguments = {
            key: value
            for key, value in bounded_arguments.items()
            if key in component_spec.parameters or key == "organization_unit_ids"
        }
        result = _execute_registered_tool(
            db,
            claims,
            component_name,
            component_arguments,
        )
        component_configuration = effective_tool(db, claims.enterprise_id, component_name)
        if component_configuration is None:
            raise CapabilityError("composite tool dependency is unavailable")
        component_results.append(
            {
                "tool": component_name,
                "display_name": component_configuration["display_name"],
                "data": result.get("data", {}),
            }
        )
        for row in result.get("freshness", []):
            key = (
                str(row.get("domain")),
                row.get("source_data_as_of"),
                row.get("dataset_version"),
            )
            if key not in freshness_keys:
                freshness_keys.add(key)
                freshness.append(row)
        for row in result.get("evidence", []):
            evidence.append({**row, "component_tool": component_name})

    return {
        "tool": tool_name,
        "data": {"components": component_results},
        "freshness": freshness,
        "scope": {
            "organization_unit_ids": sorted(
                str(value) for value in _scope(claims, bounded_arguments)
            )
        },
        "evidence": evidence[:100],
    }
