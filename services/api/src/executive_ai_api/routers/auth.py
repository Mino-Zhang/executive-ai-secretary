from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..audit import client_ip, record_audit
from ..authz import (
    Principal,
    accessible_organization_unit_ids,
    get_authenticated_principal,
    get_current_principal,
)
from ..config import Settings, get_settings
from ..database import get_db
from ..errors import AppError
from ..models import Enterprise, OrganizationUnit, User, UserCredential, UserSession
from ..schemas import (
    ChangePasswordRequest,
    LoginRequest,
    LoginResponse,
    MeResponse,
    OrganizationUnitOut,
    SessionOut,
    UserOut,
)
from ..security import (
    as_utc,
    hash_password,
    new_session_tokens,
    password_needs_rehash,
    rate_limiter,
    session_expirations,
    token_hash,
    utc_now,
    validate_new_password,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def set_auth_cookies(
    response: Response,
    settings: Settings,
    session_token: str,
    csrf_token: str,
) -> None:
    max_age = settings.session_ttl_seconds
    response.set_cookie(
        settings.session_cookie_name,
        session_token,
        max_age=max_age,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,
        path="/",
    )
    response.set_cookie(
        settings.csrf_cookie_name,
        csrf_token,
        max_age=max_age,
        httponly=False,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,
        path="/",
    )


def clear_auth_cookies(response: Response, settings: Settings) -> None:
    for name in (settings.session_cookie_name, settings.csrf_cookie_name):
        response.delete_cookie(
            name,
            httponly=name == settings.session_cookie_name,
            secure=settings.session_cookie_secure,
            samesite=settings.session_cookie_samesite,
            path="/",
        )


@router.post("/login", response_model=LoginResponse)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> LoginResponse:
    ip = client_ip(request) or "unknown"
    allowed, retry_after = rate_limiter.check(
        f"login:{ip}:{payload.email}",
        settings.login_max_attempts,
        settings.login_window_seconds,
    )
    if not allowed:
        raise AppError(
            429, "rate_limited", "登录尝试过于频繁，请稍后重试", {"retry_after": retry_after}
        )

    matches = db.scalars(
        select(User)
        .options(selectinload(User.credential))
        .where(User.email == payload.email)
        .limit(2)
    ).all()
    user = matches[0] if len(matches) == 1 else None
    valid = bool(
        user
        and user.credential
        and verify_password(user.credential.password_hash, payload.password)
    )
    now = utc_now()
    if not valid or not user:
        if user and user.credential:
            user.credential.failed_attempts += 1
            if user.credential.failed_attempts >= settings.login_max_attempts:
                user.locked_until = now + timedelta(seconds=settings.login_window_seconds)
            record_audit(
                db,
                request,
                "auth.login_failed",
                actor=user,
                target_type="user",
                target_id=user.id,
                outcome="failure",
            )
            db.commit()
        raise AppError(401, "invalid_credentials", "邮箱或密码不正确")
    if not user.is_active:
        raise AppError(403, "user_disabled", "账号已停用")
    if user.locked_until and as_utc(user.locked_until) > now:
        raise AppError(423, "user_locked", "账号暂时锁定，请稍后重试")

    credential = user.credential
    assert credential is not None
    if password_needs_rehash(credential.password_hash):
        credential.password_hash = hash_password(payload.password)
    credential.failed_attempts = 0
    user.locked_until = None
    user.last_login_at = now
    tokens = new_session_tokens()
    expires_at, idle_expires_at = session_expirations(settings)
    session = UserSession(
        user_id=user.id,
        token_hash=token_hash(tokens.session_token, settings.session_secret.get_secret_value()),
        csrf_hash=token_hash(tokens.csrf_token, settings.csrf_secret.get_secret_value()),
        expires_at=expires_at,
        idle_expires_at=idle_expires_at,
        last_seen_at=now,
        ip_address=ip,
        user_agent=request.headers.get("user-agent", "")[:500] or None,
    )
    db.add(session)
    db.flush()
    record_audit(
        db,
        request,
        "auth.login",
        actor=user,
        session=session,
        target_type="session",
        target_id=session.id,
    )
    db.commit()
    set_auth_cookies(response, settings, tokens.session_token, tokens.csrf_token)
    return LoginResponse(
        user=UserOut.model_validate(user),
        csrf_token=tokens.csrf_token,
        expires_at=expires_at,
        app_env=settings.app_env,
        app_mode=settings.app_mode,
    )


@router.get("/me", response_model=MeResponse)
def me(
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(get_authenticated_principal)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> MeResponse:
    enterprise = db.get(Enterprise, principal.enterprise_id)
    if enterprise is None or not enterprise.is_active:
        raise AppError(403, "enterprise_disabled", "企业账号不可用")
    ids = accessible_organization_unit_ids(db, principal)
    scopes = db.scalars(
        select(OrganizationUnit)
        .where(OrganizationUnit.id.in_(ids), OrganizationUnit.is_active.is_(True))
        .order_by(OrganizationUnit.sort_order, OrganizationUnit.name)
    ).all()
    csrf_token = request.cookies.get(settings.csrf_cookie_name)
    if (
        not csrf_token
        or token_hash(csrf_token, settings.csrf_secret.get_secret_value())
        != principal.session.csrf_hash
    ):
        csrf_token = new_session_tokens().csrf_token
        principal.session.csrf_hash = token_hash(
            csrf_token, settings.csrf_secret.get_secret_value()
        )
        db.commit()
        response.set_cookie(
            settings.csrf_cookie_name,
            csrf_token,
            max_age=settings.session_ttl_seconds,
            httponly=False,
            secure=settings.session_cookie_secure,
            samesite=settings.session_cookie_samesite,
            path="/",
        )
    return MeResponse(
        user=UserOut.model_validate(principal.user),
        enterprise=enterprise,
        scopes=[OrganizationUnitOut.model_validate(item) for item in scopes],
        csrf_token=csrf_token,
        app_env=settings.app_env,
        app_mode=settings.app_mode,
    )


@router.post("/change-password", response_model=LoginResponse)
def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(get_authenticated_principal)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> LoginResponse:
    credential = db.scalar(
        select(UserCredential).where(UserCredential.user_id == principal.user.id)
    )
    if credential is None or not verify_password(
        credential.password_hash, payload.current_password
    ):
        record_audit(
            db,
            request,
            "auth.password_change_failed",
            actor=principal.user,
            session=principal.session,
            target_type="user",
            target_id=principal.user.id,
            outcome="failure",
        )
        db.commit()
        raise AppError(400, "current_password_incorrect", "当前密码不正确")
    if verify_password(credential.password_hash, payload.new_password):
        raise AppError(422, "password_reused", "新密码不能与当前密码相同")
    validate_new_password(payload.new_password, settings)
    now = utc_now()
    credential.password_hash = hash_password(payload.new_password)
    credential.password_changed_at = now
    credential.failed_attempts = 0
    principal.user.password_change_required = False
    # End every other browser session after a credential change.
    other_sessions = db.scalars(
        select(UserSession).where(
            UserSession.user_id == principal.user.id,
            UserSession.id != principal.session.id,
            UserSession.revoked_at.is_(None),
        )
    ).all()
    for item in other_sessions:
        item.revoked_at = now
    csrf_token = new_session_tokens().csrf_token
    principal.session.csrf_hash = token_hash(csrf_token, settings.csrf_secret.get_secret_value())
    record_audit(
        db,
        request,
        "auth.password_changed",
        actor=principal.user,
        session=principal.session,
        target_type="user",
        target_id=principal.user.id,
        metadata={"other_sessions_revoked": len(other_sessions)},
    )
    db.commit()
    response.set_cookie(
        settings.csrf_cookie_name,
        csrf_token,
        max_age=settings.session_ttl_seconds,
        httponly=False,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,
        path="/",
    )
    return LoginResponse(
        user=UserOut.model_validate(principal.user),
        csrf_token=csrf_token,
        expires_at=principal.session.expires_at,
        app_env=settings.app_env,
        app_mode=settings.app_mode,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(get_authenticated_principal)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    principal.session.revoked_at = utc_now()
    record_audit(
        db,
        request,
        "auth.logout",
        actor=principal.user,
        session=principal.session,
        target_type="session",
        target_id=principal.session.id,
    )
    db.commit()
    clear_auth_cookies(response, settings)


@router.get("/sessions", response_model=list[SessionOut])
def list_sessions(
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_db)],
) -> list[SessionOut]:
    sessions = db.scalars(
        select(UserSession)
        .where(UserSession.user_id == principal.user.id, UserSession.revoked_at.is_(None))
        .order_by(UserSession.last_seen_at.desc())
    ).all()
    return [
        SessionOut.model_validate(item).model_copy(
            update={"is_current": item.id == principal.session.id}
        )
        for item in sessions
    ]


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_session(
    session_id: uuid.UUID,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    target = db.scalar(
        select(UserSession).where(
            UserSession.id == session_id,
            UserSession.user_id == principal.user.id,
            UserSession.revoked_at.is_(None),
        )
    )
    if target is None:
        raise AppError(404, "session_not_found", "登录会话不存在")
    target.revoked_at = utc_now()
    record_audit(
        db,
        request,
        "auth.session_revoked",
        actor=principal.user,
        session=principal.session,
        target_type="session",
        target_id=target.id,
    )
    db.commit()
    if target.id == principal.session.id:
        clear_auth_cookies(response, settings)
