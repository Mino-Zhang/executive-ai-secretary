from __future__ import annotations

import json
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import record_audit
from ..authz import (
    Principal,
    build_scope_snapshot,
    get_executive_principal,
    scope_snapshot_is_current_for_user,
)
from ..config import get_settings
from ..database import get_db
from ..errors import AppError
from ..job_state import close_assistant_placeholder
from ..models import FileAsset, Job, JobAttempt, Report
from ..schemas import JobCreate, JobOut, Page
from ..security import utc_now

router = APIRouter(prefix="/jobs", tags=["jobs"])
EXTERNAL_JOB_TYPES = {"report.generate", "file.extract"}


def visible_job(
    db: Session,
    principal: Principal,
    job_id: uuid.UUID,
    *,
    lock: bool = False,
) -> Job:
    statement = select(Job).where(
        Job.id == job_id,
        Job.enterprise_id == principal.enterprise_id,
        Job.created_by_user_id == principal.user.id,
    )
    if lock:
        statement = statement.with_for_update()
    item = db.scalar(statement)
    if item is None:
        raise AppError(404, "job_not_found", "任务不存在")
    if not scope_snapshot_is_current_for_user(db, principal.user, item.scope_snapshot_json):
        raise AppError(403, "data_scope_forbidden", "任务的事业部授权已失效")
    return item


@router.get("", response_model=Page)
def list_jobs(
    principal: Annotated[Principal, Depends(get_executive_principal)],
    db: Annotated[Session, Depends(get_db)],
) -> Page:
    rows = db.scalars(
        select(Job)
        .where(
            Job.enterprise_id == principal.enterprise_id,
            Job.created_by_user_id == principal.user.id,
        )
        .order_by(Job.created_at.desc())
        .limit(100)
    ).all()
    visible = [
        item
        for item in rows
        if scope_snapshot_is_current_for_user(db, principal.user, item.scope_snapshot_json)
    ]
    return Page(items=[JobOut.model_validate(item) for item in visible])


@router.post("", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def create_job(
    payload: JobCreate,
    request: Request,
    principal: Annotated[Principal, Depends(get_executive_principal)],
    db: Annotated[Session, Depends(get_db)],
) -> JobOut:
    if payload.job_type not in EXTERNAL_JOB_TYPES:
        raise AppError(403, "job_type_forbidden", "当前角色不能创建此类任务")
    if len(json.dumps(payload.payload, ensure_ascii=False)) > 100_000:
        raise AppError(413, "job_payload_too_large", "任务参数过大")
    try:
        if payload.job_type == "report.generate":
            resource_id = uuid.UUID(str(payload.payload.get("report_id", "")))
            report = db.scalar(
                select(Report).where(
                    Report.id == resource_id,
                    Report.enterprise_id == principal.enterprise_id,
                    Report.created_by_user_id == principal.user.id,
                )
            )
            if report is None:
                raise AppError(404, "report_not_found", "简报不存在")
            snapshot = build_scope_snapshot(db, principal, report.organization_unit_id)
        else:
            resource_id = uuid.UUID(str(payload.payload.get("file_id", "")))
            file_asset = db.scalar(
                select(FileAsset).where(
                    FileAsset.id == resource_id,
                    FileAsset.enterprise_id == principal.enterprise_id,
                    FileAsset.uploaded_by_user_id == principal.user.id,
                    FileAsset.deleted_at.is_(None),
                )
            )
            if file_asset is None:
                raise AppError(404, "file_not_found", "文件不存在")
            snapshot = build_scope_snapshot(db, principal)
    except ValueError as exc:
        raise AppError(422, "invalid_job_resource", "任务必须关联有效资源") from exc
    item = Job(
        enterprise_id=principal.enterprise_id,
        created_by_user_id=principal.user.id,
        job_type=payload.job_type,
        payload_json=payload.payload,
        scope_snapshot_json=snapshot,
        scheduled_at=payload.scheduled_at,
        status="queued",
        max_attempts=get_settings().worker_job_max_attempts,
    )
    db.add(item)
    db.flush()
    record_audit(
        db,
        request,
        "job.created",
        actor=principal.user,
        session=principal.session,
        target_type="job",
        target_id=item.id,
        metadata={"job_type": item.job_type},
    )
    db.commit()
    return JobOut.model_validate(item)


@router.get("/{job_id}", response_model=JobOut)
def get_job(
    job_id: uuid.UUID,
    principal: Annotated[Principal, Depends(get_executive_principal)],
    db: Annotated[Session, Depends(get_db)],
) -> JobOut:
    return JobOut.model_validate(visible_job(db, principal, job_id))


@router.post("/{job_id}/cancel", response_model=JobOut)
def cancel_job(
    job_id: uuid.UUID,
    request: Request,
    principal: Annotated[Principal, Depends(get_executive_principal)],
    db: Annotated[Session, Depends(get_db)],
) -> JobOut:
    item = visible_job(db, principal, job_id, lock=True)
    if item.status not in {"queued", "running"}:
        raise AppError(409, "job_not_cancelable", "当前任务状态不能取消")
    active_lease_token = item.lease_token
    item.status = "canceled"
    item.completed_at = utc_now()
    item.lease_owner = None
    item.lease_token = None
    item.lease_expires_at = None
    item.heartbeat_at = None
    attempt_statement = select(JobAttempt).where(
        JobAttempt.job_id == item.id,
        JobAttempt.status == "running",
    )
    if active_lease_token:
        attempt_statement = attempt_statement.where(
            JobAttempt.lease_token == active_lease_token
        )
    for attempt in db.scalars(attempt_statement).all():
        attempt.status = "canceled"
        attempt.completed_at = item.completed_at
        attempt.error_message = "Canceled by user"
    close_assistant_placeholder(db, item, status="failed", content="请求已取消")
    record_audit(
        db,
        request,
        "job.canceled",
        actor=principal.user,
        session=principal.session,
        target_type="job",
        target_id=item.id,
    )
    db.commit()
    db.refresh(item)
    return JobOut.model_validate(item)
