from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import record_audit
from ..authz import Principal, require_roles
from ..config import get_settings
from ..data_source_configuration import (
    DataSourceConfigurationError,
    merge_data_source_configuration,
    validate_data_source_configuration,
)
from ..database import get_db
from ..errors import AppError
from ..feishu_live import COLLECTION_FIELDS, DELIVERY_FIELDS, OPPORTUNITY_FIELDS
from ..ingestion import (
    IngestionError,
    require_isolated_data_source,
    test_source_connection,
)
from ..metric_policy import ensure_default_opportunity_weight_policy
from ..models import (
    DataSource,
    DataSyncRun,
    Job,
    OpportunityExperienceWeightPolicy,
    OrganizationUnit,
    ScheduledTask,
)
from ..schemas import (
    DataOperationsV3OverviewOut,
    DataSourceOperationsStatusOut,
    DataSourceOut,
    DataSourceTestOut,
    DataSourceUpdate,
    DataSyncRunOut,
    FeishuFieldBindingOut,
    FeishuTableBindingStatusOut,
    ManualRunOut,
    OpportunityExperienceWeightPolicyOut,
    OpportunityExperienceWeightPolicyUpdate,
    Page,
    ScheduledTaskOut,
)
from ..security import utc_now

router = APIRouter(prefix="/admin", tags=["admin-data"])
OperationsPrincipal = Annotated[Principal, Depends(require_roles("enterprise_admin", "fde"))]

FEISHU_TABLE_FIELDS = {
    "opportunity": ("商机总览", OPPORTUNITY_FIELDS),
    "delivery": ("项目交付", DELIVERY_FIELDS),
    "collection": ("财务回款", COLLECTION_FIELDS),
}


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
    validation_only: bool = False,
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
            "validation_only": validation_only,
            "operation": "validate" if validation_only else "activate",
            "activation_mode": "all_three_atomic",
        },
        scope_snapshot_json={
            "enterprise_id": str(principal.enterprise_id),
            "organization_unit_ids": [str(value) for value in organization_ids],
        },
    )
    db.add(job)
    db.flush()
    return job


def _masked_identifier(value: object) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if len(raw) <= 8:
        return "•" * len(raw)
    return f"{raw[:4]}…{raw[-4:]}"


def _technical_warnings(run: DataSyncRun | None, domain: str) -> list[str]:
    if run is None:
        return []
    raw = run.cross_table_validation_json or {}
    output: list[str] = []
    for key in ("warnings", "quality_warnings", "issues", "errors"):
        values = raw.get(key)
        if not isinstance(values, list):
            continue
        for value in values:
            if isinstance(value, str):
                output.append(value[:240])
                continue
            if not isinstance(value, dict) or value.get("domain") not in (None, domain):
                continue
            message = value.get("message") or value.get("code")
            if message:
                output.append(str(message)[:240])
    return output[:8]


def _is_successful_activation(run: DataSyncRun) -> bool:
    return run.atomic_activation_status in {"activated", "unchanged"} or (
        run.source_schema_version != "3.0"
        and run.status in {"completed", "succeeded"}
    )


def _is_rejected_activation(run: DataSyncRun) -> bool:
    return run.status in {"failed", "rejected"} or run.atomic_activation_status in {
        "failed",
        "rejected",
    }


def _data_source_operations_status(
    source: DataSource,
    runs: list[DataSyncRun],
) -> DataSourceOperationsStatusOut:
    latest_run = runs[0] if runs else None
    latest_success = next((run for run in runs if _is_successful_activation(run)), None)
    latest_rejected = next((run for run in runs if _is_rejected_activation(run)), None)
    configuration = source.configuration_json if isinstance(source.configuration_json, dict) else {}
    raw_tables = configuration.get("tables")
    tables = raw_tables if isinstance(raw_tables, dict) else {}
    bindings: list[FeishuTableBindingStatusOut] = []
    for domain, (display_name, fields) in FEISHU_TABLE_FIELDS.items():
        raw_binding = tables.get(domain)
        binding = raw_binding if isinstance(raw_binding, dict) else {}
        configured = bool(binding.get("app_token") and binding.get("table_id"))
        schema_hash = (
            str(latest_run.source_schema_hashes_json.get(domain))
            if latest_run and latest_run.source_schema_hashes_json.get(domain)
            else None
        )
        content_hash = (
            str(latest_run.source_content_hashes_json.get(domain))
            if latest_run and latest_run.source_content_hashes_json.get(domain)
            else None
        )
        record_count_value = (
            latest_run.source_record_counts_json.get(domain) if latest_run else None
        )
        if not configured:
            validation_status = "not_configured"
        elif latest_run and _is_rejected_activation(latest_run):
            validation_status = "rejected"
        elif (
            latest_run
            and latest_run.status in {"validated", "completed", "succeeded"}
            and latest_run.source_schema_hashes_json.get(domain)
        ):
            validation_status = "validated"
        else:
            validation_status = "configured"
        bindings.append(
            FeishuTableBindingStatusOut(
                domain=domain,
                display_name=display_name,
                configured=configured,
                app_token_masked=_masked_identifier(binding.get("app_token")),
                table_id=str(binding.get("table_id")) if binding.get("table_id") else None,
                fields=[
                    FeishuFieldBindingOut(
                        field_id=str(field.field_id),
                        field_name=str(field.field_name),
                        field_type=int(field.field_type),
                        required=bool(field.required),
                    )
                    for field in fields
                ],
                schema_hash=schema_hash,
                content_hash=content_hash,
                record_count=int(record_count_value) if record_count_value is not None else None,
                validation_status=validation_status,
                last_validated_at=latest_run.completed_at if latest_run else None,
                warnings=_technical_warnings(latest_run, domain),
            )
        )
    return DataSourceOperationsStatusOut(
        source_id=source.id,
        display_name=source.display_name,
        source_type=source.source_type,
        schema_version=source.schema_version,
        is_enabled=source.is_enabled,
        activation_policy=str(configuration.get("activation_policy") or "all_three_atomic"),
        bindings=bindings,
        latest_successful_run=(
            DataSyncRunOut.model_validate(latest_success) if latest_success else None
        ),
        latest_rejected_run=(
            DataSyncRunOut.model_validate(latest_rejected) if latest_rejected else None
        ),
    )


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


@router.get("/data-operations/overview", response_model=DataOperationsV3OverviewOut)
def get_data_operations_overview(
    principal: OperationsPrincipal,
    db: Annotated[Session, Depends(get_db)],
) -> DataOperationsV3OverviewOut:
    sources = db.scalars(
        select(DataSource)
        .where(DataSource.enterprise_id == principal.enterprise_id)
        .order_by(DataSource.created_at)
    ).all()
    runs = db.scalars(
        select(DataSyncRun)
        .where(DataSyncRun.enterprise_id == principal.enterprise_id)
        .order_by(DataSyncRun.created_at.desc())
        .limit(300)
    ).all()
    runs_by_source: dict[uuid.UUID, list[DataSyncRun]] = {}
    for run in runs:
        runs_by_source.setdefault(run.data_source_id, []).append(run)
    policy = ensure_default_opportunity_weight_policy(db, principal.enterprise_id)
    db.commit()
    db.refresh(policy)
    return DataOperationsV3OverviewOut(
        sources=[
            _data_source_operations_status(source, runs_by_source.get(source.id, []))
            for source in sources
        ],
        experience_weight_policy=OpportunityExperienceWeightPolicyOut.model_validate(policy),
        generated_at=utc_now(),
    )


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
    if "configuration_json" in changes:
        if changes["configuration_json"] is None:
            raise AppError(
                422,
                "data_source_configuration_invalid",
                "数据源配置不能为空",
                {"path": ["configuration_json"]},
            )
        try:
            validated_configuration = validate_data_source_configuration(
                changes["configuration_json"]
            )
            changes["configuration_json"] = merge_data_source_configuration(
                source.configuration_json,
                validated_configuration,
            )
        except DataSourceConfigurationError as exc:
            raise AppError(
                422,
                exc.code,
                str(exc),
                {"path": [*exc.path]},
            ) from exc
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


@router.post("/data-sources/{source_id}/validate", response_model=ManualRunOut, status_code=202)
def validate_data_source_without_activation(
    source_id: uuid.UUID,
    request: Request,
    principal: OperationsPrincipal,
    db: Annotated[Session, Depends(get_db)],
) -> ManualRunOut:
    source = _data_source(db, principal, source_id)
    if not source.is_enabled:
        raise AppError(409, "data_source_disabled", "数据源已停用")
    job = _enqueue_sync(
        db,
        principal,
        source,
        trigger_type="manual_validation",
        validation_only=True,
    )
    record_audit(
        db,
        request,
        "admin.data_source_validation_requested",
        actor=principal.user,
        session=principal.session,
        target_type="job",
        target_id=job.id,
        metadata={
            "data_source_id": str(source.id),
            "activation_mode": "validate_only",
        },
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


@router.get(
    "/metric-policies/opportunity-experience-weight",
    response_model=OpportunityExperienceWeightPolicyOut,
)
def get_opportunity_experience_weight_policy(
    principal: OperationsPrincipal,
    db: Annotated[Session, Depends(get_db)],
) -> OpportunityExperienceWeightPolicyOut:
    policy = ensure_default_opportunity_weight_policy(db, principal.enterprise_id)
    db.commit()
    db.refresh(policy)
    return OpportunityExperienceWeightPolicyOut.model_validate(policy)


@router.patch(
    "/metric-policies/opportunity-experience-weight",
    response_model=OpportunityExperienceWeightPolicyOut,
)
def update_opportunity_experience_weight_policy(
    payload: OpportunityExperienceWeightPolicyUpdate,
    request: Request,
    principal: OperationsPrincipal,
    db: Annotated[Session, Depends(get_db)],
) -> OpportunityExperienceWeightPolicyOut:
    current = ensure_default_opportunity_weight_policy(db, principal.enterprise_id)
    if current.version != payload.base_version:
        raise AppError(
            409,
            "experience_weight_policy_version_conflict",
            "指标口径已被其他管理员更新，请刷新后重试",
            {"current_version": current.version},
        )
    previous_weights = dict(current.weights_json)
    current.is_active = False
    db.flush()
    row = OpportunityExperienceWeightPolicy(
        enterprise_id=principal.enterprise_id,
        version=current.version + 1,
        label=payload.label.strip() if payload.label else current.label,
        weights_json=payload.weights.model_dump(),
        observation_windows_json=list(current.observation_windows_json),
        observation_window_days=current.observation_window_days,
        is_active=True,
        activated_at=utc_now(),
        created_by_user_id=principal.user.id,
        notes=payload.notes.strip() if payload.notes is not None else current.notes,
    )
    db.add(row)
    db.flush()
    record_audit(
        db,
        request,
        "admin.opportunity_experience_weight_policy_updated",
        actor=principal.user,
        session=principal.session,
        target_type="opportunity_experience_weight_policy",
        target_id=row.id,
        metadata={
            "previous_version": current.version,
            "version": row.version,
            "previous_weights": previous_weights,
            "weights": row.weights_json,
            "observation_windows": row.observation_windows_json,
        },
    )
    db.commit()
    db.refresh(row)
    return OpportunityExperienceWeightPolicyOut.model_validate(row)


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
