from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..audit import record_audit
from ..authz import Principal, require_roles
from ..database import get_db
from ..errors import AppError
from ..mcp_registry import (
    MCP_TOOL_SPECS,
    effective_catalog,
    registered_spec,
)
from ..models import DataDomainStatus, McpToolConfig, McpToolDefinition
from ..schemas import (
    McpCompositeToolCreate,
    McpToolCatalogOut,
    McpToolOut,
    McpToolUpdate,
    McpToolValidationOut,
)
from ..security import utc_now

router = APIRouter(prefix="/admin/mcp-tools", tags=["admin-mcp-tools"])
OperationsPrincipal = Annotated[Principal, Depends(require_roles("enterprise_admin", "fde"))]


def _domain_readiness(db: Session, principal: Principal) -> dict[str, bool]:
    rows = db.scalars(
        select(DataDomainStatus).where(
            DataDomainStatus.enterprise_id == principal.enterprise_id
        )
    ).all()
    return {
        row.domain: bool(
            row.status in {"fresh", "stale", "partial"} and row.active_sync_run_id
        )
        for row in rows
    }


def _decorate(
    item: dict[str, Any],
    readiness: dict[str, bool],
    catalog_by_name: dict[str, dict[str, Any]],
) -> McpToolOut:
    issues = [domain for domain in item["domains"] if not readiness.get(domain, False)]
    if item["source_type"] == "composite":
        for component in item["component_tools"]:
            dependency = catalog_by_name.get(component)
            if dependency is None:
                issues.append(f"依赖工具不存在：{component}")
            elif not dependency["is_enabled"]:
                issues.append(f"依赖工具已停用：{dependency['display_name']}")
    if not item["is_enabled"]:
        state = "disabled"
        messages = ["工具已由企业管理员停用"]
    elif issues:
        state = "data_unavailable"
        messages = [f"数据域尚不可用：{domain}" for domain in issues]
    else:
        state = "ready"
        messages = []
    return McpToolOut(
        **item,
        readiness=state,
        readiness_issues=messages,
    )


def _catalog(db: Session, principal: Principal) -> McpToolCatalogOut:
    readiness = _domain_readiness(db, principal)
    raw_catalog = effective_catalog(db, principal.enterprise_id)
    catalog_by_name = {item["tool_name"]: item for item in raw_catalog}
    tools = [_decorate(item, readiness, catalog_by_name) for item in raw_catalog]
    return McpToolCatalogOut(
        tools=tools,
        enabled_count=sum(item.is_enabled for item in tools),
        planner_count=sum(item.is_enabled and item.planner_enabled for item in tools),
        generated_at=utc_now(),
    )


@router.get("", response_model=McpToolCatalogOut)
def list_mcp_tools(
    principal: OperationsPrincipal,
    db: Annotated[Session, Depends(get_db)],
) -> McpToolCatalogOut:
    return _catalog(db, principal)


@router.post("", response_model=McpToolOut, status_code=201)
def create_mcp_tool(
    payload: McpCompositeToolCreate,
    request: Request,
    principal: OperationsPrincipal,
    db: Annotated[Session, Depends(get_db)],
) -> McpToolOut:
    if registered_spec(db, principal.enterprise_id, payload.tool_name) is not None:
        raise AppError(409, "mcp_tool_name_conflict", "工具标识已经存在")
    component_names = list(dict.fromkeys(payload.component_tools))
    if len(component_names) != len(payload.component_tools):
        raise AppError(422, "invalid_mcp_components", "组合工具不能重复选择同一个依赖工具")
    unknown = [name for name in component_names if name not in MCP_TOOL_SPECS]
    if unknown:
        raise AppError(
            422,
            "invalid_mcp_components",
            f"组合工具只能依赖系统内置工具：{', '.join(unknown)}",
        )

    domains: list[str] = []
    parameters: dict[str, dict[str, Any]] = {}
    for name in component_names:
        spec = MCP_TOOL_SPECS[name]
        for domain in spec.domains:
            if domain not in domains:
                domains.append(domain)
        for parameter, schema in spec.parameters.items():
            existing = parameters.get(parameter)
            if existing is not None and existing != schema:
                raise AppError(
                    422,
                    "incompatible_mcp_parameters",
                    f"依赖工具的参数定义冲突：{parameter}",
                )
            parameters[parameter] = schema

    definition = McpToolDefinition(
        enterprise_id=principal.enterprise_id,
        tool_name=payload.tool_name,
        display_name=payload.display_name.strip(),
        description=payload.description.strip(),
        category=payload.category.strip(),
        tool_type="composite",
        component_tools_json=component_names,
        domains_json=domains,
        parameters_json=parameters,
        version=1,
        created_by_user_id=principal.user.id,
        updated_by_user_id=principal.user.id,
    )
    config = McpToolConfig(
        enterprise_id=principal.enterprise_id,
        tool_name=payload.tool_name,
        display_name=payload.display_name.strip(),
        description=payload.description.strip(),
        is_enabled=False,
        planner_enabled=False,
        timeout_seconds=min(
            60,
            max(MCP_TOOL_SPECS[name].default_timeout_seconds for name in component_names) + 5,
        ),
        max_rows=min(MCP_TOOL_SPECS[name].default_limit for name in component_names),
        operator_note=payload.operator_note,
        updated_by_user_id=principal.user.id,
    )
    db.add_all([definition, config])
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(409, "mcp_tool_name_conflict", "工具标识已经存在") from exc
    record_audit(
        db,
        request,
        "admin.mcp_tool_created",
        actor=principal.user,
        session=principal.session,
        target_type="mcp_tool",
        target_id=payload.tool_name,
        metadata={"tool_type": "composite", "component_tools": component_names},
    )
    db.commit()
    return next(
        item for item in _catalog(db, principal).tools if item.tool_name == payload.tool_name
    )


@router.patch("/{tool_name}", response_model=McpToolOut)
def update_mcp_tool(
    tool_name: str,
    payload: McpToolUpdate,
    request: Request,
    principal: OperationsPrincipal,
    db: Annotated[Session, Depends(get_db)],
) -> McpToolOut:
    spec = registered_spec(db, principal.enterprise_id, tool_name)
    if spec is None:
        raise AppError(404, "mcp_tool_not_found", "MCP 工具不存在")
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("is_enabled") is False:
        changes["planner_enabled"] = False
    row = db.scalar(
        select(McpToolConfig).where(
            McpToolConfig.enterprise_id == principal.enterprise_id,
            McpToolConfig.tool_name == tool_name,
        )
    )
    if row is None:
        row = McpToolConfig(
            enterprise_id=principal.enterprise_id,
            tool_name=tool_name,
            display_name=spec.display_name,
            description=spec.description,
            is_enabled=True,
            planner_enabled=True,
            timeout_seconds=spec.default_timeout_seconds,
            max_rows=spec.default_limit,
        )
        db.add(row)
    for key, value in changes.items():
        setattr(row, key, value)
    row.updated_by_user_id = principal.user.id
    record_audit(
        db,
        request,
        "admin.mcp_tool_updated",
        actor=principal.user,
        session=principal.session,
        target_type="mcp_tool",
        target_id=tool_name,
        metadata={"fields": sorted(changes)},
    )
    db.commit()
    return next(
        item for item in _catalog(db, principal).tools if item.tool_name == tool_name
    )


@router.post("/{tool_name}/validate", response_model=McpToolValidationOut)
def validate_mcp_tool(
    tool_name: str,
    request: Request,
    principal: OperationsPrincipal,
    db: Annotated[Session, Depends(get_db)],
) -> McpToolValidationOut:
    if registered_spec(db, principal.enterprise_id, tool_name) is None:
        raise AppError(404, "mcp_tool_not_found", "MCP 工具不存在")
    tool = next(
        item for item in _catalog(db, principal).tools if item.tool_name == tool_name
    )
    ready = tool.readiness == "ready"
    record_audit(
        db,
        request,
        "admin.mcp_tool_validated",
        actor=principal.user,
        session=principal.session,
        target_type="mcp_tool",
        target_id=tool_name,
        outcome="success" if ready else "failure",
        failure_reason_code=None if ready else "mcp_tool_not_ready",
        metadata={"issues": tool.readiness_issues},
    )
    db.commit()
    return McpToolValidationOut(tool=tool, ready=ready, issues=tool.readiness_issues)
