from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import McpToolConfig


@dataclass(frozen=True)
class McpToolSpec:
    name: str
    display_name: str
    description: str
    category: str
    domains: tuple[str, ...]
    parameters: dict[str, dict[str, Any]]
    default_limit: int = 50
    default_timeout_seconds: int = 20


COMMON_PERIOD_PARAMETERS = {
    "period_start": {
        "type": "string",
        "format": "date",
        "description": "查询开始日期，ISO 8601，例如 2026-07-01",
    },
    "period_end": {
        "type": "string",
        "format": "date",
        "description": "查询结束日期，ISO 8601，例如 2026-07-31",
    },
}


MCP_TOOL_SPECS: dict[str, McpToolSpec] = {
    "list_query_scopes": McpToolSpec(
        name="list_query_scopes",
        display_name="可查询范围",
        description="读取当前用户获准分析的事业部，不查询经营事实。",
        category="权限与范围",
        domains=(),
        parameters={},
        default_limit=20,
    ),
    "get_overall_business": McpToolSpec(
        name="get_overall_business",
        display_name="整体经营概览",
        description="汇总商机、交付、应收、回款和逾期等核心经营指标。",
        category="综合经营",
        domains=("opportunity", "delivery", "collection", "target"),
        parameters=COMMON_PERIOD_PARAMETERS,
    ),
    "get_target_completion": McpToolSpec(
        name="get_target_completion",
        display_name="目标完成情况",
        description="按月、季度或年度口径核对目标、实际值和完成率。",
        category="目标与计划",
        domains=("target", "opportunity", "collection"),
        parameters={
            "period_type": {
                "type": "string",
                "enum": ["month", "quarter", "year"],
                "description": "目标周期口径",
            },
            "period_start": COMMON_PERIOD_PARAMETERS["period_start"],
        },
    ),
    "get_opportunity_funnel": McpToolSpec(
        name="get_opportunity_funnel",
        display_name="商机漏斗",
        description="按阶段汇总商机数量、金额和加权金额。",
        category="销售与商机",
        domains=("opportunity",),
        parameters={
            **COMMON_PERIOD_PARAMETERS,
            "statuses": {
                "type": "array",
                "items": {"type": "string", "enum": ["active", "stalled", "won", "lost", "paused"]},
                "description": "商机状态过滤",
            },
        },
    ),
    "get_sales_forecast": McpToolSpec(
        name="get_sales_forecast",
        display_name="销售预测",
        description="计算加权商机预测，并返回影响最大的商机。",
        category="销售与商机",
        domains=("opportunity",),
        parameters={
            **COMMON_PERIOD_PARAMETERS,
            "min_probability": {"type": "integer", "minimum": 0, "maximum": 100},
            "statuses": {
                "type": "array",
                "items": {"type": "string", "enum": ["active", "stalled", "won", "lost", "paused"]},
            },
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
        },
    ),
    "get_customer_status": McpToolSpec(
        name="get_customer_status",
        display_name="客户经营情况",
        description="查询客户应收、逾期和风险排序，支持脱敏客户名称检索。",
        category="客户与回款",
        domains=("opportunity", "delivery", "collection"),
        parameters={
            "customer_query": {"type": "string", "maxLength": 120},
            "only_overdue": {"type": "boolean"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
        },
    ),
    "get_delivery_status": McpToolSpec(
        name="get_delivery_status",
        display_name="项目交付情况",
        description="查询项目进度、风险、延期天数和当前里程碑。",
        category="项目与交付",
        domains=("delivery",),
        parameters={
            **COMMON_PERIOD_PARAMETERS,
            "project_query": {"type": "string", "maxLength": 120},
            "statuses": {"type": "array", "items": {"type": "string"}},
            "risk_levels": {
                "type": "array",
                "items": {"type": "string", "enum": ["normal", "attention", "delayed", "critical"]},
            },
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
        },
    ),
    "get_finance_margin": McpToolSpec(
        name="get_finance_margin",
        display_name="收入与毛利",
        description="汇总合同金额、毛利金额和综合毛利率。",
        category="财务经营",
        domains=("delivery", "collection"),
        parameters=COMMON_PERIOD_PARAMETERS,
    ),
    "get_collection_aging": McpToolSpec(
        name="get_collection_aging",
        display_name="回款与账龄",
        description="按账龄汇总未回款金额，并可限定客户或逾期区间。",
        category="客户与回款",
        domains=("collection",),
        parameters={
            "customer_query": {"type": "string", "maxLength": 120},
            "aging_buckets": {"type": "array", "items": {"type": "string"}},
            "minimum_overdue_days": {"type": "integer", "minimum": 0, "maximum": 3650},
        },
    ),
    "get_organization_performance": McpToolSpec(
        name="get_organization_performance",
        display_name="事业部表现",
        description="比较获准范围内各事业部的回款和未回款表现。",
        category="组织分析",
        domains=("opportunity", "delivery", "collection", "target"),
        parameters=COMMON_PERIOD_PARAMETERS,
    ),
    "get_daily_changes": McpToolSpec(
        name="get_daily_changes",
        display_name="经营变化",
        description="读取最近经营快照，识别日度变化和异常事项。",
        category="综合经营",
        domains=("opportunity", "delivery", "collection"),
        parameters={
            "days": {"type": "integer", "minimum": 1, "maximum": 31},
        },
        default_limit=14,
    ),
}


def configured_rows(db: Session, enterprise_id: uuid.UUID) -> dict[str, McpToolConfig]:
    return {
        row.tool_name: row
        for row in db.scalars(
            select(McpToolConfig).where(McpToolConfig.enterprise_id == enterprise_id)
        ).all()
    }


def effective_catalog(db: Session, enterprise_id: uuid.UUID) -> list[dict[str, Any]]:
    configured = configured_rows(db, enterprise_id)
    catalog: list[dict[str, Any]] = []
    for spec in MCP_TOOL_SPECS.values():
        row = configured.get(spec.name)
        catalog.append(
            {
                "tool_name": spec.name,
                "display_name": row.display_name if row else spec.display_name,
                "description": row.description if row else spec.description,
                "category": spec.category,
                "domains": list(spec.domains),
                "parameters": spec.parameters,
                "is_enabled": row.is_enabled if row else True,
                "planner_enabled": row.planner_enabled if row else True,
                "timeout_seconds": row.timeout_seconds if row else spec.default_timeout_seconds,
                "max_rows": row.max_rows if row else spec.default_limit,
                "operator_note": row.operator_note if row else None,
                "configured": row is not None,
                "updated_at": row.updated_at if row else None,
            }
        )
    return catalog


def planner_catalog(db: Session, enterprise_id: uuid.UUID) -> list[dict[str, Any]]:
    return [
        item
        for item in effective_catalog(db, enterprise_id)
        if item["is_enabled"] and item["planner_enabled"]
    ]


def effective_tool(
    db: Session, enterprise_id: uuid.UUID, tool_name: str
) -> dict[str, Any] | None:
    return next(
        (item for item in effective_catalog(db, enterprise_id) if item["tool_name"] == tool_name),
        None,
    )
