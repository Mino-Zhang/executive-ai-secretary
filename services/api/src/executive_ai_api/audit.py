from __future__ import annotations

import uuid
from typing import Any

from fastapi import Request
from sqlalchemy.orm import Session

from .models import AuditEvent, User, UserSession


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def record_audit(
    db: Session,
    request: Request,
    action: str,
    *,
    actor: User | None = None,
    session: UserSession | None = None,
    target_type: str | None = None,
    target_id: uuid.UUID | str | None = None,
    outcome: str = "success",
    failure_reason_code: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> AuditEvent:
    event = AuditEvent(
        enterprise_id=actor.enterprise_id if actor else None,
        actor_user_id=actor.id if actor else None,
        session_id=session.id if session else None,
        action=action,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        outcome=outcome,
        failure_reason_code=failure_reason_code,
        environment="",
        actor_role=actor.role if actor else None,
        integrity_hash="",
        request_id=getattr(request.state, "request_id", None),
        ip_address=client_ip(request),
        user_agent=request.headers.get("user-agent", "")[:500] or None,
        metadata_json=metadata or {},
        scope_summary_json={},
    )
    db.add(event)
    return event
