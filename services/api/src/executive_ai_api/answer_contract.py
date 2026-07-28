from __future__ import annotations

import copy
import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ANSWER_CONTRACT_VERSION = "1.0"
DATA_TEMPLATE_IDS = {
    "executive_pulse",
    "target_gap",
    "risk_action",
    "top_opportunities",
    "decision_memo",
}
GENERAL_MODES = {"direct_answer", "analysis_memo", "action_plan", "writing_draft"}


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Confidence(ContractModel):
    level: Literal["high", "medium", "low"]
    reason: str = Field(min_length=4, max_length=160)


class Metric(ContractModel):
    label: str = Field(min_length=1, max_length=24)
    value: int | float | str
    unit: str = Field(max_length=12)
    context: str = Field(max_length=80)
    direction: Literal["up", "down", "flat", "unknown"] = "unknown"
    evidence_refs: list[str] = Field(min_length=1, max_length=8)


class PrimaryEvidence(ContractModel):
    kind: Literal[
        "progress",
        "bar",
        "ranked_bar",
        "waterfall",
        "timeline",
        "table",
        "comparison_matrix",
    ]
    title: str = Field(min_length=1, max_length=60)
    dataset_ref: str = Field(min_length=1, max_length=120)
    reason: str = Field(max_length=120)


class RiskOpportunity(ContractModel):
    type: Literal["risk", "opportunity"]
    title: str = Field(min_length=1, max_length=50)
    impact: str = Field(max_length=160)
    evidence_refs: list[str] = Field(min_length=1, max_length=8)


class ExecutiveAction(ContractModel):
    owner: str = Field(min_length=1, max_length=40)
    action: str = Field(min_length=4, max_length=120)
    due_at: str = Field(min_length=1, max_length=40)
    success_metric: str = Field(min_length=2, max_length=100)


class DataQualityIssue(ContractModel):
    dimension: Literal[
        "completeness",
        "uniqueness",
        "consistency",
        "timeliness",
        "validity",
        "accuracy",
        "definition",
    ]
    severity: Literal["critical", "high", "medium", "low"]
    detail: str = Field(max_length=160)


class DataQuality(ContractModel):
    as_of: str
    scope: str = Field(min_length=1, max_length=120)
    readiness: Literal["ready", "conditional", "not_ready"]
    issues: list[DataQualityIssue] = Field(default_factory=list, max_length=4)
    decision_impact: str = Field(max_length=180)


class AnswerSource(ContractModel):
    id: str
    label: str
    as_of: str
    dataset_version: str | None = None


class ChairmanAnswer(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    template_id: Literal[
        "executive_pulse",
        "target_gap",
        "risk_action",
        "top_opportunities",
        "decision_memo",
    ]
    decision_readiness: Literal["ready", "conditional", "not_ready"]
    decision_line: str = Field(min_length=8, max_length=120)
    confidence: Confidence
    metrics: list[Metric] = Field(default_factory=list, max_length=3)
    primary_evidence: PrimaryEvidence | None = None
    risks_or_opportunities: list[RiskOpportunity] = Field(default_factory=list, max_length=3)
    actions: list[ExecutiveAction] = Field(default_factory=list, max_length=4)
    data_quality: DataQuality
    sources: list[AnswerSource] = Field(min_length=1)
    follow_up_questions: list[str] = Field(default_factory=list, max_length=3)


class GeneralSection(ContractModel):
    title: str = Field(min_length=1, max_length=60)
    content: str = Field(min_length=1, max_length=4000)


class GeneralAction(ContractModel):
    action: str = Field(min_length=2, max_length=160)
    rationale: str = Field(default="", max_length=240)


class ExecutiveGeneralAnswer(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    mode: Literal["direct_answer", "analysis_memo", "action_plan", "writing_draft"]
    headline: str = Field(min_length=4, max_length=120)
    direct_answer: str = Field(min_length=4, max_length=1200)
    sections: list[GeneralSection] = Field(default_factory=list, max_length=4)
    action_items: list[GeneralAction] = Field(default_factory=list, max_length=4)
    caveats: list[str] = Field(default_factory=list, max_length=3)
    draft_markdown: str | None = Field(default=None, max_length=16000)
    capability_notice: str | None = Field(default=None, max_length=240)
    follow_up_questions: list[str] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def writing_mode_has_draft(self):
        if self.mode == "writing_draft" and not (self.draft_markdown or "").strip():
            raise ValueError("writing_draft requires draft_markdown")
        if self.mode != "writing_draft" and self.draft_markdown:
            raise ValueError("draft_markdown is only allowed for writing_draft")
        return self


class ClarificationAnswer(ContractModel):
    question: str = Field(min_length=2, max_length=240)
    options: list[dict[str, Any]] = Field(default_factory=list, max_length=20)


class AssistantOutputEnvelope(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["data", "general", "clarification"]
    body: dict[str, Any]


def extract_json_object(text: str) -> dict[str, Any]:
    value = text.strip()
    if value.startswith("```"):
        value = value.split("\n", 1)[-1]
        value = value.rsplit("```", 1)[0].strip()
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("answer must be a JSON object")
    return parsed


def select_data_template(query_spec: dict[str, Any], tool_names: list[str]) -> str:
    question = str(query_spec.get("normalized_question") or "")
    goals = " ".join(str(item) for item in query_spec.get("analysis_goals", []))
    haystack = f"{question} {goals}".casefold()
    tools = set(tool_names)
    if (
        "get_organization_performance" in tools
        or any(term in haystack for term in ("对比", "比较", "哪个事业部", "资源", "取舍"))
    ):
        return "decision_memo"
    risk_tools = {
        "get_delivery_status",
        "get_collection_aging",
        "get_finance_margin",
    }
    if tools.intersection(risk_tools) or any(
        term in haystack for term in ("风险", "延期", "逾期", "异常", "下滑", "卡点")
    ):
        return "risk_action"
    if tools.intersection({"get_target_completion", "get_sales_forecast"}) or any(
        term in haystack for term in ("目标", "达标", "差距", "缺口", "兑现", "覆盖")
    ):
        return "target_gap"
    if any(term in haystack for term in ("top", "前五", "最大", "排名", "哪些客户", "哪些商机")):
        return "top_opportunities"
    return "executive_pulse"


def select_general_mode(question: str) -> str:
    normalized = question.casefold()
    if any(term in normalized for term in ("起草", "撰写", "改写", "写一", "邮件", "发言稿")):
        return "writing_draft"
    if any(term in normalized for term in ("计划", "步骤", "怎么做", "路线", "行动")):
        return "action_plan"
    if any(term in normalized for term in ("分析", "比较", "权衡", "为什么", "判断")):
        return "analysis_memo"
    return "direct_answer"


def enrich_tool_results(tool_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    enriched = copy.deepcopy(tool_results)
    for call_index, item in enumerate(enriched):
        tool = str(item.get("tool") or "tool")
        result = item.get("result") if isinstance(item.get("result"), dict) else {}
        for domain_index, row in enumerate(result.get("freshness", [])):
            if not isinstance(row, dict):
                continue
            row["evidence_id"] = (
                f"{call_index}:{tool}:{row.get('domain', 'data')}:{domain_index}"
            )
        result["dataset_ref"] = tool
    return enriched


def _evidence_ids(tool_results: list[dict[str, Any]]) -> set[str]:
    return {
        str(row["evidence_id"])
        for item in tool_results
        for row in (item.get("result") or {}).get("freshness", [])
        if isinstance(row, dict) and row.get("evidence_id")
    }


def _authoritative_sources(tool_results: list[dict[str, Any]]) -> list[AnswerSource]:
    sources: dict[str, AnswerSource] = {}
    for item in tool_results:
        for row in (item.get("result") or {}).get("freshness", []):
            if not isinstance(row, dict) or not row.get("evidence_id"):
                continue
            source = AnswerSource(
                id=str(row["evidence_id"]),
                label=str(row.get("source_display_name") or "经营数据源"),
                as_of=str(row.get("source_data_as_of") or "未知"),
                dataset_version=(
                    str(row["dataset_version"]) if row.get("dataset_version") else None
                ),
            )
            sources[source.id] = source
    return list(sources.values())


def _flatten_numbers(value: Any) -> list[Decimal]:
    values: list[Decimal] = []
    if isinstance(value, bool) or value is None:
        return values
    if isinstance(value, (int, float, Decimal)):
        try:
            values.append(Decimal(str(value)))
        except InvalidOperation:
            pass
        return values
    if isinstance(value, str):
        for match in re.findall(r"-?\d+(?:\.\d+)?", value.replace(",", "")):
            try:
                values.append(Decimal(match))
            except InvalidOperation:
                continue
        return values
    if isinstance(value, dict):
        for item in value.values():
            values.extend(_flatten_numbers(item))
    elif isinstance(value, list):
        for item in value:
            values.extend(_flatten_numbers(item))
    return values


def _metric_number(value: int | float | str) -> Decimal | None:
    numbers = _flatten_numbers(value)
    return numbers[0] if numbers else None


def _metric_is_supported(metric: Metric, tool_results: list[dict[str, Any]]) -> bool:
    target = _metric_number(metric.value)
    if target is None:
        return True
    supported = _flatten_numbers(
        [item.get("result", {}).get("data", {}) for item in tool_results]
    )
    for candidate in supported:
        if abs(candidate - target) <= Decimal("0.0001"):
            return True
        if abs(candidate * 100 - target) <= Decimal("0.01"):
            return True
        if abs(candidate - target * 100) <= Decimal("0.01"):
            return True
    return False


def _authoritative_quality(
    tool_results: list[dict[str, Any]],
    organization_names: list[str],
    requested: DataQuality,
) -> DataQuality:
    freshness = [
        row
        for item in tool_results
        for row in (item.get("result") or {}).get("freshness", [])
        if isinstance(row, dict)
    ]
    timestamps = [
        str(row["source_data_as_of"])
        for row in freshness
        if row.get("source_data_as_of")
    ]
    statuses = {str(row.get("status") or "unknown") for row in freshness}
    readiness: Literal["ready", "conditional", "not_ready"] = requested.readiness
    if statuses.intersection({"failed", "unavailable"}):
        readiness = "not_ready"
    elif statuses.difference({"fresh"}) and readiness == "ready":
        readiness = "conditional"
    issues = list(requested.issues)
    has_timeliness_issue = any(item.dimension == "timeliness" for item in issues)
    if statuses.difference({"fresh"}) and not has_timeliness_issue:
        issues.append(
            DataQualityIssue(
                dimension="timeliness",
                severity="medium" if readiness != "not_ready" else "high",
                detail="部分经营数据未处于最新成功版本。",
            )
        )
    scope = "全部授权事业部" if not organization_names else "、".join(organization_names[:6])
    return DataQuality(
        as_of=min(timestamps) if timestamps else requested.as_of,
        scope=scope[:120],
        readiness=readiness,
        issues=issues[:4],
        decision_impact=requested.decision_impact,
    )


def validate_chairman_answer(
    raw: dict[str, Any],
    *,
    expected_template: str,
    tool_results: list[dict[str, Any]],
    organization_names: list[str],
) -> ChairmanAnswer:
    answer = ChairmanAnswer.model_validate(raw)
    if answer.template_id != expected_template:
        raise ValueError(f"template_id must be {expected_template}")
    valid_refs = _evidence_ids(tool_results)
    if not valid_refs:
        raise ValueError("no authoritative evidence is available")
    for metric in answer.metrics:
        if not set(metric.evidence_refs).issubset(valid_refs):
            raise ValueError(f"metric {metric.label} references unknown evidence")
        if not _metric_is_supported(metric, tool_results):
            raise ValueError(f"metric {metric.label} is not present in authorized results")
    for item in answer.risks_or_opportunities:
        if not set(item.evidence_refs).issubset(valid_refs):
            raise ValueError(f"{item.title} references unknown evidence")
    if answer.primary_evidence and answer.primary_evidence.dataset_ref not in {
        str(item.get("tool")) for item in tool_results
    }:
        raise ValueError("primary evidence references an unknown dataset")
    answer.sources = _authoritative_sources(tool_results)
    answer.data_quality = _authoritative_quality(
        tool_results, organization_names, answer.data_quality
    )
    if answer.data_quality.readiness != "ready" and answer.decision_readiness == "ready":
        answer.decision_readiness = answer.data_quality.readiness
    return answer


def validate_general_answer(
    raw: dict[str, Any], *, expected_mode: str
) -> ExecutiveGeneralAnswer:
    answer = ExecutiveGeneralAnswer.model_validate(raw)
    if answer.mode != expected_mode:
        raise ValueError(f"general answer mode must be {expected_mode}")
    return answer


def fallback_chairman_answer(
    *,
    template_id: str,
    tool_results: list[dict[str, Any]],
    organization_names: list[str],
    reason: str,
) -> ChairmanAnswer:
    sources = _authoritative_sources(tool_results)
    requested_quality = DataQuality(
        as_of=sources[0].as_of if sources else "未知",
        scope="全部授权事业部",
        readiness="conditional",
        issues=[
            DataQualityIssue(
                dimension="validity",
                severity="medium",
                detail="模型回答结构未通过服务端契约校验。",
            )
        ],
        decision_impact="本次仅保留已验证的数据来源，不生成未经校验的经营判断。",
    )
    return ChairmanAnswer(
        template_id=template_id,  # type: ignore[arg-type]
        decision_readiness="conditional",
        decision_line="本次已取得经营数据，但回答结构未通过校验，暂不生成推测性结论。",
        confidence=Confidence(level="low", reason=reason[:160] or "输出契约校验未通过"),
        metrics=[],
        primary_evidence=None,
        risks_or_opportunities=[],
        actions=[],
        data_quality=_authoritative_quality(
            tool_results, organization_names, requested_quality
        ),
        sources=sources,
        follow_up_questions=["是否重新执行这次经营分析？"],
    )


def fallback_general_answer(reason: str, *, mode: str = "direct_answer") -> ExecutiveGeneralAnswer:
    fallback_mode = mode if mode in GENERAL_MODES else "direct_answer"
    return ExecutiveGeneralAnswer(
        mode=fallback_mode,  # type: ignore[arg-type]
        headline="本次回答未能通过结构校验",
        direct_answer="模型已经返回内容，但其结构不满足当前高管回答规范，因此没有直接展示未经校验的正文。",
        caveats=[reason[:200] or "输出契约校验未通过"],
        draft_markdown=(
            "本次草稿未通过结构校验，请重新生成。"
            if fallback_mode == "writing_draft"
            else None
        ),
        follow_up_questions=["是否重新生成这次回答？"],
    )


def envelope_for_data(answer: ChairmanAnswer) -> dict[str, Any]:
    return AssistantOutputEnvelope(
        kind="data", body=answer.model_dump(mode="json")
    ).model_dump(mode="json")


def envelope_for_general(answer: ExecutiveGeneralAnswer) -> dict[str, Any]:
    return AssistantOutputEnvelope(
        kind="general", body=answer.model_dump(mode="json")
    ).model_dump(mode="json")


def envelope_for_clarification(question: str, options: list[dict[str, Any]]) -> dict[str, Any]:
    body = ClarificationAnswer(question=question, options=options)
    return AssistantOutputEnvelope(
        kind="clarification", body=body.model_dump(mode="json")
    ).model_dump(mode="json")


def plain_text_for_data(answer: ChairmanAnswer) -> str:
    lines = [answer.decision_line]
    if answer.metrics:
        lines.append(
            "；".join(
                f"{item.label}：{item.value}{item.unit}（{item.context}）"
                for item in answer.metrics
            )
        )
    if answer.actions:
        lines.append(
            "建议："
            + "；".join(
                f"{item.owner}｜{item.action}｜{item.due_at}｜{item.success_metric}"
                for item in answer.actions
            )
        )
    return "\n\n".join(lines)


def plain_text_for_general(answer: ExecutiveGeneralAnswer) -> str:
    lines = [answer.headline, answer.direct_answer]
    lines.extend(f"{item.title}\n{item.content}" for item in answer.sections)
    if answer.draft_markdown:
        lines.append(answer.draft_markdown)
    return "\n\n".join(lines)


def contract_prompt(kind: str) -> dict[str, Any]:
    if kind == "data":
        return {
            "name": "ChairmanAnswer",
            "schema": ChairmanAnswer.model_json_schema(),
            "rules": [
                "只输出 JSON，不要 Markdown 代码围栏",
                "关键数字必须引用 authorized_results 中提供的 evidence_id",
                "最多一个 primary_evidence",
            ],
        }
    return {
        "name": "ExecutiveGeneralAnswer",
        "schema": ExecutiveGeneralAnswer.model_json_schema(),
        "rules": ["只输出 JSON，不要 Markdown 代码围栏", "不得编造企业数据或实时联网结果"],
    }
