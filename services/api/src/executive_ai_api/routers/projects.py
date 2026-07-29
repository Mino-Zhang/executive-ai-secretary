from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import ORJSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import record_audit
from ..authz import Principal, assert_org_scope, get_executive_principal
from ..database import get_db
from ..errors import AppError
from ..idempotency import replay, save_response
from ..models import Project
from ..pagination import decode_cursor, encode_cursor
from ..schemas import Page, ProjectCreate, ProjectOut, ProjectUpdate
from ..security import utc_now

router = APIRouter(prefix="/projects", tags=["projects"])


def owned_project(db: Session, principal: Principal, project_id: uuid.UUID) -> Project:
    # Admin/FDE privacy rule: project content remains owner-only unless explicitly delegated later.
    item = db.scalar(
        select(Project).where(
            Project.id == project_id,
            Project.enterprise_id == principal.enterprise_id,
            Project.owner_user_id == principal.user.id,
        )
    )
    if item is None:
        raise AppError(404, "project_not_found", "项目不存在")
    assert_org_scope(db, principal, item.organization_unit_id)
    return item


@router.get("", response_model=Page)
def list_projects(
    principal: Annotated[Principal, Depends(get_executive_principal)],
    db: Annotated[Session, Depends(get_db)],
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    include_archived: bool = False,
) -> Page:
    cursor_id = decode_cursor(cursor)
    statement = select(Project).where(
        Project.enterprise_id == principal.enterprise_id,
        Project.owner_user_id == principal.user.id,
    )
    if not include_archived:
        statement = statement.where(Project.archived_at.is_(None))
    if cursor_id:
        statement = statement.where(Project.id < cursor_id)
    rows = db.scalars(statement.order_by(Project.id.desc()).limit(limit + 1)).all()
    next_cursor = encode_cursor(rows[limit - 1].id) if len(rows) > limit else None
    rows = rows[:limit]
    visible = []
    for item in rows:
        try:
            assert_org_scope(db, principal, item.organization_unit_id)
        except AppError:
            continue
        visible.append(ProjectOut.model_validate(item))
    return Page(items=visible, next_cursor=next_cursor)


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
def create_project(
    payload: ProjectCreate,
    request: Request,
    principal: Annotated[Principal, Depends(get_executive_principal)],
    db: Annotated[Session, Depends(get_db)],
):
    previous = replay(db, request, principal, payload)
    if previous:
        return ORJSONResponse(status_code=previous[0], content=previous[1])
    assert_org_scope(db, principal, payload.organization_unit_id)
    item = Project(
        enterprise_id=principal.enterprise_id,
        owner_user_id=principal.user.id,
        organization_unit_id=payload.organization_unit_id,
        name=payload.name,
        description=payload.description,
    )
    db.add(item)
    db.flush()
    output = ProjectOut.model_validate(item)
    record_audit(
        db,
        request,
        "project.created",
        actor=principal.user,
        session=principal.session,
        target_type="project",
        target_id=item.id,
    )
    save_response(db, request, principal, payload, 201, output)
    db.commit()
    return output


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(
    project_id: uuid.UUID,
    principal: Annotated[Principal, Depends(get_executive_principal)],
    db: Annotated[Session, Depends(get_db)],
) -> ProjectOut:
    return ProjectOut.model_validate(owned_project(db, principal, project_id))


@router.patch("/{project_id}", response_model=ProjectOut)
def update_project(
    project_id: uuid.UUID,
    payload: ProjectUpdate,
    request: Request,
    principal: Annotated[Principal, Depends(get_executive_principal)],
    db: Annotated[Session, Depends(get_db)],
) -> ProjectOut:
    item = owned_project(db, principal, project_id)
    changes = payload.model_dump(exclude_unset=True)
    if "organization_unit_id" in changes:
        assert_org_scope(db, principal, changes["organization_unit_id"])
    for key, value in changes.items():
        setattr(item, key, value)
    record_audit(
        db,
        request,
        "project.updated",
        actor=principal.user,
        session=principal.session,
        target_type="project",
        target_id=item.id,
        metadata={"fields": sorted(changes)},
    )
    db.commit()
    db.refresh(item)
    return ProjectOut.model_validate(item)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def archive_project(
    project_id: uuid.UUID,
    request: Request,
    principal: Annotated[Principal, Depends(get_executive_principal)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    item = owned_project(db, principal, project_id)
    item.archived_at = utc_now()
    record_audit(
        db,
        request,
        "project.archived",
        actor=principal.user,
        session=principal.session,
        target_type="project",
        target_id=item.id,
    )
    db.commit()


@router.post("/{project_id}/pin", response_model=ProjectOut)
def pin_project(
    project_id: uuid.UUID,
    request: Request,
    principal: Annotated[Principal, Depends(get_executive_principal)],
    db: Annotated[Session, Depends(get_db)],
) -> ProjectOut:
    item = owned_project(db, principal, project_id)
    item.pinned_at = utc_now()
    record_audit(
        db,
        request,
        "project.pinned",
        actor=principal.user,
        session=principal.session,
        target_type="project",
        target_id=item.id,
    )
    db.commit()
    db.refresh(item)
    return ProjectOut.model_validate(item)


@router.delete("/{project_id}/pin", response_model=ProjectOut)
def unpin_project(
    project_id: uuid.UUID,
    request: Request,
    principal: Annotated[Principal, Depends(get_executive_principal)],
    db: Annotated[Session, Depends(get_db)],
) -> ProjectOut:
    item = owned_project(db, principal, project_id)
    item.pinned_at = None
    record_audit(
        db,
        request,
        "project.unpinned",
        actor=principal.user,
        session=principal.session,
        target_type="project",
        target_id=item.id,
    )
    db.commit()
    db.refresh(item)
    return ProjectOut.model_validate(item)
