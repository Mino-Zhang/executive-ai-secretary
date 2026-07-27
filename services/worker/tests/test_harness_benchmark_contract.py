from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path

os.environ.update(
    {
        "APP_ENV": "test",
        "APP_MODE": "demo",
        "DATABASE_URL": "sqlite+pysqlite:///:memory:",
        "SESSION_SECRET": "benchmark-test-session-secret-at-least-32-chars",
        "CSRF_SECRET": "benchmark-test-csrf-secret-at-least-32-chars",
        "AUDIT_HMAC_KEY": "benchmark-test-audit-key-at-least-32-characters",
    }
)

from executive_ai_api.mcp_registry import MCP_TOOL_SPECS


def test_executive_query_benchmark_is_balanced_and_not_a_single_metric_fixture() -> None:
    path = Path(__file__).parent / "benchmarks" / "executive_queries.json"
    cases = json.loads(path.read_text(encoding="utf-8"))
    assert len(cases) >= 40
    assert len({item["id"] for item in cases}) == len(cases)
    assert len({item["query"].strip().lower() for item in cases}) == len(cases)

    routes = Counter(item["route"] for item in cases)
    dimensions = Counter(item["dimension"] for item in cases)
    assert routes["data"] >= 20
    assert routes["general"] >= 10
    assert dimensions["multi_tool"] >= 4
    assert sum(count for name, count in dimensions.items() if name.endswith("attack")) >= 1
    assert sum(count for name, count in dimensions.items() if name in {
        "authorization_attack",
        "sql_injection",
        "network_boundary",
        "fabrication_request",
        "prompt_exfiltration",
        "tool_injection",
    }) >= 6

    registered = set(MCP_TOOL_SPECS)
    for item in cases:
        assert item["route"] in {"data", "general", "clarification"}
        assert set(item["tools"]).issubset(registered)
        if item["route"] == "general":
            assert item["tools"] == []
