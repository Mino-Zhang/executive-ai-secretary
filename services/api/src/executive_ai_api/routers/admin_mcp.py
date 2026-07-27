from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import record_audit
from ..authz import Principal, require_roles
from ..database import get_db
from ..errors import AppError
from ..mcp_registry import MCP_TOOL_SPECS, effective_catalog
from ..models import DataDomainStatus, McpToolConfig
from ..schemas import (
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


def _decorate(item: dict[str, Any], readiness: dict[str, bool]) -> McpToolOut:
    issues = [domain for domain in item["domains"] if not readiness.get(domain, False)]
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
    tools = [_decorate(item, readiness) for item in effective_catalog(db, principal.enterprise_id)]
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


@router.patch("/{tool_name}", response_model=McpToolOut)
def update_mcp_tool(
    tool_name: str,
    payload: McpToolUpdate,
    request: Request,
    principal: OperationsPrincipal,
    db: Annotated[Session, Depends(get_db)],
) -> McpToolOut:
    spec = MCP_TOOL_SPECS.get(tool_name)
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
    if tool_name not in MCP_TOOL_SPECS:
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
