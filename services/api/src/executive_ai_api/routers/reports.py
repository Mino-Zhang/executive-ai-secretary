from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..authz import Principal, assert_org_scope, get_executive_principal
from ..database import get_db
from ..errors import AppError
from ..models import Report, ReportVersion
from ..schemas import Page, ReportOut

router = APIRouter(prefix="/reports", tags=["reports"])


def owned_report(db: Session, principal: Principal, report_id: uuid.UUID) -> Report:
    item = db.scalar(
        select(Report).where(
            Report.id == report_id,
            Report.enterprise_id == principal.enterprise_id,
            # Executive reports remain creator-private in phase one.
            Report.created_by_user_id == principal.user.id,
        )
    )
    if item is None:
        raise AppError(404, "report_not_found", "简报不存在")
    assert_org_scope(db, principal, item.organization_unit_id)
    return item


def report_output(db: Session, item: Report, include_content: bool = True) -> ReportOut:
    version = db.scalar(
        select(ReportVersion)
        .where(ReportVersion.report_id == item.id)
        .order_by(ReportVersion.version.desc())
        .limit(1)
    )
    output = ReportOut.model_validate(item)
    return output.model_copy(
        update={
            "latest_version": version.version if version else None,
            "content": version.content_json if version and include_content else None,
        }
    )


@router.get("", response_model=Page)
def list_reports(
    principal: Annotated[Principal, Depends(get_executive_principal)],
    db: Annotated[Session, Depends(get_db)],
    kind: str | None = None,
) -> Page:
    statement = select(Report).where(
        Report.enterprise_id == principal.enterprise_id,
        Report.created_by_user_id == principal.user.id,
    )
    if kind:
        statement = statement.where(Report.kind == kind)
    rows = db.scalars(statement.order_by(Report.period_end.desc()).limit(100)).all()
    visible = []
    for item in rows:
        try:
            assert_org_scope(db, principal, item.organization_unit_id)
        except AppError:
            continue
        visible.append(report_output(db, item, include_content=False))
    return Page(items=visible)


@router.get("/{report_id}", response_model=ReportOut)
def get_report(
    report_id: uuid.UUID,
    principal: Annotated[Principal, Depends(get_executive_principal)],
    db: Annotated[Session, Depends(get_db)],
) -> ReportOut:
    return report_output(db, owned_report(db, principal, report_id))
