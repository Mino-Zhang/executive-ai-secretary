from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..authz import (
    Principal,
    accessible_organization_unit_ids,
    get_executive_principal,
)
from ..database import get_db
from ..models import DataDomainStatus
from ..schemas import DataCapabilitiesOut, DataDomainStatusOut
from ..security import utc_now

router = APIRouter(tags=["data"])

DOMAIN_CAPABILITIES = {
    "opportunity": ["overall", "pipeline", "forecast", "customer", "organization"],
    "delivery": ["overall", "delivery", "customer", "organization", "daily_change"],
    "collection": ["overall", "finance", "collection", "customer", "organization", "daily_change"],
    "target": ["overall", "target", "organization"],
}


@router.get("/data-capabilities", response_model=DataCapabilitiesOut)
def get_data_capabilities(
    principal: Annotated[Principal, Depends(get_executive_principal)],
    db: Annotated[Session, Depends(get_db)],
) -> DataCapabilitiesOut:
    rows = db.scalars(
        select(DataDomainStatus)
        .where(DataDomainStatus.enterprise_id == principal.enterprise_id)
        .order_by(DataDomainStatus.domain)
    ).all()
    capabilities: dict[str, bool] = {
        name: False for names in DOMAIN_CAPABILITIES.values() for name in names
    }
    for row in rows:
        if row.status in {"fresh", "stale", "partial"} and row.active_sync_run_id:
            for name in DOMAIN_CAPABILITIES.get(row.domain, []):
                capabilities[name] = True
    source_types = {row.source_type for row in rows}
    source_labels = {row.source_display_name for row in rows}
    overall_status = "unavailable"
    if rows:
        if any(row.status == "failed" for row in rows):
            overall_status = "partial" if any(capabilities.values()) else "failed"
        elif any(row.status == "stale" for row in rows):
            overall_status = "stale"
        elif all(row.status == "fresh" for row in rows):
            overall_status = "fresh"
        else:
            overall_status = "partial"
    return DataCapabilitiesOut(
        source_kind=next(iter(source_types), "not_configured"),
        source_label=" / ".join(sorted(source_labels)) or "尚未配置数据源",
        organization_unit_ids=sorted(accessible_organization_unit_ids(db, principal), key=str),
        capabilities=capabilities,
        domains=[DataDomainStatusOut.model_validate(row) for row in rows],
        overall_status=overall_status,
        generated_at=utc_now(),
    )
