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

from executive_ai_api.harness_config import default_harness_config, match_fast_rule
from executive_ai_api.mcp_registry import MCP_TOOL_SPECS
from executive_ai_api.query_spec import normalize_query_spec

from executive_ai_worker.assistant_orchestrator import (
    _bounded_conversation_context,
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


def test_empty_or_invalid_plan_does_not_guess_a_tool() -> None:
    organization_id = uuid.uuid4()
    calls = _normalize_calls(
        [{"tool": "not_registered", "arguments": {}}],
        "本月回款情况",
        [_tool("get_collection_aging")],
        {organization_id},
    )
    assert calls == []


def test_fast_path_is_driven_by_versioned_configuration() -> None:
    config = default_harness_config()
    rule = match_fast_rule("本月最需要关注哪些回款风险？", config)
    assert rule is not None
    assert rule["route"] == "data"
    assert match_fast_rule("帮我润色这段话", config)["route"] == "general"
    assert match_fast_rule("解释现金转换周期", config) is None


def test_query_spec_keeps_model_scope_out_of_the_authority_boundary() -> None:
    authorized_id = uuid.uuid4()
    spec = normalize_query_spec(
        {
            "normalized_question": "本月回款风险",
            "metrics": ["collection_amount"],
            "organization_scope": {
                "mode": "all_authorized",
                "organization_unit_ids": [str(uuid.uuid4())],
            },
        },
        question="本月回款风险",
        organization_scope={
            "mode": "selected",
            "organization_unit_ids": [str(authorized_id)],
        },
    )
    assert spec["organization_scope"] == {
        "mode": "selected",
        "organization_unit_ids": [str(authorized_id)],
    }


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
