from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..authz import (
    Principal,
    accessible_organization_unit_ids,
    get_executive_principal,
)
from ..config import Settings, get_settings
from ..daily_brief import build_daily_brief
from ..data_freshness import effective_domain_status
from ..database import get_db
from ..errors import AppError
from ..models import DataDomainStatus, OrganizationUnit
from ..schemas import DailyBriefOut, DataCapabilitiesOut, DataDomainStatusOut
from ..security import utc_now

router = APIRouter(tags=["data"])

DOMAIN_CAPABILITIES = {
    "opportunity": ["overall", "pipeline", "forecast", "customer", "organization"],
    "delivery": ["overall", "delivery", "customer", "organization", "daily_change"],
    "collection": ["overall", "finance", "collection", "customer", "organization", "daily_change"],
    "target": ["overall", "target", "organization"],
}


@router.get("/daily-brief", response_model=DailyBriefOut)
def get_daily_brief(
    principal: Annotated[Principal, Depends(get_executive_principal)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    organization_unit_ids: Annotated[list[uuid.UUID] | None, Query()] = None,
) -> DailyBriefOut:
    connected = set(
        db.scalars(
            select(OrganizationUnit.id).where(
                OrganizationUnit.enterprise_id == principal.enterprise_id,
                OrganizationUnit.is_active.is_(True),
                OrganizationUnit.enabled_for_analysis.is_(True),
                OrganizationUnit.data_connected.is_(True),
            )
        ).all()
    )
    allowed = accessible_organization_unit_ids(db, principal) & connected
    if not allowed:
        raise AppError(403, "data_scope_forbidden", "当前账号没有有效的事业部查询范围")

    requested = set(organization_unit_ids) if organization_unit_ids else set(allowed)
    if not requested or not requested.issubset(allowed):
        raise AppError(403, "data_scope_forbidden", "请求的事业部不在您的可查询范围内")

    return build_daily_brief(
        db,
        enterprise_id=principal.enterprise_id,
        organization_unit_ids=requested,
        connected_organization_unit_ids=connected,
        settings=settings,
    )


@router.get("/data-capabilities", response_model=DataCapabilitiesOut)
def get_data_capabilities(
    principal: Annotated[Principal, Depends(get_executive_principal)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> DataCapabilitiesOut:
    rows = db.scalars(
        select(DataDomainStatus)
        .where(DataDomainStatus.enterprise_id == principal.enterprise_id)
        .order_by(DataDomainStatus.domain)
    ).all()
    capabilities: dict[str, bool] = {
        name: False for names in DOMAIN_CAPABILITIES.values() for name in names
    }
    effective_statuses = {
        row.id: effective_domain_status(row, settings.data_stale_after_hours) for row in rows
    }
    for row in rows:
        if effective_statuses[row.id] in {"fresh", "stale", "partial"} and row.active_sync_run_id:
            for name in DOMAIN_CAPABILITIES.get(row.domain, []):
                capabilities[name] = True
    source_types = {row.source_type for row in rows}
    source_labels = {row.source_display_name for row in rows}
    overall_status = "unavailable"
    if rows:
        statuses = [effective_statuses[row.id] for row in rows]
        if any(value == "failed" for value in statuses):
            overall_status = "partial" if any(capabilities.values()) else "failed"
        elif any(value == "stale" for value in statuses):
            overall_status = "stale"
        elif all(value == "fresh" for value in statuses):
            overall_status = "fresh"
        else:
            overall_status = "partial"
    return DataCapabilitiesOut(
        source_kind=next(iter(source_types), "not_configured"),
        source_label=" / ".join(sorted(source_labels)) or "尚未配置数据源",
        organization_unit_ids=sorted(accessible_organization_unit_ids(db, principal), key=str),
        capabilities=capabilities,
        domains=[
            DataDomainStatusOut.model_validate(row).model_copy(
                update={"status": effective_statuses[row.id]}
            )
            for row in rows
        ],
        overall_status=overall_status,
        generated_at=utc_now(),
    )
