from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

os.environ.update(
    {
        "APP_ENV": "test",
        "APP_MODE": "demo",
        "DATABASE_URL": "sqlite+pysqlite:///:memory:",
        "SESSION_SECRET": "worker-test-session-secret-at-least-32-chars",
        "CSRF_SECRET": "worker-test-csrf-secret-at-least-32-chars",
        "AUDIT_HMAC_KEY": "worker-test-audit-key-at-least-32-characters",
    }
)

from executive_ai_api.mcp_registry import MCP_TOOL_SPECS

from executive_ai_worker.assistant_orchestrator import (
    _bounded_conversation_context,
    _deterministic_period_arguments,
    _deterministic_tools,
    _fallback_route,
    _normalize_calls,
)
from executive_ai_worker.scheduler import next_cron_time


def _tool(tool_name: str, *, max_rows: int = 50) -> dict[str, object]:
    spec = MCP_TOOL_SPECS[tool_name]
    return {
        "tool_name": tool_name,
        "description": spec.description,
        "parameters": spec.parameters,
        "max_rows": max_rows,
        "timeout_seconds": 12,
    }


def test_deterministic_route_fallback_separates_business_and_general_questions() -> None:
    assert _fallback_route("本月整体回款情况") == "data"
    assert _fallback_route("解释一下什么是现金转换周期") == "general"
    assert _fallback_route("帮我整理一份董事会沟通框架") == "general"


def test_planner_calls_are_allowlisted_bounded_and_server_scoped() -> None:
    organization_id = uuid.uuid4()
    calls = _normalize_calls(
        [
            {
                "tool": "get_delivery_status",
                "arguments": {
                    "limit": 9999,
                    "risk_levels": ["delayed", "not-allowed"],
                    "arbitrary_sql": "select * from secrets",
                    "organization_unit_ids": [str(uuid.uuid4())],
                },
                "reason": "核对延期项目",
            },
            {
                "tool": "unknown_tool",
                "arguments": {"url": "https://untrusted.example"},
            },
        ],
        "哪些项目延期",
        [_tool("get_delivery_status", max_rows=25)],
        {organization_id},
    )

    assert len(calls) == 1
    assert calls[0]["tool"] == "get_delivery_status"
    assert calls[0]["arguments"] == {
        "limit": 25,
        "risk_levels": ["delayed"],
        "organization_unit_ids": [str(organization_id)],
    }
    assert calls[0]["timeout_seconds"] == 12


def test_empty_or_invalid_plan_uses_only_an_available_registered_tool() -> None:
    organization_id = uuid.uuid4()
    calls = _normalize_calls(
        [{"tool": "not_registered", "arguments": {}}],
        "本月回款情况",
        [_tool("get_collection_aging")],
        {organization_id},
    )
    assert [item["tool"] for item in calls] == ["get_collection_aging"]
    assert calls[0]["arguments"]["organization_unit_ids"] == [str(organization_id)]


def test_deterministic_fast_path_only_accepts_explicit_registered_intents() -> None:
    allowed = {"get_collection_aging", "get_organization_performance"}
    assert _deterministic_tools("本月最需要关注哪些回款风险？", allowed) == [
        "get_collection_aging"
    ]
    assert _deterministic_tools("比较六个事业部的回款差距", allowed) == [
        "get_organization_performance"
    ]
    assert _deterministic_tools("再展开看看", allowed) == []
    assert _deterministic_tools("哪些项目延期", allowed) == []


def test_deterministic_periods_are_timezone_ready_and_stable() -> None:
    reference = datetime(2026, 7, 28, tzinfo=UTC).date()
    assert _deterministic_period_arguments(
        "本月回款风险", "Asia/Shanghai", today=reference
    ) == {"period_start": "2026-07-01", "period_end": "2026-07-31"}
    assert _deterministic_period_arguments(
        "上月回款风险", "Asia/Shanghai", today=reference
    ) == {"period_start": "2026-06-01", "period_end": "2026-06-30"}
    assert _deterministic_period_arguments(
        "本季度回款风险", "Asia/Shanghai", today=reference
    ) == {"period_start": "2026-07-01", "period_end": "2026-09-30"}


def test_conversation_context_preserves_recent_turns_with_a_total_budget() -> None:
    rows = [
        ("user", "较早问题" * 400),
        ("assistant", "较早长回答" * 500),
        ("user", "最近问题"),
        ("assistant", "最近回答"),
    ]
    context = _bounded_conversation_context(rows, total_characters=100)

    assert context[-2:] == [
        {"role": "user", "content": "最近问题"},
        {"role": "assistant", "content": "最近回答"},
    ]
    assert sum(len(item["content"]) for item in context) <= 100


def test_daily_schedule_uses_configured_timezone_and_next_window() -> None:
    after = datetime(2026, 7, 26, 17, 59, tzinfo=UTC)
    next_run = next_cron_time("0 2 * * *", "Asia/Shanghai", after)
    assert next_run == datetime(2026, 7, 26, 18, 0, tzinfo=UTC)
