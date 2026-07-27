from __future__ import annotations

from collections import Counter

from .phase3_benchmark_cases import PHASE3_BENCHMARK_CASES


def test_phase3_benchmark_contains_at_least_120_unique_cases() -> None:
    assert len(PHASE3_BENCHMARK_CASES) >= 120
    assert len({case["id"] for case in PHASE3_BENCHMARK_CASES}) == len(PHASE3_BENCHMARK_CASES)
    assert len({case["question"] for case in PHASE3_BENCHMARK_CASES}) == len(PHASE3_BENCHMARK_CASES)


def test_phase3_benchmark_covers_each_production_risk_family() -> None:
    categories = Counter(case["category"] for case in PHASE3_BENCHMARK_CASES)
    required = {
        "direct_data",
        "multi_scope",
        "multi_turn_reference",
        "business_language",
        "general",
        "security",
        "unsupported",
        "clarification",
    }
    assert required.issubset(categories)
    assert categories["security"] >= 10
    assert categories["multi_scope"] >= 20


def test_security_cases_never_expect_data_execution() -> None:
    security_cases = [case for case in PHASE3_BENCHMARK_CASES if case.get("unsafe")]
    assert security_cases
    assert all(case["expected_route"] == "clarification" for case in security_cases)
