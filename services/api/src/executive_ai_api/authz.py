from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .database import get_db
from .errors import AppError
from .models import DataScopeGrant, Enterprise, OrganizationUnit, User, UserSession
from .security import as_utc, secure_equal, token_hash, utc_now


@dataclass
class Principal:
    user: User
    session: UserSession

    @property
    def enterprise_id(self) -> uuid.UUID:
        return self.user.enterprise_id


def _read_session(
    request: Request,
    db: Session,
    settings: Settings,
) -> Principal:
    raw_token = request.cookies.get(settings.session_cookie_name)
    if not raw_token:
        raise AppError(401, "authentication_required", "请先登录")
    hashed = token_hash(raw_token, settings.session_secret.get_secret_value())
    row = db.execute(
        select(UserSession, User)
        .join(User, User.id == UserSession.user_id)
        .where(UserSession.token_hash == hashed)
    ).one_or_none()
    if row is None:
        raise AppError(401, "invalid_session", "登录状态无效，请重新登录")
    user_session, user = row
    now = utc_now()
    if (
        user_session.revoked_at
        or as_utc(user_session.expires_at) <= now
        or as_utc(user_session.idle_expires_at) <= now
    ):
        raise AppError(401, "session_expired", "登录已过期，请重新登录")
    if not user.is_active:
        raise AppError(403, "user_disabled", "账号已停用")
    if user.locked_until and as_utc(user.locked_until) > now:
        raise AppError(423, "user_locked", "账号暂时锁定")
    enterprise = db.get(Enterprise, user.enterprise_id)
    if enterprise is None or not enterprise.is_active:
        raise AppError(403, "enterprise_disabled", "企业账号不可用")
    if as_utc(user_session.last_seen_at) < now - timedelta(minutes=5):
        user_session.last_seen_at = now
        user_session.idle_expires_at = min(
            as_utc(user_session.expires_at),
            now + timedelta(seconds=settings.session_idle_seconds),
        )
        db.commit()
    return Principal(user=user, session=user_session)


def get_authenticated_principal(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Principal:
    cached = getattr(request.state, "principal", None)
    if isinstance(cached, Principal):
        return cached
    principal = _read_session(request, db, settings)
    request.state.principal = principal
    return principal


def get_current_principal(
    principal: Annotated[Principal, Depends(get_authenticated_principal)],
) -> Principal:
    if principal.user.password_change_required:
        raise AppError(
            403,
            "password_change_required",
            "首次登录需要先修改密码",
        )
    return principal


def get_executive_principal(
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> Principal:
    if principal.user.role != "executive":
        raise AppError(403, "role_forbidden", "当前角色无权使用经营业务工作台")
    return principal


def csrf_protect(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    if request.url.path == f"{settings.api_prefix}/auth/login":
        return
    principal = _read_session(request, db, settings)
    header = request.headers.get("X-CSRF-Token", "")
    cookie = request.cookies.get(settings.csrf_cookie_name, "")
    if not header or not cookie or not secure_equal(header, cookie):
        raise AppError(403, "csrf_failed", "安全校验失败，请刷新页面后重试")
    expected = token_hash(header, settings.csrf_secret.get_secret_value())
    if not secure_equal(expected, principal.session.csrf_hash):
        raise AppError(403, "csrf_failed", "安全校验失败，请刷新页面后重试")


def require_roles(*roles: str):
    def dependency(principal: Annotated[Principal, Depends(get_current_principal)]) -> Principal:
        if principal.user.role not in roles:
            raise AppError(403, "role_forbidden", "当前角色无权执行此操作")
        return principal

    return dependency


def accessible_organization_unit_ids(db: Session, principal: Principal) -> set[uuid.UUID]:
    return accessible_organization_unit_ids_for_user(db, principal.user)


def accessible_organization_unit_ids_for_user(db: Session, user: User) -> set[uuid.UUID]:
    analyzable_unit = (
        OrganizationUnit.enterprise_id == user.enterprise_id,
        OrganizationUnit.is_active.is_(True),
        OrganizationUnit.enabled_for_analysis.is_(True),
        OrganizationUnit.data_connected.is_(True),
    )
    grants = db.scalars(
        select(DataScopeGrant).where(
            DataScopeGrant.user_id == user.id,
            DataScopeGrant.can_read.is_(True),
        )
    ).all()
    if any(grant.scope_kind == "enterprise" for grant in grants):
        return set(db.scalars(select(OrganizationUnit.id).where(*analyzable_unit)).all())
    granted_root_ids = {
        grant.organization_unit_id for grant in grants if grant.organization_unit_id is not None
    }
    roots = set(
        db.scalars(
            select(OrganizationUnit.id).where(
                OrganizationUnit.id.in_(granted_root_ids),
                *analyzable_unit,
            )
        ).all()
    )
    if not roots:
        return set()
    # The hierarchy is shallow in phase one; iterate to include configured descendants safely.
    accessible = set(roots)
    while True:
        children = set(
            db.scalars(
                select(OrganizationUnit.id).where(
                    OrganizationUnit.parent_id.in_(accessible),
                    *analyzable_unit,
                )
            ).all()
        )
        expanded = accessible | children
        if expanded == accessible:
            return accessible
        accessible = expanded


def assert_org_scope(
    db: Session,
    principal: Principal,
    organization_unit_id: uuid.UUID | None,
) -> None:
    allowed = accessible_organization_unit_ids(db, principal)
    if not allowed:
        raise AppError(403, "data_scope_forbidden", "当前账号没有有效的事业部查询范围")
    if organization_unit_id is not None and organization_unit_id not in allowed:
        raise AppError(403, "data_scope_forbidden", "该事业部不在您的可查询范围内")


def build_scope_snapshot(
    db: Session,
    principal: Principal,
    organization_unit_id: uuid.UUID | None = None,
) -> dict[str, object]:
    assert_org_scope(db, principal, organization_unit_id)
    grants = db.scalars(
        select(DataScopeGrant).where(
            DataScopeGrant.user_id == principal.user.id,
            DataScopeGrant.can_read.is_(True),
        )
    ).all()
    enterprise_wide = organization_unit_id is None and any(
        item.scope_kind == "enterprise" for item in grants
    )
    unit_ids = (
        [organization_unit_id]
        if organization_unit_id is not None
        else sorted(accessible_organization_unit_ids(db, principal), key=str)
    )
    return {
        "enterprise_wide": enterprise_wide,
        "organization_unit_ids": [str(item) for item in unit_ids],
    }


def scope_snapshot_is_current_for_user(
    db: Session,
    user: User,
    snapshot: dict[str, object] | None,
) -> bool:
    """Fail closed unless every snapshotted unit remains usable and granted."""
    if not snapshot:
        return False
    try:
        required = {uuid.UUID(str(value)) for value in snapshot.get("organization_unit_ids", [])}
    except (TypeError, ValueError):
        return False
    if not required:
        return False
    current = accessible_organization_unit_ids_for_user(db, user)
    if not required.issubset(current):
        return False
    if snapshot.get("enterprise_wide"):
        return (
            db.scalar(
                select(DataScopeGrant.id).where(
                    DataScopeGrant.user_id == user.id,
                    DataScopeGrant.scope_kind == "enterprise",
                    DataScopeGrant.can_read.is_(True),
                )
            )
            is not None
        )
    return True


def organization_scope_predicate(db: Session, principal: Principal, column):
    allowed = accessible_organization_unit_ids(db, principal)
    # Enterprise-neutral records remain visible only when explicitly unscoped.
    return or_(column.is_(None), column.in_(allowed))
