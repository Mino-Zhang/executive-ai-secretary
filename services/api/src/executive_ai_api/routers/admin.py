from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from ..audit import record_audit
from ..audit_integrity import verify_audit_chain
from ..authz import Principal, require_roles
from ..config import Settings, get_settings
from ..database import get_db
from ..errors import AppError
from ..models import (
    AuditEvent,
    DataScopeGrant,
    Enterprise,
    OrganizationUnit,
    User,
    UserCredential,
    UserSession,
)
from ..schemas import (
    AuditEventOut,
    AuditVerification,
    DataScopeUpdate,
    OrganizationUnitCreate,
    OrganizationUnitOut,
    OrganizationUnitUpdate,
    Page,
    RuntimeStatus,
    TemporaryPasswordRequest,
    UserCreate,
    UserOut,
    UserUpdate,
)
from ..security import hash_password, utc_now, validate_new_password

router = APIRouter(prefix="/admin", tags=["admin"])
AdminPrincipal = Annotated[Principal, Depends(require_roles("enterprise_admin"))]
OperationsPrincipal = Annotated[Principal, Depends(require_roles("enterprise_admin", "fde"))]


def lock_enterprise_admin_updates(db: Session, enterprise_id: uuid.UUID) -> None:
    """Serialize administrator mutations inside one enterprise.

    PostgreSQL's row lock is held until the request transaction commits or rolls
    back.  That makes the subsequent "last active administrator" check and the
    mutation one critical section, while unrelated enterprises remain
    independent. SQLite deliberately skips the lock because it does not support
    ``SELECT .. FOR UPDATE``; this keeps the lightweight unit-test database
    compatible, while the concurrency guarantee is covered against PostgreSQL.
    """

    statement = select(Enterprise.id).where(Enterprise.id == enterprise_id)
    if db.get_bind().dialect.name == "postgresql":
        statement = statement.with_for_update()
    if db.scalar(statement) is None:
        raise AppError(404, "enterprise_not_found", "企业不存在")


def enterprise_user(db: Session, principal: Principal, user_id: uuid.UUID) -> User:
    item = db.scalar(
        select(User).where(
            User.id == user_id,
            User.enterprise_id == principal.enterprise_id,
        )
    )
    if item is None:
        raise AppError(404, "user_not_found", "用户不存在")
    return item


@router.get("/users", response_model=Page)
def list_users(
    principal: AdminPrincipal,
    db: Annotated[Session, Depends(get_db)],
) -> Page:
    rows = db.scalars(
        select(User)
        .where(User.enterprise_id == principal.enterprise_id)
        .order_by(User.created_at.desc())
    ).all()
    return Page(items=[UserOut.model_validate(item) for item in rows])


@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserCreate,
    request: Request,
    principal: AdminPrincipal,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> UserOut:
    validate_new_password(payload.temporary_password, settings)
    # Email is the login identifier, so it must remain unambiguous across tenants.
    if db.scalar(select(User.id).where(User.email == payload.email)):
        raise AppError(409, "email_exists", "该邮箱已存在")
    valid_units = set(
        db.scalars(
            select(OrganizationUnit.id).where(
                OrganizationUnit.enterprise_id == principal.enterprise_id,
                OrganizationUnit.id.in_(payload.organization_unit_ids),
                OrganizationUnit.is_active.is_(True),
            )
        ).all()
    )
    if valid_units != set(payload.organization_unit_ids):
        raise AppError(422, "invalid_organization_unit", "一个或多个事业部无效")
    user = User(
        enterprise_id=principal.enterprise_id,
        email=payload.email,
        display_name=payload.display_name,
        preferred_name=payload.preferred_name,
        role=payload.role,
        password_change_required=True,
    )
    db.add(user)
    db.flush()
    db.add(UserCredential(user_id=user.id, password_hash=hash_password(payload.temporary_password)))
    if payload.enterprise_wide_scope:
        db.add(DataScopeGrant(user_id=user.id, scope_kind="enterprise", can_read=True))
    else:
        for unit_id in valid_units:
            db.add(
                DataScopeGrant(
                    user_id=user.id,
                    scope_kind="organization_unit",
                    organization_unit_id=unit_id,
                    can_read=True,
                )
            )
    record_audit(
        db,
        request,
        "admin.user_created",
        actor=principal.user,
        session=principal.session,
        target_type="user",
        target_id=user.id,
        metadata={
            "role": user.role,
            "scope_count": len(valid_units),
            "enterprise_wide": payload.enterprise_wide_scope,
        },
    )
    db.commit()
    return UserOut.model_validate(user)


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(
    user_id: uuid.UUID,
    payload: UserUpdate,
    request: Request,
    principal: AdminPrincipal,
    db: Annotated[Session, Depends(get_db)],
) -> UserOut:
    # The enterprise lock must be acquired before loading the target or counting
    # administrators. Under PostgreSQL READ COMMITTED, a waiter then observes the
    # preceding transaction's committed role/active-state changes.
    lock_enterprise_admin_updates(db, principal.enterprise_id)
    target = enterprise_user(db, principal, user_id)
    changes = payload.model_dump(exclude_unset=True)
    if target.id == principal.user.id and changes.get("is_active") is False:
        raise AppError(409, "cannot_disable_self", "不能停用当前登录账号")
    if (
        target.id == principal.user.id
        and "role" in changes
        and changes["role"] != "enterprise_admin"
    ):
        raise AppError(409, "cannot_demote_self", "不能降低当前登录账号的管理员角色")
    removes_active_admin = (
        target.role == "enterprise_admin"
        and target.is_active
        and (
            changes.get("is_active") is False
            or ("role" in changes and changes["role"] != "enterprise_admin")
        )
    )
    if removes_active_admin:
        remaining_admins = db.scalar(
            select(func.count())
            .select_from(User)
            .where(
                User.enterprise_id == principal.enterprise_id,
                User.role == "enterprise_admin",
                User.is_active.is_(True),
                User.id != target.id,
            )
        )
        if not remaining_admins:
            raise AppError(409, "last_admin_required", "企业必须保留至少一名有效管理员")
    for key, value in changes.items():
        setattr(target, key, value)
    if changes.get("is_active") is False:
        now = utc_now()
        for item in db.scalars(
            select(UserSession).where(
                UserSession.user_id == target.id, UserSession.revoked_at.is_(None)
            )
        ):
            item.revoked_at = now
    record_audit(
        db,
        request,
        "admin.user_updated",
        actor=principal.user,
        session=principal.session,
        target_type="user",
        target_id=target.id,
        metadata={"fields": sorted(changes)},
    )
    db.commit()
    db.refresh(target)
    return UserOut.model_validate(target)


@router.post("/users/{user_id}/reset-password", response_model=UserOut)
def reset_password(
    user_id: uuid.UUID,
    payload: TemporaryPasswordRequest,
    request: Request,
    principal: AdminPrincipal,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> UserOut:
    validate_new_password(payload.temporary_password, settings)
    target = enterprise_user(db, principal, user_id)
    credential = db.scalar(select(UserCredential).where(UserCredential.user_id == target.id))
    if credential is None:
        credential = UserCredential(user_id=target.id, password_hash="")
        db.add(credential)
    credential.password_hash = hash_password(payload.temporary_password)
    credential.password_changed_at = utc_now()
    credential.failed_attempts = 0
    target.password_change_required = True
    target.locked_until = None
    now = utc_now()
    for item in db.scalars(
        select(UserSession).where(
            UserSession.user_id == target.id, UserSession.revoked_at.is_(None)
        )
    ):
        item.revoked_at = now
    record_audit(
        db,
        request,
        "admin.password_reset",
        actor=principal.user,
        session=principal.session,
        target_type="user",
        target_id=target.id,
    )
    db.commit()
    return UserOut.model_validate(target)


@router.put("/users/{user_id}/data-scopes", response_model=list[OrganizationUnitOut])
def replace_data_scopes(
    user_id: uuid.UUID,
    payload: DataScopeUpdate,
    request: Request,
    principal: AdminPrincipal,
    db: Annotated[Session, Depends(get_db)],
) -> list[OrganizationUnitOut]:
    target = enterprise_user(db, principal, user_id)
    units = db.scalars(
        select(OrganizationUnit).where(
            OrganizationUnit.enterprise_id == principal.enterprise_id,
            OrganizationUnit.id.in_(payload.organization_unit_ids),
            OrganizationUnit.is_active.is_(True),
        )
    ).all()
    if {item.id for item in units} != set(payload.organization_unit_ids):
        raise AppError(422, "invalid_organization_unit", "一个或多个事业部无效")
    db.execute(delete(DataScopeGrant).where(DataScopeGrant.user_id == target.id))
    if payload.enterprise_wide_scope:
        db.add(DataScopeGrant(user_id=target.id, scope_kind="enterprise", can_read=True))
    else:
        for item in units:
            db.add(
                DataScopeGrant(
                    user_id=target.id,
                    scope_kind="organization_unit",
                    organization_unit_id=item.id,
                    can_read=True,
                )
            )
    record_audit(
        db,
        request,
        "admin.data_scopes_replaced",
        actor=principal.user,
        session=principal.session,
        target_type="user",
        target_id=target.id,
        metadata={
            "enterprise_wide": payload.enterprise_wide_scope,
            "unit_ids": [str(item.id) for item in units],
        },
    )
    db.commit()
    return [OrganizationUnitOut.model_validate(item) for item in units]


@router.delete("/users/{user_id}/sessions", status_code=status.HTTP_204_NO_CONTENT)
def revoke_user_sessions(
    user_id: uuid.UUID,
    request: Request,
    principal: AdminPrincipal,
    db: Annotated[Session, Depends(get_db)],
) -> None:
    target = enterprise_user(db, principal, user_id)
    now = utc_now()
    sessions = db.scalars(
        select(UserSession).where(
            UserSession.user_id == target.id, UserSession.revoked_at.is_(None)
        )
    ).all()
    for item in sessions:
        item.revoked_at = now
    record_audit(
        db,
        request,
        "admin.sessions_revoked",
        actor=principal.user,
        session=principal.session,
        target_type="user",
        target_id=target.id,
        metadata={"count": len(sessions)},
    )
    db.commit()


@router.get("/organization-units", response_model=Page)
def admin_list_organization_units(
    principal: AdminPrincipal,
    db: Annotated[Session, Depends(get_db)],
) -> Page:
    rows = db.scalars(
        select(OrganizationUnit)
        .where(OrganizationUnit.enterprise_id == principal.enterprise_id)
        .order_by(OrganizationUnit.sort_order, OrganizationUnit.name)
    ).all()
    return Page(items=[OrganizationUnitOut.model_validate(item) for item in rows])


@router.post("/organization-units", response_model=OrganizationUnitOut, status_code=201)
def create_organization_unit(
    payload: OrganizationUnitCreate,
    request: Request,
    principal: AdminPrincipal,
    db: Annotated[Session, Depends(get_db)],
) -> OrganizationUnitOut:
    if payload.parent_id:
        parent = db.scalar(
            select(OrganizationUnit).where(
                OrganizationUnit.id == payload.parent_id,
                OrganizationUnit.enterprise_id == principal.enterprise_id,
            )
        )
        if parent is None:
            raise AppError(422, "invalid_parent", "上级组织不存在")
    item = OrganizationUnit(enterprise_id=principal.enterprise_id, **payload.model_dump())
    db.add(item)
    db.flush()
    record_audit(
        db,
        request,
        "admin.organization_unit_created",
        actor=principal.user,
        session=principal.session,
        target_type="organization_unit",
        target_id=item.id,
        metadata={"code": item.code},
    )
    db.commit()
    return OrganizationUnitOut.model_validate(item)


@router.patch("/organization-units/{unit_id}", response_model=OrganizationUnitOut)
def update_organization_unit(
    unit_id: uuid.UUID,
    payload: OrganizationUnitUpdate,
    request: Request,
    principal: AdminPrincipal,
    db: Annotated[Session, Depends(get_db)],
) -> OrganizationUnitOut:
    item = db.scalar(
        select(OrganizationUnit).where(
            OrganizationUnit.id == unit_id,
            OrganizationUnit.enterprise_id == principal.enterprise_id,
        )
    )
    if item is None:
        raise AppError(404, "organization_unit_not_found", "事业部不存在")
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("parent_id") == item.id:
        raise AppError(422, "invalid_parent", "事业部不能成为自己的上级")
    if "parent_id" in changes and changes["parent_id"] is not None:
        parent = db.scalar(
            select(OrganizationUnit).where(
                OrganizationUnit.id == changes["parent_id"],
                OrganizationUnit.enterprise_id == principal.enterprise_id,
            )
        )
        if parent is None:
            raise AppError(422, "invalid_parent", "上级组织不存在")
        visited = {item.id}
        cursor: OrganizationUnit | None = parent
        while cursor is not None:
            if cursor.id in visited:
                raise AppError(422, "organization_cycle", "组织层级不能形成循环")
            visited.add(cursor.id)
            if cursor.parent_id is None:
                break
            cursor = db.scalar(
                select(OrganizationUnit).where(
                    OrganizationUnit.id == cursor.parent_id,
                    OrganizationUnit.enterprise_id == principal.enterprise_id,
                )
            )
            if cursor is None:
                raise AppError(422, "invalid_parent", "组织层级包含无效的上级")
    for key, value in changes.items():
        setattr(item, key, value)
    record_audit(
        db,
        request,
        "admin.organization_unit_updated",
        actor=principal.user,
        session=principal.session,
        target_type="organization_unit",
        target_id=item.id,
        metadata={"fields": sorted(changes)},
    )
    db.commit()
    db.refresh(item)
    return OrganizationUnitOut.model_validate(item)


@router.get("/audit-events", response_model=Page)
def list_audit_events(
    principal: OperationsPrincipal,
    db: Annotated[Session, Depends(get_db)],
    limit: int = Query(default=100, ge=1, le=500),
) -> Page:
    statement = select(AuditEvent).where(AuditEvent.enterprise_id == principal.enterprise_id)
    if principal.user.role == "fde":
        statement = statement.where(
            AuditEvent.action.in_(
                [
                    "job.started",
                    "job.completed",
                    "job.failed",
                    "system.config_changed",
                    "auth.login",
                    "auth.logout",
                ]
            )
        )
    rows = db.scalars(statement.order_by(AuditEvent.created_at.desc()).limit(limit)).all()
    return Page(items=[AuditEventOut.model_validate(item) for item in rows])


@router.get("/runtime", response_model=RuntimeStatus)
def runtime_status(
    principal: OperationsPrincipal,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> RuntimeStatus:
    db.execute(text("SELECT 1"))
    return RuntimeStatus(
        app_env=settings.app_env,
        app_mode=settings.app_mode,
        version=settings.app_version,
        database="ready",
        storage="encrypted-local",
        demo_data_enabled=settings.seed_demo_data,
    )


@router.post("/audit-events/verify", response_model=AuditVerification)
def verify_audit_events(
    request: Request,
    principal: AdminPrincipal,
    db: Annotated[Session, Depends(get_db)],
) -> AuditVerification:
    verification = verify_audit_chain(db, principal.enterprise_id)
    # Never mutate a chain that just failed verification. In particular, an
    # absent anchor must remain visible: appending here would create a fresh
    # head and make the next verification incorrectly look healthy. Successful
    # checks are still appended to the verified chain as normal audit events.
    if verification.valid:
        record_audit(
            db,
            request,
            "admin.audit_integrity_verified",
            actor=principal.user,
            session=principal.session,
            target_type="enterprise",
            target_id=principal.enterprise_id,
            outcome="success",
            metadata={
                "checked_count": verification.checked_count,
                "invalid_count": 0,
                "errors": [],
            },
        )
        db.commit()
    return AuditVerification(
        valid=verification.valid,
        checked_count=verification.checked_count,
        invalid_event_ids=verification.invalid_event_ids,
        errors=verification.errors,
    )
