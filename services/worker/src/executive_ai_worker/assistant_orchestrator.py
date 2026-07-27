from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any

import httpx
from executive_ai_api.anspire import AnspireConfigurationError, runtime_provider_config
from executive_ai_api.capabilities import issue_capability_token
from executive_ai_api.config import Settings
from executive_ai_api.database import SessionLocal
from executive_ai_api.hermes_client import (
    HermesRuntimeError,
    parse_json_response,
    run_hermes,
)
from executive_ai_api.models import (
    Clarification,
    Conversation,
    ConversationFile,
    FileAsset,
    FileChunk,
    FileExtraction,
    Job,
    Message,
    MessageEvidence,
    MessageRoute,
    MessageRun,
    ModelProviderConfig,
)
from executive_ai_api.security import utc_now
from sqlalchemy import case, literal, or_, select

from .file_extraction import embed_texts

ALL_TOOLS = {
    "list_query_scopes",
    "get_overall_business",
    "get_target_completion",
    "get_opportunity_funnel",
    "get_sales_forecast",
    "get_customer_status",
    "get_delivery_status",
    "get_finance_margin",
    "get_collection_aging",
    "get_organization_performance",
    "get_daily_changes",
}

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
        values = {
            uuid.UUID(str(value))
            for value in job.scope_snapshot_json.get("organization_unit_ids", [])
        }
    except ValueError as exc:
        raise OrchestrationPermanentError(
            "invalid_scope_snapshot", "任务权限快照无效", "当前查询范围无效"
        ) from exc
    if not values:
        raise OrchestrationPermanentError(
            "empty_scope_snapshot", "任务没有事业部权限", "当前账号没有可查询的数据范围"
        )
    return values


def _fallback_tool(question: str) -> str:
    for tool, hints in TOOL_HINTS.items():
        if any(hint in question for hint in hints):
            return tool
    return "get_overall_business"


def _conversation_files(
    conversation_id: uuid.UUID,
    enterprise_id: uuid.UUID,
    user_id: uuid.UUID,
) -> list[dict[str, Any]]:
    with SessionLocal() as db:
        rows = db.execute(
            select(FileAsset, FileExtraction)
            .join(ConversationFile, ConversationFile.file_id == FileAsset.id)
            .outerjoin(FileExtraction, FileExtraction.file_id == FileAsset.id)
            .where(
                ConversationFile.conversation_id == conversation_id,
                FileAsset.enterprise_id == enterprise_id,
                FileAsset.uploaded_by_user_id == user_id,
                FileAsset.deleted_at.is_(None),
            )
        ).all()
        return [
            {
                "id": str(file_asset.id),
                "name": file_asset.original_name,
                "extraction_status": extraction.status if extraction else "unsupported",
            }
            for file_asset, extraction in rows
        ]


def _route(
    job: Job,
    settings: Settings,
    question: str,
    files: list[dict[str, Any]],
    organization_ids: set[uuid.UUID],
    provider_config: dict[str, str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    response = run_hermes(
        settings,
        profile="route",
        request_id=str(job.id),
        payload={
            "question": question,
            "allowed_tools": sorted(ALL_TOOLS),
            "current_conversation_files": files,
            "organization_unit_ids": sorted(str(value) for value in organization_ids),
        },
        provider_config=provider_config,
    )
    route = parse_json_response(response["text"])
    route_name = str(route.get("route", "data"))
    if route_name not in {"data", "document", "mixed", "clarification"}:
        route_name = "data"
    tool = route.get("tool")
    if tool not in ALL_TOOLS:
        tool = _fallback_tool(question)
    route.update(
        {
            "route": route_name,
            "tool": tool,
            "rewritten_query": str(route.get("rewritten_query") or question),
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
        if existing is None:
            existing = MessageRoute(
                message_id=message_id,
                conversation_id=conversation_id,
                route=route["route"],
                profile="route",
                rewritten_query=route["rewritten_query"],
                scope_status=(
                    "clarification_required" if route["route"] == "clarification" else "authorized"
                ),
                rationale=str(route.get("reason") or ""),
                confidence=float(route.get("confidence") or 0),
                model_name=hermes_response.get("model"),
                completed_at=utc_now(),
            )
            db.add(existing)
        else:
            existing.route = route["route"]
            existing.rewritten_query = route["rewritten_query"]
            existing.scope_status = (
                "clarification_required" if route["route"] == "clarification" else "authorized"
            )
            existing.rationale = str(route.get("reason") or "")
            existing.confidence = float(route.get("confidence") or 0)
            existing.model_name = hermes_response.get("model")
            existing.completed_at = utc_now()


def _create_clarification(
    *,
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    assistant_message_id: uuid.UUID,
    route: dict[str, Any],
) -> dict[str, Any]:
    question = str(route.get("clarification_question") or "请确认需要查询的事业部范围。")
    options = route.get("clarification_options")
    if not isinstance(options, list):
        options = []
    normalized = [
        option if isinstance(option, dict) else {"label": str(option), "value": str(option)}
        for option in options[:20]
    ]
    with SessionLocal.begin() as db:
        clarification = Clarification(
            conversation_id=conversation_id,
            message_id=message_id,
            question=question,
            options_json=normalized,
            status="pending",
        )
        db.add(clarification)
        assistant = db.get(Message, assistant_message_id)
        if assistant:
            assistant.content = question
            assistant.content_json = {
                "route": "clarification",
                "clarification_id": str(clarification.id),
                "options": normalized,
            }
            assistant.status = "completed"
    return {"content": question, "route": "clarification"}


def _call_tool(
    *,
    settings: Settings,
    token: str,
    tool: str,
    organization_ids: set[uuid.UUID],
) -> dict[str, Any]:
    try:
        response = httpx.post(
            f"{settings.mcp_hub_url.rstrip('/')}/v1/tools/call",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "tool": tool,
                "arguments": {
                    "organization_unit_ids": sorted(str(value) for value in organization_ids)
                },
            },
            timeout=30,
        )
    except httpx.HTTPError as exc:
        raise RuntimeError(f"MCP Hub unavailable: {exc}") from exc
    if response.status_code >= 400:
        raise RuntimeError(f"MCP Hub rejected the query: {response.text[:1000]}")
    return response.json()


def _document_chunks(
    *,
    conversation_id: uuid.UUID,
    enterprise_id: uuid.UUID,
    user_id: uuid.UUID,
    query: str,
    settings: Settings,
) -> list[dict[str, Any]]:
    query_embedding = embed_texts([query], settings)[0]
    with SessionLocal() as db:
        statement = (
            select(FileChunk, FileAsset)
            .join(FileAsset, FileAsset.id == FileChunk.file_id)
            .join(ConversationFile, ConversationFile.file_id == FileChunk.file_id)
            .join(FileExtraction, FileExtraction.id == FileChunk.extraction_id)
            .where(
                ConversationFile.conversation_id == conversation_id,
                FileAsset.enterprise_id == enterprise_id,
                FileAsset.uploaded_by_user_id == user_id,
                FileAsset.deleted_at.is_(None),
                FileExtraction.status == "completed",
            )
        )
        if db.get_bind().dialect.name == "postgresql":
            vector_rows = db.execute(
                statement.where(FileChunk.embedding.is_not(None))
                .order_by(FileChunk.embedding.cosine_distance(query_embedding))
                .limit(32)
            ).all()
            terms = _query_terms(query)
            keyword_rows: list[Any] = []
            if terms:
                predicates = [FileChunk.content.contains(term, autoescape=True) for term in terms]
                keyword_score = literal(0)
                for predicate in predicates:
                    keyword_score += case((predicate, 1), else_=0)
                keyword_rows = db.execute(
                    statement.where(or_(*predicates))
                    .order_by(keyword_score.desc(), FileChunk.chunk_index)
                    .limit(32)
                ).all()
            rows = _reciprocal_rank_fusion(vector_rows, keyword_rows, limit=16)
        else:
            rows = db.execute(statement.limit(500)).all()
            terms = set(_query_terms(query))
            rows = sorted(
                rows,
                key=lambda row: sum(term in row[0].content for term in terms),
                reverse=True,
            )[:16]
        return [
            {
                "file_id": str(chunk.file_id),
                "file_name": file_asset.original_name,
                "locator": chunk.locator_json,
                "content": chunk.content,
            }
            for chunk, file_asset in rows
        ]


def _query_terms(query: str) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        normalized = value.strip().lower()
        if len(normalized) < 2 or normalized in seen:
            return
        seen.add(normalized)
        terms.append(normalized)

    for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9._:/-]{1,63}", query):
        add(token)
    sequences = re.findall(r"[\u3400-\u9fff]{2,}", query)
    for sequence in sequences:
        add(sequence)
    # Preserve exact identifiers and both short Chinese keywords (for example
    # “回款”) and longer entity phrases (for example “华东事业部”) before the
    # bounded term budget is exhausted.
    for size in (2, 6, 5, 4, 3):
        for sequence in sequences:
            for index in range(max(len(sequence) - size + 1, 0)):
                add(sequence[index : index + size])
                if len(terms) >= 32:
                    return terms
    return terms[:32]


def _reciprocal_rank_fusion(
    vector_rows: list[Any],
    keyword_rows: list[Any],
    *,
    limit: int,
) -> list[Any]:
    scores: dict[uuid.UUID, float] = {}
    rows_by_id: dict[uuid.UUID, Any] = {}
    for weight, rows in ((0.7, vector_rows), (0.3, keyword_rows)):
        for rank, row in enumerate(rows, start=1):
            chunk_id = row[0].id
            rows_by_id[chunk_id] = row
            scores[chunk_id] = scores.get(chunk_id, 0.0) + weight / (60 + rank)
    ranked_ids = sorted(scores, key=lambda chunk_id: (-scores[chunk_id], str(chunk_id)))
    return [rows_by_id[chunk_id] for chunk_id in ranked_ids[:limit]]


def _save_answer_with_evidence(
    *,
    assistant_message_id: uuid.UUID,
    content: str,
    response: dict[str, Any],
    content_json: dict[str, Any],
    tool: str | None,
    tool_result: dict[str, Any] | None,
) -> int:
    with SessionLocal.begin() as db:
        assistant = db.get(Message, assistant_message_id)
        if assistant is None:
            raise OrchestrationPermanentError(
                "assistant_message_missing", "回答占位消息不存在", "请求无法保存"
            )
        evidence_count = 0
        if tool and tool_result:
            for index, freshness_row in enumerate(tool_result.get("freshness", [])):
                source_data_as_of = freshness_row.get("source_data_as_of")
                if not source_data_as_of:
                    continue
                evidence_key = f"{tool}:{freshness_row['domain']}:{index}"
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
                evidence.value_json = tool_result.get("data", {})
                evidence.source_type = freshness_row.get("source_type", "unknown")
                evidence.source_display_name = freshness_row.get(
                    "source_display_name", "未知数据源"
                )
                evidence.source_data_as_of = datetime.fromisoformat(source_data_as_of)
                evidence.dataset_version = freshness_row.get("dataset_version")
                evidence.scope_json = tool_result.get("scope", {})
                evidence.query_json = {"tool": tool}
                evidence.row_references_json = tool_result.get("evidence", [])
                evidence_count += 1
        content_json["evidence_count"] = evidence_count
        assistant.content = content
        assistant.content_json = content_json
        assistant.status = "completed"
        assistant.model_name = response.get("model")
        freshness = content_json.get("freshness") or []
        timestamps = [
            row.get("source_data_as_of") for row in freshness if row.get("source_data_as_of")
        ]
        if timestamps:
            assistant.source_data_as_of = min(datetime.fromisoformat(value) for value in timestamps)
        message_run = db.scalar(
            select(MessageRun)
            .where(MessageRun.message_id == assistant.id)
            .order_by(MessageRun.created_at.desc())
        )
        if message_run is None:
            message_run = MessageRun(
                message_id=assistant.id,
            )
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
    organization_ids = _organization_ids(job)
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
                "anspire_not_configured",
                "企业尚未配置 Anspire 模型",
                "Anspire 模型尚未配置",
            )
        try:
            provider_config = runtime_provider_config(model_config, settings)
        except AnspireConfigurationError as exc:
            raise OrchestrationPermanentError(
                exc.code,
                str(exc),
                "Anspire 模型尚未配置或启用",
            ) from exc
    files = _conversation_files(
        conversation_id,
        job.enterprise_id,
        job.created_by_user_id,
    )
    try:
        route, route_response = _route(
            job,
            settings,
            question,
            files,
            organization_ids,
            provider_config,
        )
    except HermesRuntimeError as exc:
        if exc.permanent:
            raise OrchestrationPermanentError(
                exc.code,
                str(exc),
                "Anspire 模型尚未配置",
            ) from exc
        raise
    _save_route(message_id, conversation_id, route, route_response)
    if route["route"] == "clarification":
        return _create_clarification(
            conversation_id=conversation_id,
            message_id=message_id,
            assistant_message_id=assistant_message_id,
            route=route,
        )
    tool_result: dict[str, Any] | None = None
    chunks: list[dict[str, Any]] = []
    if route["route"] in {"data", "mixed"}:
        token = issue_capability_token(
            settings=settings,
            enterprise_id=job.enterprise_id,
            user_id=job.created_by_user_id,
            organization_unit_ids=organization_ids,
            tools={route["tool"]},
            message_id=message_id,
        )
        tool_result = _call_tool(
            settings=settings,
            token=token,
            tool=route["tool"],
            organization_ids=organization_ids,
        )
    if route["route"] in {"document", "mixed"}:
        if not files:
            return _create_clarification(
                conversation_id=conversation_id,
                message_id=message_id,
                assistant_message_id=assistant_message_id,
                route={
                    "clarification_question": "请先上传需要分析的 PDF、DOCX、XLSX 或 PPTX 文件。",
                    "clarification_options": [],
                },
            )
        if not any(item["extraction_status"] == "completed" for item in files):
            raise OrchestrationPermanentError(
                "file_not_ready", "当前会话文件尚未解析完成", "文件正在解析，完成后即可提问"
            )
        chunks = _document_chunks(
            conversation_id=conversation_id,
            enterprise_id=job.enterprise_id,
            user_id=job.created_by_user_id,
            query=route["rewritten_query"],
            settings=settings,
        )
    profile = "document" if route["route"] == "document" else "data"
    answer_payload = {
        "question": question,
        "rewritten_query": route["rewritten_query"],
        "authorized_result": tool_result,
        "current_conversation_chunks": chunks,
    }
    try:
        answer_response = run_hermes(
            settings,
            profile=profile,
            payload=answer_payload,
            request_id=str(job.id),
            provider_config=provider_config,
        )
    except HermesRuntimeError as exc:
        if exc.permanent:
            raise OrchestrationPermanentError(exc.code, str(exc), "无法连接已配置模型") from exc
        raise
    content_json = {
        "route": route["route"],
        "tool": route["tool"] if tool_result else None,
        "structured_data": tool_result.get("data", {}) if tool_result else {},
        "freshness": tool_result.get("freshness", []) if tool_result else [],
        "scope": tool_result.get("scope", {}) if tool_result else {},
        "file_citations": [
            {
                "file_id": chunk["file_id"],
                "file_name": chunk["file_name"],
                "locator": chunk["locator"],
            }
            for chunk in chunks
        ],
    }
    evidence_count = _save_answer_with_evidence(
        assistant_message_id=assistant_message_id,
        content=answer_response["text"],
        response=answer_response,
        content_json=content_json,
        tool=route["tool"] if tool_result else None,
        tool_result=tool_result,
    )
    return {
        "content": answer_response["text"],
        "route": route["route"],
        "tool": route["tool"] if tool_result else None,
        "evidence_count": evidence_count,
    }
