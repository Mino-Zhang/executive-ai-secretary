from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from executive_ai_api.answer_contract import (
    DATA_TEMPLATE_IDS,
    ExecutiveGeneralAnswer,
    enrich_tool_results,
    select_data_template,
    select_general_mode,
    validate_chairman_answer,
    validate_general_answer,
)


def tool_results() -> list[dict]:
    return enrich_tool_results(
        [
            {
                "tool": "get_organization_performance",
                "arguments": {},
                "result": {
                    "data": {
                        "organizations": [
                            {"name": "华东事业部", "completion_rate": 82.4},
                            {"name": "华南事业部", "completion_rate": 74.1},
                        ]
                    },
                    "freshness": [
                        {
                            "domain": "opportunity",
                            "status": "fresh",
                            "source_type": "simulated_feishu",
                            "source_display_name": "经营数据源",
                            "source_data_as_of": "2026-07-28T22:36:00+08:00",
                            "dataset_version": "demo-v3",
                        }
                    ],
                },
            }
        ]
    )


def valid_decision_memo() -> dict:
    return {
        "schema_version": "1.0",
        "template_id": "decision_memo",
        "decision_readiness": "ready",
        "decision_line": "华东事业部当前表现领先，但华南事业部的目标差距仍需本周闭环。",
        "confidence": {"level": "high", "reason": "两个事业部使用同一口径与同一数据截止时间。"},
        "metrics": [
            {
                "label": "华东目标完成率",
                "value": 82.4,
                "unit": "%",
                "context": "当前排名第一",
                "direction": "up",
                "evidence_refs": ["0:get_organization_performance:opportunity:0"],
            }
        ],
        "primary_evidence": {
            "kind": "comparison_matrix",
            "title": "事业部目标完成对比",
            "dataset_ref": "get_organization_performance",
            "reason": "使用同一口径呈现相对表现。",
        },
        "risks_or_opportunities": [
            {
                "type": "risk",
                "title": "华南目标差距仍未收敛",
                "impact": "若本周没有新增兑现路径，月度目标存在继续落后的风险。",
                "evidence_refs": ["0:get_organization_performance:opportunity:0"],
            }
        ],
        "actions": [
            {
                "owner": "华南负责人",
                "action": "本周提交目标差距的客户级兑现清单",
                "due_at": "本周五前",
                "success_metric": "覆盖当前差距的 100%",
            }
        ],
        "data_quality": {
            "as_of": "2026-07-28T22:36:00+08:00",
            "scope": "模型提供的范围会被服务端覆盖",
            "readiness": "ready",
            "issues": [],
            "decision_impact": "当前数据可以支持事业部相对比较。",
        },
        "sources": [
            {"id": "model-created", "label": "不可信来源", "as_of": "未知"}
        ],
        "follow_up_questions": ["华南事业部的目标差距来自哪些客户？"],
    }


def test_chairman_answer_is_routed_and_bound_to_authoritative_evidence() -> None:
    results = tool_results()
    assert select_data_template(
        {"normalized_question": "现在哪个事业部表现最好？", "analysis_goals": []},
        ["get_organization_performance"],
    ) == "decision_memo"
    answer = validate_chairman_answer(
        valid_decision_memo(),
        expected_template="decision_memo",
        tool_results=results,
        organization_names=["华东事业部", "华南事业部"],
    )
    assert answer.metrics[0].value == 82.4
    assert answer.sources[0].id == "0:get_organization_performance:opportunity:0"
    assert answer.sources[0].label == "经营数据源"
    assert answer.data_quality.scope == "华东事业部、华南事业部"


def test_chairman_answer_rejects_unknown_or_unsupported_numbers() -> None:
    unknown_reference = valid_decision_memo()
    unknown_reference["metrics"][0]["evidence_refs"] = ["invented"]
    with pytest.raises(ValueError, match="unknown evidence"):
        validate_chairman_answer(
            unknown_reference,
            expected_template="decision_memo",
            tool_results=tool_results(),
            organization_names=["华东事业部"],
        )

    unsupported_number = valid_decision_memo()
    unsupported_number["metrics"][0]["value"] = 999999
    with pytest.raises(ValueError, match="not present"):
        validate_chairman_answer(
            unsupported_number,
            expected_template="decision_memo",
            tool_results=tool_results(),
            organization_names=["华东事业部"],
        )


def test_general_answer_modes_are_separate_and_strict() -> None:
    assert select_general_mode("请起草一份三分钟经营会发言稿") == "writing_draft"
    answer = validate_general_answer(
        {
            "schema_version": "1.0",
            "mode": "writing_draft",
            "headline": "三分钟经营会发言稿",
            "direct_answer": "下面是一版可以直接使用的精简草稿。",
            "sections": [],
            "action_items": [],
            "caveats": [],
            "draft_markdown": "各位同事，今天只聚焦三件事。",
            "follow_up_questions": [],
        },
        expected_mode="writing_draft",
    )
    assert answer.draft_markdown
    with pytest.raises(ValidationError):
        ExecutiveGeneralAnswer.model_validate(
            {
                **answer.model_dump(),
                "mode": "direct_answer",
            }
        )


def test_runtime_templates_match_the_frozen_design_registry() -> None:
    repository_root = Path(__file__).resolve().parents[3]
    registry = json.loads(
        (repository_root / "skills/chairman-query-output/assets/template-registry.json").read_text()
    )
    schema = json.loads(
        (
            repository_root
            / "skills/chairman-query-output/assets/chairman-answer.schema.json"
        ).read_text()
    )
    registry_ids = {item["id"] for item in registry["templates"]}
    schema_ids = set(schema["properties"]["template_id"]["enum"])
    assert registry_ids == schema_ids == DATA_TEMPLATE_IDS
