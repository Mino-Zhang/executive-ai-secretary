from __future__ import annotations

import uuid

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .authz import Principal, accessible_organization_unit_ids
from .errors import AppError
from .models import Conversation, ConversationOrganizationScope
from .schemas import OrganizationScopeInput, OrganizationScopeOut


def legacy_scope(organization_unit_id: uuid.UUID | None) -> OrganizationScopeInput:
    if organization_unit_id is None:
        return OrganizationScopeInput(mode="all_authorized", organization_unit_ids=[])
    return OrganizationScopeInput(
        mode="selected", organization_unit_ids=[organization_unit_id]
    )


def normalize_scope(
    db: Session,
    principal: Principal,
    scope: OrganizationScopeInput,
) -> tuple[OrganizationScopeInput, list[uuid.UUID]]:
    allowed = accessible_organization_unit_ids(db, principal)
    if scope.mode == "all_authorized":
        return scope, sorted(allowed, key=str)
    requested = set(scope.organization_unit_ids)
    forbidden = requested - allowed
    if forbidden:
        raise AppError(403, "data_scope_forbidden", "一个或多个事业部不在您的可查询范围内")
    if not requested:
        raise AppError(422, "empty_organization_scope", "至少选择一个事业部")
    if allowed and requested == allowed:
        collapsed = OrganizationScopeInput(mode="all_authorized", organization_unit_ids=[])
        return collapsed, sorted(allowed, key=str)
    return (
        OrganizationScopeInput(
            mode="selected",
            organization_unit_ids=sorted(requested, key=str),
        ),
        sorted(requested, key=str),
    )


def persisted_scope(db: Session, conversation: Conversation) -> OrganizationScopeInput:
    if conversation.scope_mode == "all_authorized":
        return OrganizationScopeInput(mode="all_authorized", organization_unit_ids=[])
    ids = list(
        db.scalars(
            select(ConversationOrganizationScope.organization_unit_id)
            .where(ConversationOrganizationScope.conversation_id == conversation.id)
            .order_by(ConversationOrganizationScope.organization_unit_id)
        ).all()
    )
    if not ids and conversation.organization_unit_id is not None:
        ids = [conversation.organization_unit_id]
    if not ids:
        # A damaged selected scope must not silently widen authority.
        raise AppError(409, "invalid_conversation_scope", "会话的事业部范围已失效，请重新选择")
    return OrganizationScopeInput(mode="selected", organization_unit_ids=ids)


def set_conversation_scope(
    db: Session,
    conversation: Conversation,
    scope: OrganizationScopeInput,
) -> None:
    db.execute(
        delete(ConversationOrganizationScope).where(
            ConversationOrganizationScope.conversation_id == conversation.id
        )
    )
    conversation.scope_mode = scope.mode
    conversation.organization_unit_id = (
        scope.organization_unit_ids[0]
        if scope.mode == "selected" and len(scope.organization_unit_ids) == 1
        else None
    )
    if scope.mode == "selected":
        for unit_id in scope.organization_unit_ids:
            db.add(
                ConversationOrganizationScope(
                    conversation_id=conversation.id,
                    organization_unit_id=unit_id,
                )
            )
    # Callers can serialize the scope before committing. Flush here so a
    # subsequent persisted_scope() never observes an empty selected set.
    db.flush()


def scope_out(
    db: Session,
    principal: Principal,
    conversation: Conversation,
) -> OrganizationScopeOut:
    scope = persisted_scope(db, conversation)
    normalized, resolved = normalize_scope(db, principal, scope)
    return OrganizationScopeOut(
        mode=normalized.mode,
        organization_unit_ids=normalized.organization_unit_ids,
        resolved_organization_unit_ids=resolved,
    )


def scope_snapshot(
    normalized: OrganizationScopeInput,
    resolved_ids: list[uuid.UUID],
) -> dict[str, object]:
    if not resolved_ids:
        return {
            "enterprise_wide": False,
            "organization_unit_ids": [],
            "general_only": True,
        }
    return {
        "scope_mode": normalized.mode,
        # all_authorized resolves the caller's current grants; it is not an
        # enterprise-wide privilege and must never weaken retry validation.
        "enterprise_wide": False,
        "organization_unit_ids": [str(item) for item in resolved_ids],
    }


def scope_changed(left: OrganizationScopeInput, right: OrganizationScopeInput) -> bool:
    return left.mode != right.mode or set(left.organization_unit_ids) != set(
        right.organization_unit_ids
    )
