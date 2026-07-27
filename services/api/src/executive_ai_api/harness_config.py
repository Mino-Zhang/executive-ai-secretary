from __future__ import annotations

import copy
import hashlib
import json
import re
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .errors import AppError
from .mcp_registry import MCP_TOOL_SPECS
from .models import HarnessConfigVersion
from .security import utc_now

HARNESS_SCHEMA_VERSION = "3.0"
PROMPT_KEYS = {
    "system",
    "route",
    "rewrite",
    "plan",
    "data_answer",
    "general_answer",
}
SAFE_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")

DEFAULT_HARNESS_CONFIG: dict[str, Any] = {
    "prompts": {
        "system": (
            "你是董事长的人工智能研究员。表达准确、克制、直接；区分企业事实、推断与建议，"
            "不虚构数据，不把未联网内容描述为实时信息。"
        ),
        "route": (
            "判断问题是否必须使用企业经营数据。只输出 data、general 或 clarification。"
            "涉及公司、事业部、客户、商机、交付、回款、目标和经营指标时选择 data。"
        ),
        "rewrite": (
            "把经营问题改写为结构化 QuerySpec，保留时间、比较基准、指标、实体、范围、"
            "过滤、排序、数量和指代来源；无法可靠确定的内容放入 unresolved_ambiguities。"
        ),
        "plan": (
            "根据 QuerySpec 选择最少且充分的 MCP 工具。最多四个工具；独立工具可以并行；"
            "不得扩大服务端注入的事业部范围。"
        ),
        "data_answer": (
            "只依据授权工具结果回答。关键数字必须与证据一致；标注数据截止时间和范围；"
            "结果不足时明确说明缺口，不猜测。"
        ),
        "general_answer": (
            "回答董事长的日常分析、写作、思考和办公问题。可使用当前会话和本人授权记忆；"
            "当前未联网，涉及最新公开信息时必须说明限制。"
        ),
    },
    "glossary": [
        {"term": "SA", "canonical": "销售商机", "category": "商机", "enabled": True},
        {"term": "回款", "canonical": "财务回款", "category": "财务", "enabled": True},
        {"term": "交付", "canonical": "项目交付", "category": "项目", "enabled": True},
    ],
    "fast_rules": [
        {
            "id": "business-data-core",
            "name": "核心经营问数",
            "enabled": True,
            "priority": 100,
            "match_mode": "any",
            "terms": ["商机", "回款", "应收", "交付", "项目", "目标完成", "毛利"],
            "exclusions": ["怎么写", "润色", "翻译"],
            "route": "data",
            "candidate_tools": [],
        },
        {
            "id": "general-writing",
            "name": "写作与表达",
            "enabled": True,
            "priority": 60,
            "match_mode": "any",
            "terms": ["润色", "改写", "写一段", "总结这段话", "翻译"],
            "exclusions": ["本月", "事业部", "客户", "回款"],
            "route": "general",
            "candidate_tools": [],
        },
    ],
}

SAFETY_KERNEL_SUMMARY = {
    "editable": False,
    "output_schemas": ["RouteDecision", "QuerySpec", "ExecutionPlan"],
    "permission_scope": "server_enforced",
    "tool_allowlist": "registered_enabled_mcp_only",
    "max_tool_calls": 4,
    "max_parallel_calls": 3,
    "max_repair_plans": 1,
    "evidence_required_for_data": True,
    "internet_access": False,
    "file_access": False,
    "arbitrary_code": False,
}


def canonical_config_json(config: dict[str, Any]) -> str:
    return json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def config_hash(config: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_config_json(config).encode("utf-8")).hexdigest()


def _clean_text(value: Any, *, field: str, minimum: int = 1, maximum: int = 12000) -> str:
    if not isinstance(value, str):
        raise AppError(422, "invalid_harness_config", f"{field} 必须是文本")
    cleaned = value.strip()
    if len(cleaned) < minimum or len(cleaned) > maximum:
        raise AppError(
            422,
            "invalid_harness_config",
            f"{field} 长度必须在 {minimum} 到 {maximum} 个字符之间",
        )
    return cleaned


def validate_harness_config(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) != {"prompts", "glossary", "fast_rules"}:
        raise AppError(422, "invalid_harness_config", "编排配置结构无效")
    prompts = raw.get("prompts")
    if not isinstance(prompts, dict) or set(prompts) != PROMPT_KEYS:
        raise AppError(422, "invalid_harness_config", "Prompt 分区不完整")
    clean_prompts = {
        key: _clean_text(prompts[key], field=f"prompts.{key}", minimum=12)
        for key in sorted(PROMPT_KEYS)
    }

    glossary = raw.get("glossary")
    if not isinstance(glossary, list) or len(glossary) > 100:
        raise AppError(422, "invalid_harness_config", "业务术语表最多 100 项")
    clean_glossary: list[dict[str, Any]] = []
    seen_terms: set[str] = set()
    for index, item in enumerate(glossary):
        if not isinstance(item, dict):
            raise AppError(422, "invalid_harness_config", f"术语第 {index + 1} 项无效")
        term = _clean_text(item.get("term"), field="glossary.term", maximum=80)
        normalized = term.casefold()
        if normalized in seen_terms:
            raise AppError(422, "invalid_harness_config", f"术语重复：{term}")
        seen_terms.add(normalized)
        clean_glossary.append(
            {
                "term": term,
                "canonical": _clean_text(
                    item.get("canonical"), field="glossary.canonical", maximum=120
                ),
                "category": _clean_text(
                    item.get("category", "其他"), field="glossary.category", maximum=60
                ),
                "enabled": bool(item.get("enabled", True)),
            }
        )

    rules = raw.get("fast_rules")
    if not isinstance(rules, list) or len(rules) > 100:
        raise AppError(422, "invalid_harness_config", "快速规则最多 100 项")
    clean_rules: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(rules):
        if not isinstance(item, dict):
            raise AppError(422, "invalid_harness_config", f"规则第 {index + 1} 项无效")
        rule_id = str(item.get("id") or "").strip()
        if not SAFE_IDENTIFIER.fullmatch(rule_id) or rule_id in seen_ids:
            raise AppError(422, "invalid_harness_config", "规则 ID 无效或重复")
        seen_ids.add(rule_id)
        route = item.get("route")
        if route not in {"data", "general"}:
            raise AppError(422, "invalid_harness_config", "快速规则路由只能是 data 或 general")
        match_mode = item.get("match_mode")
        if match_mode not in {"any", "all"}:
            raise AppError(422, "invalid_harness_config", "匹配方式只能是 any 或 all")
        terms = item.get("terms")
        exclusions = item.get("exclusions", [])
        if not isinstance(terms, list) or not 1 <= len(terms) <= 20:
            raise AppError(422, "invalid_harness_config", "每条规则需要 1 到 20 个关键词")
        if not isinstance(exclusions, list) or len(exclusions) > 20:
            raise AppError(422, "invalid_harness_config", "每条规则最多 20 个排除词")
        candidate_tools = item.get("candidate_tools", [])
        if not isinstance(candidate_tools, list) or len(candidate_tools) > 4:
            raise AppError(422, "invalid_harness_config", "候选 MCP 工具最多 4 个")
        unknown_tools = set(candidate_tools) - set(MCP_TOOL_SPECS)
        if unknown_tools:
            raise AppError(
                422,
                "invalid_harness_config",
                f"规则包含未知 MCP 工具：{', '.join(sorted(unknown_tools))}",
            )
        if route == "general" and candidate_tools:
            raise AppError(422, "invalid_harness_config", "泛化规则不能指定 MCP 工具")
        clean_rules.append(
            {
                "id": rule_id,
                "name": _clean_text(item.get("name"), field="fast_rules.name", maximum=100),
                "enabled": bool(item.get("enabled", True)),
                "priority": max(0, min(int(item.get("priority", 0)), 1000)),
                "match_mode": match_mode,
                "terms": [
                    _clean_text(value, field="fast_rules.terms", maximum=100)
                    for value in terms
                ],
                "exclusions": [
                    _clean_text(value, field="fast_rules.exclusions", maximum=100)
                    for value in exclusions
                ],
                "route": route,
                "candidate_tools": list(dict.fromkeys(candidate_tools)),
            }
        )
    clean_rules.sort(key=lambda item: (-item["priority"], item["id"]))
    return {
        "prompts": clean_prompts,
        "glossary": clean_glossary,
        "fast_rules": clean_rules,
    }


def default_harness_config() -> dict[str, Any]:
    return validate_harness_config(copy.deepcopy(DEFAULT_HARNESS_CONFIG))


def active_harness_config(db: Session, enterprise_id: uuid.UUID) -> HarnessConfigVersion:
    row = db.scalar(
        select(HarnessConfigVersion).where(
            HarnessConfigVersion.enterprise_id == enterprise_id,
            HarnessConfigVersion.is_active.is_(True),
        )
    )
    if row is not None:
        return row
    config = default_harness_config()
    row = HarnessConfigVersion(
        enterprise_id=enterprise_id,
        version=1,
        schema_version=HARNESS_SCHEMA_VERSION,
        config_json=config,
        config_hash=config_hash(config),
        is_active=True,
        activated_at=utc_now(),
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(
            select(HarnessConfigVersion).where(
                HarnessConfigVersion.enterprise_id == enterprise_id,
                HarnessConfigVersion.is_active.is_(True),
            )
        )
        if existing is None:
            raise
        return existing
    return row


def next_harness_version(db: Session, enterprise_id: uuid.UUID) -> int:
    return int(
        db.scalar(
            select(func.coalesce(func.max(HarnessConfigVersion.version), 0)).where(
                HarnessConfigVersion.enterprise_id == enterprise_id
            )
        )
        or 0
    ) + 1


def match_fast_rule(question: str, config: dict[str, Any]) -> dict[str, Any] | None:
    normalized = question.casefold()
    for rule in config.get("fast_rules", []):
        if not rule.get("enabled"):
            continue
        if any(value.casefold() in normalized for value in rule.get("exclusions", [])):
            continue
        matches = [value.casefold() in normalized for value in rule.get("terms", [])]
        if matches and (all(matches) if rule.get("match_mode") == "all" else any(matches)):
            return rule
    return None


def apply_glossary(question: str, config: dict[str, Any]) -> str:
    rewritten = question
    for item in config.get("glossary", []):
        if not item.get("enabled"):
            continue
        rewritten = re.sub(
            re.escape(item["term"]),
            item["canonical"],
            rewritten,
            flags=re.IGNORECASE,
        )
    return rewritten
