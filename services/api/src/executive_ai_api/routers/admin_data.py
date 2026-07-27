from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import record_audit
from ..authz import Principal, require_roles
from ..config import get_settings
from ..database import get_db
from ..errors import AppError
from ..ingestion import (
    IngestionError,
    require_isolated_data_source,
    test_source_connection,
)
from ..models import (
    DataSource,
    DataSyncRun,
    Job,
    OrganizationUnit,
    ScheduledTask,
)
from ..schemas import (
    DataSourceOut,
    DataSourceTestOut,
    DataSourceUpdate,
    DataSyncRunOut,
    ManualRunOut,
    Page,
    ScheduledTaskOut,
)
from ..security import utc_now

router = APIRouter(prefix="/admin", tags=["admin-data"])
OperationsPrincipal = Annotated[Principal, Depends(require_roles("enterprise_admin", "fde"))]


def _data_source(db: Session, principal: Principal, source_id: uuid.UUID) -> DataSource:
    source = db.scalar(
        select(DataSource).where(
            DataSource.id == source_id,
            DataSource.enterprise_id == principal.enterprise_id,
        )
    )
    if source is None:
        raise AppError(404, "data_source_not_found", "数据源不存在")
    return source


def _enqueue_sync(
    db: Session,
    principal: Principal,
    source: DataSource,
    *,
    trigger_type: str,
    scheduled_task_id: uuid.UUID | None = None,
) -> Job:
    if not source.is_enabled:
        raise AppError(409, "data_source_disabled", "数据源已停用")
    try:
        require_isolated_data_source(db, source)
    except IngestionError as exc:
        raise AppError(409, exc.code, str(exc)) from exc
    organization_ids = db.scalars(
        select(OrganizationUnit.id).where(
            OrganizationUnit.enterprise_id == principal.enterprise_id,
            OrganizationUnit.is_active.is_(True),
            OrganizationUnit.enabled_for_analysis.is_(True),
            OrganizationUnit.data_connected.is_(True),
        )
    ).all()
    job = Job(
        enterprise_id=principal.enterprise_id,
        created_by_user_id=principal.user.id,
        job_type="data.sync",
        status="queued",
        max_attempts=get_settings().worker_job_max_attempts,
        payload_json={
            "data_source_id": str(source.id),
            "scheduled_task_id": str(scheduled_task_id) if scheduled_task_id else None,
            "trigger_type": trigger_type,
        },
        scope_snapshot_json={
            "enterprise_id": str(principal.enterprise_id),
            "organization_unit_ids": [str(value) for value in organization_ids],
        },
    )
    db.add(job)
    db.flush()
    return job


@router.get("/data-sources", response_model=Page)
def list_data_sources(
    principal: OperationsPrincipal,
    db: Annotated[Session, Depends(get_db)],
) -> Page:
    rows = db.scalars(
        select(DataSource)
        .where(DataSource.enterprise_id == principal.enterprise_id)
        .order_by(DataSource.created_at)
    ).all()
    return Page(items=[DataSourceOut.model_validate(row) for row in rows])


@router.patch("/data-sources/{source_id}", response_model=DataSourceOut)
def update_data_source(
    source_id: uuid.UUID,
    payload: DataSourceUpdate,
    request: Request,
    principal: OperationsPrincipal,
    db: Annotated[Session, Depends(get_db)],
) -> DataSourceOut:
    source = _data_source(db, principal, source_id)
    changes = payload.model_dump(exclude_unset=True)
    for key, value in changes.items():
        setattr(source, key, value)
    db.flush()
    if source.is_enabled:
        try:
            require_isolated_data_source(db, source)
        except IngestionError as exc:
            raise AppError(409, exc.code, str(exc)) from exc
    record_audit(
        db,
        request,
        "admin.data_source_updated",
        actor=principal.user,
        session=principal.session,
        target_type="data_source",
        target_id=source.id,
        metadata={"fields": sorted(changes)},
    )
    db.commit()
    db.refresh(source)
    return DataSourceOut.model_validate(source)


@router.post("/data-sources/{source_id}/test", response_model=DataSourceTestOut)
def test_data_source(
    source_id: uuid.UUID,
    request: Request,
    principal: OperationsPrincipal,
    db: Annotated[Session, Depends(get_db)],
) -> DataSourceTestOut:
    source = _data_source(db, principal, source_id)
    try:
        result = test_source_connection(source, db=db)
    except Exception as exc:
        source.last_tested_at = utc_now()
        source.last_test_status = "failed"
        source.last_test_error = str(exc)[:2000]
        record_audit(
            db,
            request,
            "admin.data_source_tested",
            actor=principal.user,
            session=principal.session,
            target_type="data_source",
            target_id=source.id,
            outcome="failure",
            failure_reason_code=getattr(exc, "code", "source_test_failed"),
        )
        db.commit()
        code = exc.code if isinstance(exc, IngestionError) else "source_test_failed"
        raise AppError(422, code, f"数据源校验失败：{exc}") from exc
    source.last_tested_at = utc_now()
    source.last_test_status = "success"
    source.last_test_error = None
    record_audit(
        db,
        request,
        "admin.data_source_tested",
        actor=principal.user,
        session=principal.session,
        target_type="data_source",
        target_id=source.id,
    )
    db.commit()
    return DataSourceTestOut(**result)


@router.post("/data-sources/{source_id}/sync", response_model=ManualRunOut, status_code=202)
def sync_data_source(
    source_id: uuid.UUID,
    request: Request,
    principal: OperationsPrincipal,
    db: Annotated[Session, Depends(get_db)],
) -> ManualRunOut:
    source = _data_source(db, principal, source_id)
    if not source.is_enabled:
        raise AppError(409, "data_source_disabled", "数据源已停用")
    job = _enqueue_sync(db, principal, source, trigger_type="manual")
    record_audit(
        db,
        request,
        "admin.data_sync_requested",
        actor=principal.user,
        session=principal.session,
        target_type="job",
        target_id=job.id,
        metadata={"data_source_id": str(source.id)},
    )
    db.commit()
    return ManualRunOut(job_id=job.id)


@router.get("/data-sync-runs", response_model=Page)
def list_data_sync_runs(
    principal: OperationsPrincipal,
    db: Annotated[Session, Depends(get_db)],
) -> Page:
    rows = db.scalars(
        select(DataSyncRun)
        .where(DataSyncRun.enterprise_id == principal.enterprise_id)
        .order_by(DataSyncRun.created_at.desc())
        .limit(100)
    ).all()
    return Page(items=[DataSyncRunOut.model_validate(row) for row in rows])


@router.get("/scheduled-tasks", response_model=Page)
def list_scheduled_tasks(
    principal: OperationsPrincipal,
    db: Annotated[Session, Depends(get_db)],
) -> Page:
    rows = db.scalars(
        select(ScheduledTask)
        .where(ScheduledTask.enterprise_id == principal.enterprise_id)
        .order_by(ScheduledTask.key)
    ).all()
    return Page(items=[ScheduledTaskOut.model_validate(row) for row in rows])


@router.post("/scheduled-tasks/{task_id}/run", response_model=ManualRunOut, status_code=202)
def run_scheduled_task(
    task_id: uuid.UUID,
    request: Request,
    principal: OperationsPrincipal,
    db: Annotated[Session, Depends(get_db)],
) -> ManualRunOut:
    task = db.scalar(
        select(ScheduledTask).where(
            ScheduledTask.id == task_id,
            ScheduledTask.enterprise_id == principal.enterprise_id,
        )
    )
    if task is None or task.data_source_id is None:
        raise AppError(404, "scheduled_task_not_found", "自动任务不存在")
    source = _data_source(db, principal, task.data_source_id)
    job = _enqueue_sync(
        db,
        principal,
        source,
        trigger_type="manual_schedule",
        scheduled_task_id=task.id,
    )
    record_audit(
        db,
        request,
        "admin.scheduled_task_run_requested",
        actor=principal.user,
        session=principal.session,
        target_type="scheduled_task",
        target_id=task.id,
        metadata={"job_id": str(job.id)},
    )
    db.commit()
    return ManualRunOut(job_id=job.id)
