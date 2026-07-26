from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..authz import Principal, accessible_organization_unit_ids, get_executive_principal
from ..database import get_db
from ..models import OrganizationUnit
from ..schemas import OrganizationUnitOut, Page

router = APIRouter(prefix="/organization-units", tags=["organization-units"])


@router.get("", response_model=Page)
def list_organization_units(
    principal: Annotated[Principal, Depends(get_executive_principal)],
    db: Annotated[Session, Depends(get_db)],
    enabled_for_analysis: bool | None = Query(default=None),
) -> Page:
    ids = accessible_organization_unit_ids(db, principal)
    statement = select(OrganizationUnit).where(
        OrganizationUnit.enterprise_id == principal.enterprise_id,
        OrganizationUnit.id.in_(ids),
        OrganizationUnit.is_active.is_(True),
    )
    if enabled_for_analysis is not None:
        statement = statement.where(OrganizationUnit.enabled_for_analysis.is_(enabled_for_analysis))
        if enabled_for_analysis:
            statement = statement.where(OrganizationUnit.data_connected.is_(True))
    items = db.scalars(statement.order_by(OrganizationUnit.sort_order, OrganizationUnit.name)).all()
    return Page(items=[OrganizationUnitOut.model_validate(item) for item in items])
