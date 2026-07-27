from __future__ import annotations

import uuid
from calendar import monthrange
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from executive_ai_api.anspire import AnspireConfigurationError, runtime_provider_config
from executive_ai_api.capabilities import issue_capability_token
from executive_ai_api.config import Settings
from executive_ai_api.database import SessionLocal
from executive_ai_api.hermes_client import HermesRuntimeError, parse_json_response, run_hermes
from executive_ai_api.mcp_registry import MCP_TOOL_SPECS, planner_catalog
from executive_ai_api.models import (
    Clarification,
    Conversation,
    Job,
    Memory,
    Message,
    MessageEvidence,
    MessageRoute,
    MessageRun,
    ModelProviderConfig,
    OrganizationUnit,
    User,
)
from executive_ai_api.security import utc_now
from sqlalchemy import or_, select

TOOL_HINTS = {
    "get_target_completion": ("目标", "完成率", "达成"),
    "get_opportunity_funnel": ("漏斗", "阶段分布"),
    "get_sales_forecast": ("预测", "加权", "签约", "商机"),
    "get_customer_status": ("客户", "哪些客户"),
    "get_delivery_status": ("项目", "交付", "里程碑", "延期"),
    "get_finance_margin": ("毛利", "财务", "合同额"),
    "get_collection_aging": ("回款", "逾期", "账龄", "应收"),
    "get_organization_performance": ("事业部", "部门", "组织", "对比"),
    "get_daily_changes": ("今日", "今天", "变化", "昨日"),
}
BUSINESS_HINTS = tuple(
    dict.fromkeys(
        hint
        for hints in TOOL_HINTS.values()
        for hint in hints
    )
) + ("经营", "收入", "现金流", "业绩", "销售")
WIDE_SCOPE_HINTS = ("整体", "全部", "所有", "各事业部", "事业部对比", "横向对比")


def _deterministic_tools(question: str, allowed_tools: set[str]) -> list[str]:
    """Resolve only explicit, high-confidence intents; ambiguous wording stays model-routed."""

    candidates: list[str] = []
    if (
        any(hint in question for hint in ("事业部", "部门", "组织"))
        and any(hint in question for hint in ("比较", "对比", "排名", "差距", "表现"))
    ):
        candidates.append("get_organization_performance")
    elif any(hint in question for hint in ("逾期", "账龄")) or (
        "回款" in question and any(hint in question for hint in ("风险", "催收", "应收"))
    ):
        candidates.append("get_collection_aging")
    elif any(hint in question for hint in ("延期项目", "交付风险", "里程碑")):
        candidates.append("get_delivery_status")
    elif any(hint in question for hint in ("目标完成率", "目标达成", "达成率")):
        candidates.append("get_target_completion")
    elif any(hint in question for hint in ("商机漏斗", "阶段分布")):
        candidates.append("get_opportunity_funnel")
    elif any(hint in question for hint in ("销售预测", "加权预测", "签约预测")):
        candidates.append("get_sales_forecast")
    elif any(hint in question for hint in ("客户排名", "重点客户", "哪些客户")):
        candidates.append("get_customer_status")
    elif any(hint in question for hint in ("毛利率", "毛利额", "收入与毛利")):
        candidates.append("get_finance_margin")
    elif any(hint in question for hint in ("今日变化", "今天变化", "昨日变化")):
        candidates.append("get_daily_changes")
    elif any(hint in question for hint in ("整体经营", "经营概览", "经营全貌")):
        candidates.append("get_overall_business")
    return [tool for tool in candidates if tool in allowed_tools]


def _deterministic_period_arguments(
    question: str,
    timezone_name: str,
    *,
    today: date | None = None,
) -> dict[str, str]:
    current = today or datetime.now(ZoneInfo(timezone_name)).date()
    if "上月" in question:
        period_end = current.replace(day=1) - timedelta(days=1)
        period_start = period_end.replace(day=1)
    elif "本季度" in question:
        quarter_month = ((current.month - 1) // 3) * 3 + 1
        period_start = current.replace(month=quarter_month, day=1)
        end_month = quarter_month + 2
        period_end = current.replace(
            month=end_month,
            day=monthrange(current.year, end_month)[1],
        )
    elif "本月" in question:
        period_start = current.replace(day=1)
        period_end = current.replace(day=monthrange(current.year, current.month)[1])
    else:
        return {}
    return {"period_start": period_start.isoformat(), "period_end": period_end.isoformat()}


class OrchestrationPermanentError(RuntimeError):
    def __init__(self, code: str, message: str, placeholder: str) -> None:
        self.code = code
        self.placeholder = placeholder
        super().__init__(message)


def _ids(job: Job) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    try:
        return (
            uuid.UUID(str(job.payload_json["conversation_id"])),
            uuid.UUID(str(job.payload_json["message_id"])),
            uuid.UUID(str(job.payload_json["assistant_message_id"])),
        )
    except (KeyError, ValueError) as exc:
        raise OrchestrationPermanentError(
            "invalid_assistant_job", "回答任务缺少有效消息标识", "请求无法处理"
        ) from exc


def _organization_ids(job: Job) -> set[uuid.UUID]:
    try:
        return {
            uuid.UUID(str(value))
            for value in job.scope_snapshot_json.get("organization_unit_ids", [])
        }
    except ValueError as exc:
        raise OrchestrationPermanentError(
            "invalid_scope_snapshot", "任务权限快照无效", "当前查询范围无效"
        ) from exc


def _fallback_tool(question: str, allowed_tools: set[str]) -> str | None:
    for tool, hints in TOOL_HINTS.items():
        if tool in allowed_tools and any(hint in question for hint in hints):
            return tool
    if "get_overall_business" in allowed_tools:
        return "get_overall_business"
    return next(iter(sorted(allowed_tools)), None)


def _fallback_route(question: str) -> str:
    return "data" if any(hint in question for hint in BUSINESS_HINTS) else "general"


def _conversation_context(
    conversation_id: uuid.UUID,
    current_message_id: uuid.UUID,
) -> list[dict[str, str]]:
    with SessionLocal() as db:
        rows = db.scalars(
            select(Message)
            .where(
                Message.conversation_id == conversation_id,
                Message.id != current_message_id,
                Message.status == "completed",
                Message.role.in_(["user", "assistant"]),
            )
            .order_by(Message.sequence.desc())
            .limit(16)
        ).all()
    return _bounded_conversation_context(
        [(row.role, row.content) for row in reversed(rows)],
    )


def _bounded_conversation_context(
    rows: list[tuple[str, str]],
    *,
    total_characters: int = 6000,
) -> list[dict[str, str]]:
    """Keep recent intent without replaying complete historical reports."""

    remaining = total_characters
    selected: list[dict[str, str]] = []
    for role, raw_content in reversed(rows):
        content = raw_content.strip()
        if not content or remaining <= 0:
            continue
        per_message_limit = 1200 if role == "assistant" else 1000
        clipped = content[: min(per_message_limit, remaining)]
        selected.append({"role": role, "content": clipped})
        remaining -= len(clipped)
    return list(reversed(selected))


def _active_memories(
    enterprise_id: uuid.UUID,
    user_id: uuid.UUID,
    organization_ids: set[uuid.UUID],
) -> tuple[bool, list[dict[str, str]]]:
    with SessionLocal() as db:
        user = db.get(User, user_id)
        if user is None or not user.memory_enabled:
            return False, []
        scope_filter = Memory.organization_unit_id.is_(None)
        if organization_ids:
            scope_filter = or_(
                scope_filter,
                Memory.organization_unit_id.in_(organization_ids),
            )
        rows = db.scalars(
            select(Memory)
            .where(
                Memory.enterprise_id == enterprise_id,
                Memory.user_id == user_id,
                Memory.status == "active",
                scope_filter,
            )
            .order_by(Memory.updated_at.desc())
            .limit(20)
        ).all()
    remaining = 4000
    memories: list[dict[str, str]] = []
    for row in rows:
        content = row.content[:remaining]
        if not content:
            break
        memories.append({"kind": row.kind, "title": row.title, "content": content})
        remaining -= len(content)
    return True, memories


def _authorized_organizations(
    enterprise_id: uuid.UUID,
    organization_ids: set[uuid.UUID],
) -> list[dict[str, str]]:
    if not organization_ids:
        return []
    with SessionLocal() as db:
        rows = db.scalars(
            select(OrganizationUnit)
            .where(
                OrganizationUnit.enterprise_id == enterprise_id,
                OrganizationUnit.id.in_(organization_ids),
                OrganizationUnit.is_active.is_(True),
            )
            .order_by(OrganizationUnit.sort_order, OrganizationUnit.name)
        ).all()
    return [{"id": str(row.id), "code": row.code, "name": row.name} for row in rows]


def _execution_scope(
    question: str,
    organizations: list[dict[str, str]],
) -> set[uuid.UUID]:
    full_scope = {uuid.UUID(item["id"]) for item in organizations}
    if any(hint in question for hint in WIDE_SCOPE_HINTS):
        return full_scope
    matched = {
        uuid.UUID(item["id"])
        for item in organizations
        if item["name"] in question or item["code"].lower() in question.lower()
    }
    return matched or full_scope


def _route(
    job: Job,
    settings: Settings,
    question: str,
    context: list[dict[str, str]],
    memories: list[dict[str, str]],
    organizations: list[dict[str, str]],
    available_tools: list[dict[str, Any]],
    provider_config: dict[str, str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    deterministic = _deterministic_tools(
        question,
        {str(item["tool_name"]) for item in available_tools},
    )
    if deterministic:
        return (
            {
                "route": "data",
                "rewritten_query": question[:12000],
                "reason": "explicit registered business intent",
                "confidence": 0.99,
            },
            {"model": "deterministic-intent-v1", "usage": {}},
        )
    response = run_hermes(
        settings,
        profile="route",
        request_id=f"{job.id}:route",
        payload={
            "question": question,
            "conversation_context": context,
            "active_memories": memories,
            "authorized_organizations": organizations,
            "available_tool_names": [item["tool_name"] for item in available_tools],
        },
        provider_config=provider_config,
    )
    try:
        route = parse_json_response(response["text"])
    except HermesRuntimeError:
        route = {"route": _fallback_route(question), "confidence": 0, "reason": "fallback"}
    route_name = str(route.get("route") or _fallback_route(question))
    if route_name not in {"data", "general", "clarification"}:
        route_name = _fallback_route(question)
    if route_name == "clarification" and len(organizations) <= 1:
        route_name = _fallback_route(question)
    route.update(
        {
            "route": route_name,
            "rewritten_query": str(route.get("rewritten_query") or question)[:12000],
        }
    )
    return route, response


def _save_route(
    message_id: uuid.UUID,
    conversation_id: uuid.UUID,
    route: dict[str, Any],
    hermes_response: dict[str, Any],
) -> None:
    with SessionLocal.begin() as db:
        existing = db.scalar(select(MessageRoute).where(MessageRoute.message_id == message_id))
        scope_status = {
            "clarification": "clarification_required",
            "general": "not_required",
        }.get(route["route"], "authorized")
        if existing is None:
            existing = MessageRoute(
                message_id=message_id,
                conversation_id=conversation_id,
                route=route["route"],
                profile="route",
                rewritten_query=route["rewritten_query"],
                scope_status=scope_status,
                rationale=str(route.get("reason") or "")[:4000],
                confidence=max(0.0, min(float(route.get("confidence") or 0), 1.0)),
                model_name=hermes_response.get("model"),
                completed_at=utc_now(),
            )
            db.add(existing)
        else:
            existing.route = route["route"]
            existing.rewritten_query = route["rewritten_query"]
            existing.scope_status = scope_status
            existing.rationale = str(route.get("reason") or "")[:4000]
            existing.confidence = max(0.0, min(float(route.get("confidence") or 0), 1.0))
            existing.model_name = hermes_response.get("model")
            existing.completed_at = utc_now()


def _create_scope_clarification(
    *,
    job_id: uuid.UUID,
    lease_token: str,
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    assistant_message_id: uuid.UUID,
    route: dict[str, Any],
    organizations: list[dict[str, str]],
) -> dict[str, Any]:
    question = str(
        route.get("clarification_question") or "请确认这次需要查询哪个事业部。"
    )[:2000]
    options = [
        {"label": item["name"], "value": item["id"], "code": item["code"]}
        for item in organizations[:20]
    ]
    with SessionLocal.begin() as db:
        _assert_job_write_fence(db, job_id, lease_token)
        clarification = Clarification(
            conversation_id=conversation_id,
            message_id=message_id,
            question=question,
            options_json=options,
            status="pending",
        )
        db.add(clarification)
        db.flush()
        assistant = db.get(Message, assistant_message_id)
        if assistant:
            assistant.content = question
            assistant.content_json = {
                "route": "clarification",
                "clarification_id": str(clarification.id),
                "options": options,
            }
            assistant.status = "completed"
    return {"content": question, "route": "clarification"}


def _assert_job_write_fence(db, job_id: uuid.UUID, lease_token: str) -> None:
    active = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
    if (
        active is None
        or active.status != "running"
        or not lease_token
        or active.lease_token != lease_token
    ):
        raise RuntimeError("assistant job no longer owns its write lease")


def _normalize_argument(value: Any, schema: dict[str, Any]) -> Any:
    kind = schema.get("type")
    if kind == "integer":
        parsed = int(value)
        return min(
            max(parsed, int(schema.get("minimum", parsed))),
            int(schema.get("maximum", parsed)),
        )
    if kind == "boolean":
        if isinstance(value, bool):
            return value
        if str(value).lower() in {"true", "1", "yes"}:
            return True
        if str(value).lower() in {"false", "0", "no"}:
            return False
        raise ValueError("invalid boolean")
    if kind == "array":
        if not isinstance(value, list):
            raise ValueError("invalid array")
        item_schema = schema.get("items", {})
        values: list[Any] = []
        for item in value[:20]:
            try:
                values.append(_normalize_argument(item, item_schema))
            except (TypeError, ValueError):
                continue
        return values
    text = str(value).strip()
    if schema.get("format") == "date":
        date.fromisoformat(text)
    allowed = schema.get("enum")
    if allowed and text not in allowed:
        raise ValueError("value is outside enum")
    return text[: int(schema.get("maxLength", 500))]


def _normalize_calls(
    raw_calls: Any,
    question: str,
    available_tools: list[dict[str, Any]],
    organization_ids: set[uuid.UUID],
) -> list[dict[str, Any]]:
    by_name = {item["tool_name"]: item for item in available_tools}
    calls: list[dict[str, Any]] = []
    if isinstance(raw_calls, list):
        for item in raw_calls[:4]:
            if not isinstance(item, dict) or item.get("tool") not in by_name:
                continue
            tool_name = str(item["tool"])
            spec = MCP_TOOL_SPECS[tool_name]
            raw_arguments = item.get("arguments") if isinstance(item.get("arguments"), dict) else {}
            arguments: dict[str, Any] = {}
            for key, value in raw_arguments.items():
                if key not in spec.parameters:
                    continue
                try:
                    arguments[key] = _normalize_argument(value, spec.parameters[key])
                except (TypeError, ValueError):
                    continue
            if "limit" in arguments:
                arguments["limit"] = min(arguments["limit"], int(by_name[tool_name]["max_rows"]))
            arguments["organization_unit_ids"] = sorted(str(value) for value in organization_ids)
            calls.append(
                {
                    "tool": tool_name,
                    "arguments": arguments,
                    "reason": str(item.get("reason") or "")[:500],
                    "timeout_seconds": int(by_name[tool_name]["timeout_seconds"]),
                }
            )
    if not calls:
        fallback = _fallback_tool(question, set(by_name))
        if fallback:
            calls.append(
                {
                    "tool": fallback,
                    "arguments": {
                        "organization_unit_ids": sorted(str(value) for value in organization_ids)
                    },
                    "reason": "deterministic fallback",
                    "timeout_seconds": int(by_name[fallback]["timeout_seconds"]),
                }
            )
    return calls


def _plan(
    job: Job,
    settings: Settings,
    question: str,
    rewritten_query: str,
    context: list[dict[str, str]],
    available_tools: list[dict[str, Any]],
    organization_ids: set[uuid.UUID],
    provider_config: dict[str, str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    deterministic = _deterministic_tools(
        rewritten_query,
        {str(item["tool_name"]) for item in available_tools},
    )
    if deterministic:
        period_arguments = _deterministic_period_arguments(
            rewritten_query,
            settings.sync_timezone,
        )
        period_arguments["limit"] = 12
        calls = _normalize_calls(
            [
                {
                    "tool": tool,
                    "arguments": period_arguments,
                    "reason": "explicit registered business intent",
                }
                for tool in deterministic
            ],
            rewritten_query,
            available_tools,
            organization_ids,
        )
        return (
            {"analysis_mode": "deterministic", "calls": calls},
            {"model": "deterministic-planner-v1", "usage": {}},
        )
    response = run_hermes(
        settings,
        profile="plan",
        request_id=f"{job.id}:plan",
        payload={
            "question": question,
            "rewritten_query": rewritten_query,
            "conversation_context": context,
            "available_tools": [
                {
                    "tool_name": item["tool_name"],
                    "description": item["description"],
                    "parameters": item["parameters"],
                }
                for item in available_tools
            ],
        },
        provider_config=provider_config,
    )
    try:
        parsed = parse_json_response(response["text"])
    except HermesRuntimeError:
        parsed = {}
    calls = _normalize_calls(
        parsed.get("calls"),
        rewritten_query,
        available_tools,
        organization_ids,
    )
    return {"analysis_mode": str(parsed.get("analysis_mode") or "direct"), "calls": calls}, response


def _call_tool(
    *,
    settings: Settings,
    token: str,
    tool: str,
    arguments: dict[str, Any],
    timeout_seconds: int,
) -> dict[str, Any]:
    try:
        response = httpx.post(
            f"{settings.mcp_hub_url.rstrip('/')}/v1/tools/call",
            headers={"Authorization": f"Bearer {token}"},
            json={"tool": tool, "arguments": arguments},
            timeout=max(3, min(timeout_seconds, 60)),
        )
    except httpx.HTTPError as exc:
        raise RuntimeError(f"MCP Hub unavailable: {exc}") from exc
    if response.status_code >= 400:
        raise RuntimeError(f"MCP Hub rejected the query: {response.text[:1000]}")
    return response.json()


def _save_answer_with_evidence(
    *,
    job_id: uuid.UUID | None = None,
    lease_token: str | None = None,
    assistant_message_id: uuid.UUID,
    content: str,
    response: dict[str, Any],
    content_json: dict[str, Any],
    tool_results: list[dict[str, Any]],
) -> int:
    with SessionLocal.begin() as db:
        if job_id is not None:
            _assert_job_write_fence(db, job_id, lease_token or "")
        assistant = db.get(Message, assistant_message_id)
        if assistant is None:
            raise OrchestrationPermanentError(
                "assistant_message_missing", "回答占位消息不存在", "请求无法保存"
            )
        evidence_count = 0
        for call_index, item in enumerate(tool_results):
            tool = item["tool"]
            result = item["result"]
            for domain_index, freshness_row in enumerate(result.get("freshness", [])):
                source_data_as_of = freshness_row.get("source_data_as_of")
                if not source_data_as_of:
                    continue
                evidence_key = f"{call_index}:{tool}:{freshness_row['domain']}:{domain_index}"
                evidence = db.scalar(
                    select(MessageEvidence).where(
                        MessageEvidence.message_id == assistant_message_id,
                        MessageEvidence.evidence_key == evidence_key,
                    )
                )
                if evidence is None:
                    evidence = MessageEvidence(
                        message_id=assistant_message_id,
                        evidence_key=evidence_key,
                        domain=freshness_row["domain"],
                        title=f"{tool} 数据依据",
                        value_json={},
                        source_type="unknown",
                        source_display_name="未知数据源",
                        source_data_as_of=datetime.fromisoformat(source_data_as_of),
                        scope_json={},
                        query_json={},
                        row_references_json=[],
                    )
                    db.add(evidence)
                evidence.domain = freshness_row["domain"]
                evidence.title = f"{tool} 数据依据"
                evidence.value_json = result.get("data", {})
                evidence.source_type = freshness_row.get("source_type", "unknown")
                evidence.source_display_name = freshness_row.get(
                    "source_display_name", "未知数据源"
                )
                evidence.source_data_as_of = datetime.fromisoformat(source_data_as_of)
                evidence.dataset_version = freshness_row.get("dataset_version")
                evidence.scope_json = result.get("scope", {})
                evidence.query_json = {"tool": tool, "arguments": item["arguments"]}
                evidence.row_references_json = result.get("evidence", [])
                evidence_count += 1
        content_json["evidence_count"] = evidence_count
        assistant.content = content
        assistant.content_json = content_json
        assistant.status = "completed"
        assistant.model_name = response.get("model")
        timestamps = [
            row.get("source_data_as_of")
            for row in content_json.get("freshness", [])
            if row.get("source_data_as_of")
        ]
        if timestamps:
            assistant.source_data_as_of = min(datetime.fromisoformat(value) for value in timestamps)
        message_run = db.scalar(
            select(MessageRun)
            .where(MessageRun.message_id == assistant.id)
            .order_by(MessageRun.created_at.desc())
        )
        if message_run is None:
            message_run = MessageRun(message_id=assistant.id)
            db.add(message_run)
        message_run.status = "completed"
        message_run.provider = response.get("provider")
        message_run.model_name = response.get("model")
        message_run.input_tokens = response.get("usage", {}).get("input_tokens")
        message_run.output_tokens = response.get("usage", {}).get("output_tokens")
        message_run.started_at = message_run.started_at or utc_now()
        message_run.completed_at = utc_now()
    return evidence_count


def run_assistant_job(job: Job, settings: Settings) -> dict[str, Any]:
    conversation_id, message_id, assistant_message_id = _ids(job)
    authorized_scope = _organization_ids(job)
    if job.created_by_user_id is None:
        raise OrchestrationPermanentError(
            "assistant_user_missing", "回答任务没有用户", "请求无法验证"
        )
    with SessionLocal() as db:
        conversation = db.get(Conversation, conversation_id)
        message = db.get(Message, message_id)
        model_config = db.scalar(
            select(ModelProviderConfig).where(
                ModelProviderConfig.enterprise_id == job.enterprise_id
            )
        )
        if (
            conversation is None
            or message is None
            or conversation.enterprise_id != job.enterprise_id
            or conversation.owner_user_id != job.created_by_user_id
            or message.conversation_id != conversation.id
        ):
            raise OrchestrationPermanentError(
                "assistant_resource_forbidden", "会话或消息不属于当前用户", "请求权限已失效"
            )
        question = message.content
        if model_config is None:
            raise OrchestrationPermanentError(
                "anspire_not_configured", "企业尚未配置 Anspire 模型", "Anspire 模型尚未配置"
            )
        try:
            provider_config = runtime_provider_config(model_config, settings)
        except AnspireConfigurationError as exc:
            raise OrchestrationPermanentError(
                exc.code, str(exc), "Anspire 模型尚未配置或启用"
            ) from exc
        available_tools = planner_catalog(db, job.enterprise_id)

    context = _conversation_context(conversation_id, message_id)
    memory_enabled, memories = _active_memories(
        job.enterprise_id,
        job.created_by_user_id,
        authorized_scope,
    )
    organizations = _authorized_organizations(job.enterprise_id, authorized_scope)
    execution_scope = _execution_scope(question, organizations)
    try:
        route, route_response = _route(
            job,
            settings,
            question,
            context,
            memories,
            organizations,
            available_tools,
            provider_config,
        )
    except HermesRuntimeError as exc:
        if exc.permanent:
            raise OrchestrationPermanentError(exc.code, str(exc), "无法连接已配置模型") from exc
        raise
    _save_route(message_id, conversation_id, route, route_response)
    if route["route"] == "clarification":
        return _create_scope_clarification(
            job_id=job.id,
            lease_token=job.lease_token or "",
            conversation_id=conversation_id,
            message_id=message_id,
            assistant_message_id=assistant_message_id,
            route=route,
            organizations=organizations,
        )

    tool_results: list[dict[str, Any]] = []
    tool_errors: list[dict[str, str]] = []
    plan: dict[str, Any] | None = None
    if route["route"] == "data":
        if not execution_scope:
            raise OrchestrationPermanentError(
                "empty_scope_snapshot", "任务没有事业部权限", "当前账号没有可查询的数据范围"
            )
        if not available_tools:
            raise OrchestrationPermanentError(
                "no_mcp_tools_available", "没有可用于规划的 MCP 工具", "经营查询工具暂不可用"
            )
        plan, _ = _plan(
            job,
            settings,
            question,
            route["rewritten_query"],
            context,
            available_tools,
            execution_scope,
            provider_config,
        )
        tool_names = {item["tool"] for item in plan["calls"]}
        token = issue_capability_token(
            settings=settings,
            enterprise_id=job.enterprise_id,
            user_id=job.created_by_user_id,
            organization_unit_ids=execution_scope,
            tools=tool_names,
            message_id=message_id,
        )
        for call in plan["calls"]:
            try:
                result = _call_tool(
                    settings=settings,
                    token=token,
                    tool=call["tool"],
                    arguments=call["arguments"],
                    timeout_seconds=call["timeout_seconds"],
                )
                tool_results.append(
                    {
                        "tool": call["tool"],
                        "arguments": call["arguments"],
                        "reason": call["reason"],
                        "result": result,
                    }
                )
            except RuntimeError as exc:
                tool_errors.append({"tool": call["tool"], "error": str(exc)[:1000]})
        if not tool_results:
            raise RuntimeError("all planned MCP tool calls failed")

    profile = "data" if route["route"] == "data" else "general"
    answer_payload = {
        "question": question,
        "rewritten_query": route["rewritten_query"],
        "conversation_context": context,
        "memory_enabled": memory_enabled,
        "active_memories": memories,
        "authorized_results": tool_results,
        "tool_errors": tool_errors,
        "execution_plan": plan,
    }
    try:
        answer_response = run_hermes(
            settings,
            profile=profile,
            payload=answer_payload,
            request_id=f"{job.id}:answer",
            provider_config=provider_config,
        )
    except HermesRuntimeError as exc:
        if exc.permanent:
            raise OrchestrationPermanentError(exc.code, str(exc), "无法连接已配置模型") from exc
        raise

    freshness = [
        row
        for item in tool_results
        for row in item["result"].get("freshness", [])
    ]
    structured_data: dict[str, Any]
    if len(tool_results) == 1:
        structured_data = tool_results[0]["result"].get("data", {})
    else:
        structured_data = {
            "results": [
                {"tool": item["tool"], "data": item["result"].get("data", {})}
                for item in tool_results
            ]
        }
    content_json = {
        "route": route["route"],
        "tools": [item["tool"] for item in tool_results],
        "execution_plan": plan,
        "structured_data": structured_data,
        "freshness": freshness,
        "scope": {"organization_unit_ids": sorted(str(value) for value in execution_scope)},
        "tool_errors": tool_errors,
        "memory_used": bool(memory_enabled and memories),
    }
    evidence_count = _save_answer_with_evidence(
        job_id=job.id,
        lease_token=job.lease_token,
        assistant_message_id=assistant_message_id,
        content=answer_response["text"],
        response=answer_response,
        content_json=content_json,
        tool_results=tool_results,
    )
    return {
        "content": answer_response["text"],
        "route": route["route"],
        "tools": content_json["tools"],
        "evidence_count": evidence_count,
    }
