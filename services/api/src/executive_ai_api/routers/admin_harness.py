from __future__ import annotations

import math
import uuid
from collections import Counter, defaultdict
from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..anspire import AnspireConfigurationError, runtime_provider_config
from ..audit import record_audit
from ..authz import Principal, require_roles
from ..config import Settings, get_settings
from ..database import get_db
from ..errors import AppError
from ..harness_config import (
    HARNESS_SCHEMA_VERSION,
    SAFETY_KERNEL_SUMMARY,
    active_harness_config,
    apply_glossary,
    config_hash,
    match_fast_rule,
    next_harness_version,
    validate_harness_config,
)
from ..hermes_client import HermesRuntimeError, parse_json_response, run_hermes
from ..mcp_registry import effective_catalog
from ..models import (
    Conversation,
    HarnessConfigVersion,
    HarnessDiagnosticGrant,
    HarnessStageRun,
    Message,
    MessageRoute,
    ModelProviderConfig,
    OrganizationUnit,
)
from ..query_spec import normalize_query_spec
from ..schemas import (
    HarnessConfigOut,
    HarnessConfigUpdate,
    HarnessMetricsOut,
    HarnessSimulationOut,
    HarnessSimulationRequest,
    HarnessTraceOut,
    HarnessVersionOut,
    OrganizationScopeInput,
)
from ..security import utc_now

router = APIRouter(prefix="/admin/harness", tags=["admin-harness"])
OperationsPrincipal = Annotated[Principal, Depends(require_roles("enterprise_admin", "fde"))]


def _config_out(row: HarnessConfigVersion) -> HarnessConfigOut:
    return HarnessConfigOut(
        id=row.id,
        version=row.version,
        schema_version=row.schema_version,
        config_hash=row.config_hash,
        config=row.config_json,
        safety_kernel=SAFETY_KERNEL_SUMMARY,
        activated_at=row.activated_at,
        updated_at=row.updated_at,
    )


@router.get("/config", response_model=HarnessConfigOut)
def get_harness_config(
    principal: OperationsPrincipal,
    db: Annotated[Session, Depends(get_db)],
) -> HarnessConfigOut:
    row = active_harness_config(db, principal.enterprise_id)
    db.commit()
    return _config_out(row)


@router.patch("/config", response_model=HarnessConfigOut)
def update_harness_config(
    payload: HarnessConfigUpdate,
    request: Request,
    principal: OperationsPrincipal,
    db: Annotated[Session, Depends(get_db)],
) -> HarnessConfigOut:
    clean = validate_harness_config(payload.config)
    current = db.scalar(
        select(HarnessConfigVersion)
        .where(
            HarnessConfigVersion.enterprise_id == principal.enterprise_id,
            HarnessConfigVersion.is_active.is_(True),
        )
        .with_for_update()
    )
    if current is None:
        current = active_harness_config(db, principal.enterprise_id)
    if current.version != payload.base_version:
        raise AppError(
            409,
            "harness_version_conflict",
            "编排策略已被其他管理员更新，请刷新后重试",
            {"current_version": current.version},
        )
    current.is_active = False
    row = HarnessConfigVersion(
        enterprise_id=principal.enterprise_id,
        version=next_harness_version(db, principal.enterprise_id),
        schema_version=HARNESS_SCHEMA_VERSION,
        config_json=clean,
        config_hash=config_hash(clean),
        is_active=True,
        source_version_id=current.id,
        created_by_user_id=principal.user.id,
        activated_at=utc_now(),
    )
    db.add(row)
    db.flush()
    record_audit(
        db,
        request,
        "admin.harness_config_updated",
        actor=principal.user,
        session=principal.session,
        target_type="harness_config_version",
        target_id=row.id,
        metadata={
            "previous_version": current.version,
            "version": row.version,
            "config_hash": row.config_hash,
        },
    )
    db.commit()
    db.refresh(row)
    return _config_out(row)


@router.get("/versions", response_model=list[HarnessVersionOut])
def list_harness_versions(
    principal: OperationsPrincipal,
    db: Annotated[Session, Depends(get_db)],
    limit: int = Query(default=30, ge=1, le=100),
) -> list[HarnessVersionOut]:
    rows = db.scalars(
        select(HarnessConfigVersion)
        .where(HarnessConfigVersion.enterprise_id == principal.enterprise_id)
        .order_by(HarnessConfigVersion.version.desc())
        .limit(limit)
    ).all()
    return [HarnessVersionOut.model_validate(row, from_attributes=True) for row in rows]


@router.post("/versions/{version_id}/restore", response_model=HarnessConfigOut)
def restore_harness_version(
    version_id: uuid.UUID,
    request: Request,
    principal: OperationsPrincipal,
    db: Annotated[Session, Depends(get_db)],
) -> HarnessConfigOut:
    source = db.scalar(
        select(HarnessConfigVersion).where(
            HarnessConfigVersion.id == version_id,
            HarnessConfigVersion.enterprise_id == principal.enterprise_id,
        )
    )
    if source is None:
        raise AppError(404, "harness_version_not_found", "编排版本不存在")
    current = db.scalar(
        select(HarnessConfigVersion)
        .where(
            HarnessConfigVersion.enterprise_id == principal.enterprise_id,
            HarnessConfigVersion.is_active.is_(True),
        )
        .with_for_update()
    )
    if current:
        current.is_active = False
    clean = validate_harness_config(source.config_json)
    row = HarnessConfigVersion(
        enterprise_id=principal.enterprise_id,
        version=next_harness_version(db, principal.enterprise_id),
        schema_version=HARNESS_SCHEMA_VERSION,
        config_json=clean,
        config_hash=config_hash(clean),
        is_active=True,
        source_version_id=source.id,
        created_by_user_id=principal.user.id,
        activated_at=utc_now(),
    )
    db.add(row)
    db.flush()
    record_audit(
        db,
        request,
        "admin.harness_version_restored",
        actor=principal.user,
        session=principal.session,
        target_type="harness_config_version",
        target_id=row.id,
        metadata={"restored_from": source.version, "new_version": row.version},
    )
    db.commit()
    db.refresh(row)
    return _config_out(row)


def _simulation_scope(
    db: Session,
    enterprise_id: uuid.UUID,
    raw_scope: dict[str, Any] | None,
) -> tuple[OrganizationScopeInput, list[uuid.UUID]]:
    available = set(
        db.scalars(
            select(OrganizationUnit.id).where(
                OrganizationUnit.enterprise_id == enterprise_id,
                OrganizationUnit.is_active.is_(True),
                OrganizationUnit.enabled_for_analysis.is_(True),
            )
        ).all()
    )
    scope = (
        OrganizationScopeInput.model_validate(raw_scope)
        if raw_scope
        else OrganizationScopeInput(mode="all_authorized", organization_unit_ids=[])
    )
    if scope.mode == "selected":
        selected = set(scope.organization_unit_ids)
        if not selected.issubset(available):
            raise AppError(422, "invalid_organization_scope", "模拟范围包含不可分析的事业部")
        return scope, sorted(selected, key=str)
    return scope, sorted(available, key=str)


@router.post("/simulate", response_model=HarnessSimulationOut)
def simulate_harness(
    payload: HarnessSimulationRequest,
    request: Request,
    principal: OperationsPrincipal,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> HarnessSimulationOut:
    current = active_harness_config(db, principal.enterprise_id)
    config = validate_harness_config(payload.config or current.config_json)
    scope, resolved_ids = _simulation_scope(
        db, principal.enterprise_id, payload.organization_scope
    )
    rule = match_fast_rule(payload.question, config)
    route = str(rule["route"]) if rule else ""
    route_source = "fast_rule" if rule else "hermes"
    model_config = db.scalar(
        select(ModelProviderConfig).where(
            ModelProviderConfig.enterprise_id == principal.enterprise_id
        )
    )
    if model_config is None:
        raise AppError(409, "anspire_not_configured", "请先配置并启用 Anspire 模型")
    try:
        provider = runtime_provider_config(model_config, settings)
        if not route:
            response = run_hermes(
                settings,
                profile="route",
                request_id=f"simulate:{uuid.uuid4()}:route",
                payload={
                    "question": payload.question,
                    "harness_config": config,
                    "organization_count": len(resolved_ids),
                },
                provider_config=provider,
            )
            route_data = parse_json_response(response["text"])
            route = str(route_data.get("route") or "clarification")
        if route not in {"data", "general", "clarification"}:
            route = "clarification"
            route_source = "validation"
        query_spec: dict[str, Any] = {}
        if route == "data":
            rewritten_question = apply_glossary(payload.question, config)
            response = run_hermes(
                settings,
                profile="rewrite",
                request_id=f"simulate:{uuid.uuid4()}:rewrite",
                payload={
                    "question": rewritten_question,
                    "harness_config": config,
                    "organization_scope": {
                        "mode": scope.mode,
                        "organization_unit_ids": [str(item) for item in resolved_ids],
                    },
                },
                provider_config=provider,
            )
            query_spec = normalize_query_spec(
                parse_json_response(response["text"]),
                question=rewritten_question,
                organization_scope={
                    "mode": scope.mode,
                    "organization_unit_ids": [str(item) for item in resolved_ids],
                },
            )
    except (AnspireConfigurationError, HermesRuntimeError) as exc:
        raise AppError(422, getattr(exc, "code", "harness_simulation_failed"), str(exc)) from exc

    planner_tools = {
        item["tool_name"]
        for item in effective_catalog(db, principal.enterprise_id)
        if item["is_enabled"] and item["planner_enabled"]
    }
    candidate_tools = [
        name for name in (rule or {}).get("candidate_tools", []) if name in planner_tools
    ]
    issues = list(query_spec.get("unresolved_ambiguities", []))
    record_audit(
        db,
        request,
        "admin.harness_simulated",
        actor=principal.user,
        session=principal.session,
        target_type="harness_config_version",
        target_id=current.id,
        metadata={
            "route": route,
            "route_source": route_source,
            "scope_count": len(resolved_ids),
            "config_hash": config_hash(config),
        },
    )
    db.commit()
    return HarnessSimulationOut(
        route=route,
        route_source=route_source,
        matched_rule_id=str(rule["id"]) if rule else None,
        candidate_tools=candidate_tools,
        query_spec=query_spec,
        validation_issues=issues,
        config_hash=config_hash(config),
    )


def _p95(values: list[int]) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)]


@router.get("/metrics", response_model=HarnessMetricsOut)
def harness_metrics(
    principal: OperationsPrincipal,
    db: Annotated[Session, Depends(get_db)],
    days: int = Query(default=30, ge=1, le=90),
) -> HarnessMetricsOut:
    cutoff = utc_now() - timedelta(days=days)
    routes = db.scalars(
        select(MessageRoute).where(
            MessageRoute.created_at >= cutoff,
            MessageRoute.conversation_id.in_(
                select(Conversation.id).where(
                    Conversation.enterprise_id == principal.enterprise_id
                )
            ),
        )
    ).all()
    stages = db.scalars(
        select(HarnessStageRun).where(
            HarnessStageRun.enterprise_id == principal.enterprise_id,
            HarnessStageRun.created_at >= cutoff,
        )
    ).all()
    stage_latencies: dict[str, list[int]] = defaultdict(list)
    for row in stages:
        if row.latency_ms is not None:
            stage_latencies[row.stage].append(row.latency_ms)
    execution = [row for row in stages if row.stage == "mcp_execution"]
    return HarnessMetricsOut(
        window_days=days,
        message_count=len(routes),
        intent_accuracy_sample_size=0,
        structured_output_rate=(
            sum(bool(row.query_spec_json) or row.route == "general" for row in routes)
            / len(routes)
            if routes
            else 0.0
        ),
        tool_success_rate=(
            sum(row.status == "completed" for row in execution) / len(execution)
            if execution
            else 0.0
        ),
        route_counts=dict(Counter(row.route for row in routes)),
        stage_latency_p95_ms={
            stage: _p95(values) for stage, values in stage_latencies.items()
        },
    )


def _query_spec_summary(route: MessageRoute | None) -> dict[str, Any]:
    spec = route.query_spec_json if route else {}
    return {
        "metric_count": len(spec.get("metrics", [])),
        "analysis_goal_count": len(spec.get("analysis_goals", [])),
        "entity_types": sorted(spec.get("entities", {}).keys())[:12],
        "has_time_range": bool(spec.get("time_range")),
        "filter_count": len(spec.get("filters", [])),
        "ambiguity_count": len(spec.get("unresolved_ambiguities", [])),
    }


def _trace_out(
    db: Session,
    principal: Principal,
    route: MessageRoute,
) -> HarnessTraceOut:
    stages = db.scalars(
        select(HarnessStageRun)
        .where(HarnessStageRun.message_id == route.message_id)
        .order_by(HarnessStageRun.created_at, HarnessStageRun.id)
    ).all()
    version = (
        db.get(HarnessConfigVersion, route.harness_version_id)
        if route.harness_version_id
        else None
    )
    question = db.get(Message, route.message_id)
    answer = db.scalar(
        select(Message)
        .where(
            Message.conversation_id == route.conversation_id,
            Message.role == "assistant",
            Message.sequence > (question.sequence if question else -1),
        )
        .order_by(Message.sequence)
        .limit(1)
    )
    grant = db.scalar(
        select(HarnessDiagnosticGrant).where(
            HarnessDiagnosticGrant.message_id == (answer.id if answer else route.message_id),
            HarnessDiagnosticGrant.enterprise_id == principal.enterprise_id,
            HarnessDiagnosticGrant.revoked_at.is_(None),
            HarnessDiagnosticGrant.expires_at > utc_now(),
        )
    )
    shared_content = None
    if grant:
        shared_content = {
            "question": question.content if question else None,
            "rewritten_query": route.rewritten_query,
            "query_spec": route.query_spec_json,
            "plan": (answer.content_json or {}).get("execution_plan") if answer else None,
            "answer": answer.content if answer else None,
        }
    tools = sorted({name for stage in stages for name in stage.tool_names_json})
    organization_count = max(
        [int(stage.summary_json.get("organization_unit_count", 0)) for stage in stages] or [0]
    )
    return HarnessTraceOut(
        message_id=answer.id if answer else route.message_id,
        conversation_id=route.conversation_id,
        route=route.route,
        route_source=route.route_source,
        query_spec_summary=_query_spec_summary(route),
        harness_version=version.version if version else None,
        organization_unit_count=organization_count,
        tools=tools,
        stages=[
            {
                "stage": row.stage,
                "status": row.status,
                "model_name": row.model_name,
                "latency_ms": row.latency_ms,
                "input_tokens": row.input_tokens,
                "output_tokens": row.output_tokens,
                "tool_names": row.tool_names_json,
                "summary": row.summary_json,
                "error_code": row.error_code,
                "created_at": row.created_at,
            }
            for row in stages
        ],
        diagnostic_shared_until=grant.expires_at if grant else None,
        shared_content=shared_content,
    )


@router.get("/traces", response_model=list[HarnessTraceOut])
def list_harness_traces(
    principal: OperationsPrincipal,
    db: Annotated[Session, Depends(get_db)],
    limit: int = Query(default=50, ge=1, le=100),
) -> list[HarnessTraceOut]:
    rows = db.scalars(
        select(MessageRoute)
        .where(
            MessageRoute.conversation_id.in_(
                select(Conversation.id).where(
                    Conversation.enterprise_id == principal.enterprise_id
                )
            )
        )
        .order_by(MessageRoute.created_at.desc())
        .limit(limit)
    ).all()
    return [_trace_out(db, principal, row) for row in rows]


@router.get("/traces/{message_id}", response_model=HarnessTraceOut)
def get_harness_trace(
    message_id: uuid.UUID,
    principal: OperationsPrincipal,
    db: Annotated[Session, Depends(get_db)],
) -> HarnessTraceOut:
    message = db.scalar(
        select(Message).where(
            Message.id == message_id,
            Message.conversation_id.in_(
                select(Conversation.id).where(
                    Conversation.enterprise_id == principal.enterprise_id
                )
            ),
        )
    )
    route_message_id = message_id
    if message and message.role == "assistant":
        route_message_id = db.scalar(
            select(Message.id)
            .where(
                Message.conversation_id == message.conversation_id,
                Message.role == "user",
                Message.sequence < message.sequence,
            )
            .order_by(Message.sequence.desc())
            .limit(1)
        ) or message_id
    route = db.scalar(
        select(MessageRoute).where(
            MessageRoute.message_id == route_message_id,
            MessageRoute.conversation_id.in_(
                select(Conversation.id).where(
                    Conversation.enterprise_id == principal.enterprise_id
                )
            ),
        )
    )
    if route is None:
        raise AppError(404, "harness_trace_not_found", "编排追踪不存在")
    return _trace_out(db, principal, route)
