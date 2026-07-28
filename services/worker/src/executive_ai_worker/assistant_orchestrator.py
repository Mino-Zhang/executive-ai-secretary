from __future__ import annotations

import json
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from time import monotonic
from typing import Any

import httpx
from executive_ai_api.anspire import AnspireConfigurationError, runtime_provider_config
from executive_ai_api.answer_contract import (
    ANSWER_CONTRACT_VERSION,
    contract_prompt,
    enrich_tool_results,
    envelope_for_clarification,
    envelope_for_data,
    envelope_for_general,
    extract_json_object,
    fallback_chairman_answer,
    fallback_general_answer,
    plain_text_for_data,
    plain_text_for_general,
    select_data_template,
    select_general_mode,
    validate_chairman_answer,
    validate_general_answer,
)
from executive_ai_api.authz import accessible_organization_unit_ids_for_user
from executive_ai_api.capabilities import issue_capability_token
from executive_ai_api.config import Settings
from executive_ai_api.database import SessionLocal
from executive_ai_api.harness_config import apply_glossary, match_fast_rule
from executive_ai_api.hermes_client import HermesRuntimeError, parse_json_response, run_hermes
from executive_ai_api.mcp_registry import planner_catalog
from executive_ai_api.models import (
    Clarification,
    Conversation,
    HarnessConfigVersion,
    HarnessStageRun,
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
from executive_ai_api.personal_data import ensure_memory_encrypted
from executive_ai_api.query_spec import normalize_query_spec
from executive_ai_api.security import utc_now
from pydantic import ValidationError
from sqlalchemy import or_, select

ANSWER_REPAIR_START_BUDGET_SECONDS = 30.0


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


def _conversation_context(
    conversation_id: uuid.UUID,
    current_message_id: uuid.UUID,
) -> list[dict[str, str]]:
    with SessionLocal.begin() as db:
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
    settings: Settings,
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
        content = ensure_memory_encrypted(row, settings)[:remaining]
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


def _outside_scope_organizations(
    enterprise_id: uuid.UUID,
    user_id: uuid.UUID,
    question: str,
    current_scope: set[uuid.UUID],
) -> list[dict[str, str]]:
    with SessionLocal() as db:
        user = db.get(User, user_id)
        if user is None:
            return []
        allowed = accessible_organization_unit_ids_for_user(db, user) - current_scope
        if not allowed:
            return []
        rows = db.scalars(
            select(OrganizationUnit).where(
                OrganizationUnit.enterprise_id == enterprise_id,
                OrganizationUnit.id.in_(allowed),
                OrganizationUnit.is_active.is_(True),
            )
        ).all()
    normalized = question.casefold()
    return [
        {"id": str(row.id), "code": row.code, "name": row.name}
        for row in rows
        if row.name in question or row.code.casefold() in normalized
    ]


def _execution_scope(
    question: str,
    organizations: list[dict[str, str]],
) -> set[uuid.UUID]:
    full_scope = {uuid.UUID(item["id"]) for item in organizations}
    matched = {
        uuid.UUID(item["id"])
        for item in organizations
        if item["name"] in question or item["code"].lower() in question.lower()
    }
    return matched or full_scope


def _record_stage(
    job: Job,
    message_id: uuid.UUID,
    *,
    stage: str,
    status: str,
    started_at: float,
    response: dict[str, Any] | None = None,
    route_source: str | None = None,
    tool_names: list[str] | None = None,
    summary: dict[str, Any] | None = None,
    error_code: str | None = None,
) -> None:
    usage = (response or {}).get("usage", {})
    with SessionLocal.begin() as db:
        active = db.get(Job, job.id)
        if active is None or active.lease_token != job.lease_token:
            return
        db.add(
            HarnessStageRun(
                enterprise_id=job.enterprise_id,
                message_id=message_id,
                harness_version_id=job.harness_version_id,
                stage=stage,
                status=status,
                route_source=route_source,
                model_name=(response or {}).get("model"),
                latency_ms=max(0, round((monotonic() - started_at) * 1000)),
                input_tokens=usage.get("input_tokens"),
                output_tokens=usage.get("output_tokens"),
                tool_names_json=tool_names or [],
                summary_json=summary or {},
                error_code=error_code,
            )
        )


def _route(
    job: Job,
    settings: Settings,
    question: str,
    context: list[dict[str, str]],
    organizations: list[dict[str, str]],
    available_tools: list[dict[str, Any]],
    provider_config: dict[str, str],
    harness_config: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    rule = match_fast_rule(question, harness_config)
    if rule:
        return (
            {
                "route": rule["route"],
                "reason": f"matched configured rule: {rule['name']}",
                "confidence": 0.99,
                "route_source": "fast_rule",
                "matched_rule_id": rule["id"],
                "candidate_tools": [
                    name
                    for name in rule.get("candidate_tools", [])
                    if name in {str(item["tool_name"]) for item in available_tools}
                ],
            },
            {"model": "configured-fast-rule-v1", "usage": {}},
        )
    response = run_hermes(
        settings,
        profile="route",
        request_id=f"{job.id}:route",
        payload={
            "question": question,
            "conversation_context": context,
            "authorized_organizations": organizations,
            "available_tool_names": [item["tool_name"] for item in available_tools],
            "harness_config": harness_config,
        },
        provider_config=provider_config,
    )
    try:
        route = parse_json_response(response["text"])
    except HermesRuntimeError:
        route = {
            "route": "clarification",
            "confidence": 0,
            "reason": "route output failed validation",
            "clarification_question": (
                "我还不能可靠判断这是否需要企业经营数据，请补充要分析的对象或目标。"
            ),
        }
    route_name = str(route.get("route") or "clarification")
    if route_name not in {"data", "general", "clarification"}:
        route_name = "clarification"
    route.update(
        {
            "route": route_name,
            "route_source": "hermes",
            "matched_rule_id": None,
            "scope_action": "add",
            "candidate_tools": [],
        }
    )
    return route, response


def _rewrite_query(
    job: Job,
    settings: Settings,
    question: str,
    context: list[dict[str, str]],
    organizations: list[dict[str, str]],
    execution_scope: set[uuid.UUID],
    provider_config: dict[str, str],
    harness_config: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    glossary_question = apply_glossary(question, harness_config)
    server_scope = {
        "mode": str(job.scope_snapshot_json.get("scope_mode") or "selected"),
        "organization_unit_ids": sorted(str(item) for item in execution_scope),
    }
    response = run_hermes(
        settings,
        profile="rewrite",
        request_id=f"{job.id}:rewrite",
        payload={
            "question": glossary_question,
            "conversation_context": context,
            "authorized_organizations": organizations,
            "organization_scope": server_scope,
            "harness_config": harness_config,
        },
        provider_config=provider_config,
    )
    parsed = parse_json_response(response["text"])
    return (
        normalize_query_spec(
            parsed,
            question=glossary_question,
            organization_scope=server_scope,
        ),
        response,
    )


def _save_route(
    message_id: uuid.UUID,
    conversation_id: uuid.UUID,
    route: dict[str, Any],
    hermes_response: dict[str, Any],
    query_spec: dict[str, Any],
    harness_version_id: uuid.UUID | None,
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
                query_spec_json=query_spec,
                harness_version_id=harness_version_id,
                route_source=str(route.get("route_source") or "hermes"),
                matched_rule_id=route.get("matched_rule_id"),
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
            existing.query_spec_json = query_spec
            existing.harness_version_id = harness_version_id
            existing.route_source = str(route.get("route_source") or "hermes")
            existing.matched_rule_id = route.get("matched_rule_id")
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
        {
            "label": item["name"],
            "value": item["id"],
            "code": item["code"],
            "action": route.get("scope_action", "select"),
        }
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
                "assistant_output": envelope_for_clarification(question, options),
            }
            assistant.status = "completed"
            assistant.output_contract_version = ANSWER_CONTRACT_VERSION
            assistant.output_template_id = "clarification"
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
            parameter_schemas = by_name[tool_name]["parameters"]
            raw_arguments = item.get("arguments") if isinstance(item.get("arguments"), dict) else {}
            arguments: dict[str, Any] = {}
            for key, value in raw_arguments.items():
                if key not in parameter_schemas:
                    continue
                try:
                    arguments[key] = _normalize_argument(value, parameter_schemas[key])
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
    return calls


def _plan(
    job: Job,
    settings: Settings,
    question: str,
    query_spec: dict[str, Any],
    context: list[dict[str, str]],
    available_tools: list[dict[str, Any]],
    organization_ids: set[uuid.UUID],
    provider_config: dict[str, str],
    harness_config: dict[str, Any],
    candidate_tools: list[str] | None = None,
    repair_context: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    response = run_hermes(
        settings,
        profile="plan",
        request_id=f"{job.id}:plan",
        payload={
            "question": question,
            "query_spec": query_spec,
            "conversation_context": context,
            "candidate_tools": candidate_tools or [],
            "repair_context": repair_context,
            "available_tools": [
                {
                    "tool_name": item["tool_name"],
                    "description": item["description"],
                    "parameters": item["parameters"],
                }
                for item in available_tools
            ],
            "harness_config": harness_config,
        },
        provider_config=provider_config,
    )
    try:
        parsed = parse_json_response(response["text"])
    except HermesRuntimeError:
        parsed = {}
    calls = _normalize_calls(
        parsed.get("calls"),
        str(query_spec.get("normalized_question") or question),
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


def _execute_calls(
    *,
    job: Job,
    message_id: uuid.UUID,
    settings: Settings,
    calls: list[dict[str, Any]],
    organization_ids: set[uuid.UUID],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    if not calls:
        return [], []
    token = issue_capability_token(
        settings=settings,
        enterprise_id=job.enterprise_id,
        user_id=job.created_by_user_id,
        organization_unit_ids=organization_ids,
        tools={item["tool"] for item in calls},
        message_id=message_id,
    )
    indexed_results: list[tuple[int, dict[str, Any]]] = []
    indexed_errors: list[tuple[int, dict[str, str]]] = []
    with ThreadPoolExecutor(max_workers=min(3, len(calls))) as pool:
        futures = {
            pool.submit(
                _call_tool,
                settings=settings,
                token=token,
                tool=call["tool"],
                arguments=call["arguments"],
                timeout_seconds=call["timeout_seconds"],
            ): (index, call)
            for index, call in enumerate(calls)
        }
        for future in as_completed(futures):
            index, call = futures[future]
            try:
                result = future.result()
                indexed_results.append(
                    (
                        index,
                        {
                            "tool": call["tool"],
                            "arguments": call["arguments"],
                            "reason": call["reason"],
                            "result": result,
                        },
                    )
                )
            except RuntimeError as exc:
                indexed_errors.append(
                    (index, {"tool": call["tool"], "error": str(exc)[:1000]})
                )
    return (
        [item for _, item in sorted(indexed_results)],
        [item for _, item in sorted(indexed_errors)],
    )


def _valid_evidence_count(tool_results: list[dict[str, Any]]) -> int:
    return sum(
        len(item["result"].get("freshness", []))
        for item in tool_results
        if isinstance(item.get("result"), dict)
    )


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
        assistant.output_contract_version = str(
            content_json.get("assistant_output", {}).get("schema_version")
            or ANSWER_CONTRACT_VERSION
        )
        body = content_json.get("assistant_output", {}).get("body", {})
        assistant.output_template_id = str(
            body.get("template_id") or body.get("mode") or content_json.get("route") or ""
        )[:64] or None
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
        message_run.requested_model_id = assistant.requested_model_id
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
        harness_version = (
            db.get(HarnessConfigVersion, job.harness_version_id)
            if job.harness_version_id
            else db.scalar(
                select(HarnessConfigVersion).where(
                    HarnessConfigVersion.enterprise_id == job.enterprise_id,
                    HarnessConfigVersion.is_active.is_(True),
                )
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
        requested_model_id = str(
            job.payload_json.get("model_id")
            or message.requested_model_id
            or conversation.selected_model_id
            or (model_config.model_id if model_config else "")
        ).strip()
        if model_config is None:
            raise OrchestrationPermanentError(
                "anspire_not_configured", "企业尚未配置 Anspire 模型", "Anspire 模型尚未配置"
            )
        try:
            provider_config = runtime_provider_config(
                model_config,
                settings,
                model_id=requested_model_id or None,
            )
        except AnspireConfigurationError as exc:
            raise OrchestrationPermanentError(
                exc.code, str(exc), "Anspire 模型尚未配置或启用"
            ) from exc
        available_tools = planner_catalog(db, job.enterprise_id)
        if harness_version is None:
            raise OrchestrationPermanentError(
                "harness_config_missing",
                "任务没有可用的编排策略快照",
                "编排策略尚未初始化",
            )
        harness_config = harness_version.config_json

    context = _conversation_context(conversation_id, message_id)
    memory_enabled, memories = _active_memories(
        job.enterprise_id,
        job.created_by_user_id,
        authorized_scope,
        settings,
    )
    organizations = _authorized_organizations(job.enterprise_id, authorized_scope)
    execution_scope = _execution_scope(question, organizations)
    outside_scope = _outside_scope_organizations(
        job.enterprise_id,
        job.created_by_user_id,
        question,
        authorized_scope,
    )
    if outside_scope:
        route = {
            "route": "clarification",
            "rewritten_query": question,
            "reason": "question references an authorized unit outside the message scope",
            "confidence": 1.0,
            "route_source": "validation",
            "matched_rule_id": None,
            "clarification_question": (
                f"当前查询范围未包含{outside_scope[0]['name']}。"
                "请在事业部选择中加入该事业部后继续。"
            ),
        }
        response = {"model": "scope-validator-v1", "usage": {}}
        _save_route(
            message_id,
            conversation_id,
            route,
            response,
            {},
            harness_version.id,
        )
        _record_stage(
            job,
            message_id,
            stage="scope_validation",
            status="clarification",
            started_at=monotonic(),
            response=response,
            route_source="validation",
            summary={"outside_scope_reference": True},
        )
        return _create_scope_clarification(
            job_id=job.id,
            lease_token=job.lease_token or "",
            conversation_id=conversation_id,
            message_id=message_id,
            assistant_message_id=assistant_message_id,
            route=route,
            organizations=outside_scope,
        )
    route_started = monotonic()
    try:
        route, route_response = _route(
            job,
            settings,
            question,
            context,
            organizations,
            available_tools,
            provider_config,
            harness_config,
        )
    except HermesRuntimeError as exc:
        if exc.permanent:
            raise OrchestrationPermanentError(exc.code, str(exc), "无法连接已配置模型") from exc
        raise
    _record_stage(
        job,
        message_id,
        stage="intent_route",
        status="completed",
        started_at=route_started,
        response=route_response,
        route_source=route.get("route_source"),
        summary={
            "route": route["route"],
            "matched_rule_id": route.get("matched_rule_id"),
            "organization_unit_count": len(execution_scope),
        },
    )
    query_spec: dict[str, Any] = {}
    if route["route"] == "clarification":
        route["rewritten_query"] = question
        _save_route(
            message_id,
            conversation_id,
            route,
            route_response,
            query_spec,
            harness_version.id,
        )
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
        rewrite_started = monotonic()
        try:
            query_spec, rewrite_response = _rewrite_query(
                job,
                settings,
                question,
                context,
                organizations,
                execution_scope,
                provider_config,
                harness_config,
            )
        except HermesRuntimeError as exc:
            raise OrchestrationPermanentError(
                exc.code,
                str(exc),
                "我还不能可靠理解这次经营问题，请补充指标、时间或对象",
            ) from exc
        route["rewritten_query"] = query_spec["normalized_question"]
        _record_stage(
            job,
            message_id,
            stage="query_rewrite",
            status="completed",
            started_at=rewrite_started,
            response=rewrite_response,
            summary={
                "metric_count": len(query_spec["metrics"]),
                "entity_type_count": len(query_spec["entities"]),
                "ambiguity_count": len(query_spec["unresolved_ambiguities"]),
                "organization_unit_count": len(execution_scope),
            },
        )
        _save_route(
            message_id,
            conversation_id,
            route,
            route_response,
            query_spec,
            harness_version.id,
        )
        plan_started = monotonic()
        plan, plan_response = _plan(
            job,
            settings,
            question,
            query_spec,
            context,
            available_tools,
            execution_scope,
            provider_config,
            harness_config,
            candidate_tools=route.get("candidate_tools"),
        )
        _record_stage(
            job,
            message_id,
            stage="task_plan",
            status="completed" if plan["calls"] else "failed",
            started_at=plan_started,
            response=plan_response,
            tool_names=[item["tool"] for item in plan["calls"]],
            summary={
                "call_count": len(plan["calls"]),
                "organization_unit_count": len(execution_scope),
            },
            error_code=None if plan["calls"] else "no_valid_tool_plan",
        )
        if not plan["calls"]:
            raise OrchestrationPermanentError(
                "no_valid_tool_plan",
                "Hermes 没有生成有效的 MCP 计划",
                "当前启用的经营工具无法完成这次查询",
            )
        execution_started = monotonic()
        tool_results, tool_errors = _execute_calls(
            job=job,
            message_id=message_id,
            settings=settings,
            calls=plan["calls"],
            organization_ids=execution_scope,
        )
        repair_used = False
        if tool_errors or _valid_evidence_count(tool_results) == 0:
            repair_started = monotonic()
            succeeded_tools = {item["tool"] for item in tool_results}
            repair_plan, repair_response = _plan(
                job,
                settings,
                question,
                query_spec,
                context,
                available_tools,
                execution_scope,
                provider_config,
                harness_config,
                candidate_tools=route.get("candidate_tools"),
                repair_context={
                    "successful_tools": sorted(succeeded_tools),
                    "failed_tools": tool_errors,
                    "instruction": "只补足仍缺少的证据，不要重复已成功工具。",
                },
            )
            repair_calls = [
                item for item in repair_plan["calls"] if item["tool"] not in succeeded_tools
            ][:4]
            if repair_calls:
                repair_results, repair_errors = _execute_calls(
                    job=job,
                    message_id=message_id,
                    settings=settings,
                    calls=repair_calls,
                    organization_ids=execution_scope,
                )
                tool_results.extend(repair_results)
                tool_errors.extend(repair_errors)
                repair_used = True
            _record_stage(
                job,
                message_id,
                stage="repair_plan",
                status="completed" if repair_calls else "skipped",
                started_at=repair_started,
                response=repair_response,
                tool_names=[item["tool"] for item in repair_calls],
                summary={"repair_call_count": len(repair_calls)},
            )
        evidence_count_before_answer = _valid_evidence_count(tool_results)
        _record_stage(
            job,
            message_id,
            stage="mcp_execution",
            status="completed" if evidence_count_before_answer else "failed",
            started_at=execution_started,
            tool_names=[item["tool"] for item in tool_results],
            summary={
                "success_count": len(tool_results),
                "failure_count": len(tool_errors),
                "evidence_count": evidence_count_before_answer,
                "repair_used": repair_used,
                "organization_unit_count": len(execution_scope),
            },
            error_code=None if evidence_count_before_answer else "insufficient_evidence",
        )
        if not tool_results or evidence_count_before_answer == 0:
            raise OrchestrationPermanentError(
                "insufficient_evidence",
                "MCP 工具未返回可验证证据",
                "本次查询没有取得足够的经营数据证据，因此没有生成推测性回答",
            )
    else:
        route["rewritten_query"] = question
        _save_route(
            message_id,
            conversation_id,
            route,
            route_response,
            query_spec,
            harness_version.id,
        )

    profile = "data" if route["route"] == "data" else "general"
    if route["route"] == "data":
        tool_results = enrich_tool_results(tool_results)
    expected_template = (
        select_data_template(
            query_spec,
            [item["tool"] for item in tool_results],
        )
        if route["route"] == "data"
        else None
    )
    expected_general_mode = (
        select_general_mode(question) if route["route"] == "general" else None
    )
    answer_payload = {
        "question": question,
        "rewritten_query": route["rewritten_query"],
        "query_spec": query_spec,
        "conversation_context": context,
        "memory_enabled": memory_enabled,
        "active_memories": memories,
        "authorized_results": tool_results,
        "tool_errors": tool_errors,
        "execution_plan": plan,
        "expected_template_id": expected_template,
        "expected_general_mode": expected_general_mode,
        "output_contract": contract_prompt(route["route"]),
        "harness_config": harness_config,
    }
    answer_started = monotonic()
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
    contract_error: str | None = None
    repair_used_for_answer = False
    repair_succeeded = False
    assistant_output: dict[str, Any]
    answer_content: str
    try:
        raw_answer = extract_json_object(answer_response["text"])
        if route["route"] == "data":
            contract_answer = validate_chairman_answer(
                raw_answer,
                expected_template=expected_template or "executive_pulse",
                tool_results=tool_results,
                organization_names=[item["name"] for item in organizations],
            )
            assistant_output = envelope_for_data(contract_answer)
            answer_content = plain_text_for_data(contract_answer)
        else:
            general_answer = validate_general_answer(
                raw_answer,
                expected_mode=expected_general_mode or "direct_answer",
            )
            assistant_output = envelope_for_general(general_answer)
            answer_content = plain_text_for_general(general_answer)
    except (json.JSONDecodeError, ValidationError, ValueError) as exc:
        contract_error = str(exc)[:1200]
        repair_payload = {
            **answer_payload,
            "contract_validation_errors": [contract_error],
            "previous_output": answer_response["text"][:12000],
            "repair_instruction": "只修复结构与证据引用，不得新增输入中不存在的事实。",
        }
        try:
            if monotonic() - answer_started >= ANSWER_REPAIR_START_BUDGET_SECONDS:
                raise TimeoutError("首轮回答已耗尽结构修复预算，直接使用受控降级答案")
            repaired_response = run_hermes(
                settings,
                profile=profile,
                payload=repair_payload,
                request_id=f"{job.id}:answer-repair",
                provider_config=provider_config,
            )
            repair_used_for_answer = True
            repaired_raw = extract_json_object(repaired_response["text"])
            if route["route"] == "data":
                contract_answer = validate_chairman_answer(
                    repaired_raw,
                    expected_template=expected_template or "executive_pulse",
                    tool_results=tool_results,
                    organization_names=[item["name"] for item in organizations],
                )
                assistant_output = envelope_for_data(contract_answer)
                answer_content = plain_text_for_data(contract_answer)
            else:
                general_answer = validate_general_answer(
                    repaired_raw,
                    expected_mode=expected_general_mode or "direct_answer",
                )
                assistant_output = envelope_for_general(general_answer)
                answer_content = plain_text_for_general(general_answer)
            answer_response = repaired_response
            repair_succeeded = True
        except (
            HermesRuntimeError,
            json.JSONDecodeError,
            ValidationError,
            ValueError,
            TimeoutError,
        ) as repair_exc:
            contract_error = f"{contract_error}; repair: {str(repair_exc)[:800]}"
            if route["route"] == "data":
                contract_answer = fallback_chairman_answer(
                    template_id=expected_template or "executive_pulse",
                    tool_results=tool_results,
                    organization_names=[item["name"] for item in organizations],
                    reason=contract_error,
                )
                assistant_output = envelope_for_data(contract_answer)
                answer_content = plain_text_for_data(contract_answer)
            else:
                general_answer = fallback_general_answer(
                    contract_error,
                    mode=expected_general_mode or "direct_answer",
                )
                assistant_output = envelope_for_general(general_answer)
                answer_content = plain_text_for_general(general_answer)
    _record_stage(
        job,
        message_id,
        stage="answer",
        status=(
            "completed"
            if contract_error is None
            else "repaired"
            if repair_succeeded
            else "fallback"
        ),
        started_at=answer_started,
        response=answer_response,
        tool_names=[item["tool"] for item in tool_results],
        summary={
            "route": route["route"],
            "template_id": expected_template or expected_general_mode,
            "contract_version": ANSWER_CONTRACT_VERSION,
            "contract_valid_first_pass": contract_error is None,
            "answer_repair_used": repair_used_for_answer,
            "answer_repair_succeeded": repair_succeeded,
            "evidence_count": _valid_evidence_count(tool_results),
            "organization_unit_count": len(execution_scope),
        },
        error_code="answer_contract_repaired" if contract_error else None,
    )

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
        "route_source": route.get("route_source"),
        "query_spec": query_spec,
        "harness_version": harness_version.version,
        "tools": [item["tool"] for item in tool_results],
        "execution_plan": plan,
        "structured_data": structured_data,
        "freshness": freshness,
        "scope": {"organization_unit_ids": sorted(str(value) for value in execution_scope)},
        "tool_errors": tool_errors,
        "memory_used": bool(memory_enabled and memories),
        "assistant_output": assistant_output,
        "output_contract_version": ANSWER_CONTRACT_VERSION,
    }
    evidence_count = _save_answer_with_evidence(
        job_id=job.id,
        lease_token=job.lease_token,
        assistant_message_id=assistant_message_id,
        content=answer_content,
        response=answer_response,
        content_json=content_json,
        tool_results=tool_results,
    )
    return {
        "content": answer_content,
        "route": route["route"],
        "tools": content_json["tools"],
        "evidence_count": evidence_count,
    }
