from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import record_audit
from ..authz import Principal, assert_org_scope, get_executive_principal
from ..config import Settings, get_settings
from ..database import get_db
from ..errors import AppError
from ..models import Conversation, Memory, MemoryEvent, new_uuid
from ..personal_data import ensure_memory_encrypted, set_memory_content
from ..schemas import MemoryCreate, MemoryOut, MemoryUpdate, Page

router = APIRouter(prefix="/memories", tags=["memories"])


def _memory_out(item: Memory, settings: Settings) -> MemoryOut:
    return MemoryOut.model_validate(item).model_copy(
        update={"content": ensure_memory_encrypted(item, settings)}
    )


def owned_memory(db: Session, principal: Principal, memory_id: uuid.UUID) -> Memory:
    item = db.scalar(
        select(Memory).where(
            Memory.id == memory_id,
            Memory.enterprise_id == principal.enterprise_id,
            # Long-term memory is private even from enterprise admin/FDE.
            Memory.user_id == principal.user.id,
            Memory.status != "deleted",
        )
    )
    if item is None:
        raise AppError(404, "memory_not_found", "记忆不存在")
    assert_org_scope(db, principal, item.organization_unit_id)
    return item


@router.get("", response_model=Page)
def list_memories(
    principal: Annotated[Principal, Depends(get_executive_principal)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    include_disabled: bool = False,
) -> Page:
    statement = select(Memory).where(
        Memory.enterprise_id == principal.enterprise_id,
        Memory.user_id == principal.user.id,
        Memory.status != "deleted",
    )
    if not include_disabled:
        statement = statement.where(Memory.status == "active")
    rows = db.scalars(statement.order_by(Memory.updated_at.desc()).limit(100)).all()
    visible = []
    for item in rows:
        try:
            assert_org_scope(db, principal, item.organization_unit_id)
        except AppError:
            continue
        visible.append(_memory_out(item, settings))
    db.commit()
    return Page(items=visible)


@router.post("", response_model=MemoryOut, status_code=status.HTTP_201_CREATED)
def create_memory(
    payload: MemoryCreate,
    request: Request,
    principal: Annotated[Principal, Depends(get_executive_principal)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> MemoryOut:
    assert_org_scope(db, principal, payload.organization_unit_id)
    if payload.source_conversation_id:
        source = db.scalar(
            select(Conversation).where(
                Conversation.id == payload.source_conversation_id,
                Conversation.enterprise_id == principal.enterprise_id,
                Conversation.owner_user_id == principal.user.id,
            )
        )
        if source is None:
            raise AppError(404, "conversation_not_found", "来源会话不存在")
    item = Memory(
        id=new_uuid(),
        enterprise_id=principal.enterprise_id,
        user_id=principal.user.id,
        organization_unit_id=payload.organization_unit_id,
        source_conversation_id=payload.source_conversation_id,
        kind=payload.kind,
        title=payload.title,
        content="",
    )
    set_memory_content(item, payload.content, settings)
    db.add(item)
    db.flush()
    db.add(
        MemoryEvent(
            memory_id=item.id,
            actor_user_id=principal.user.id,
            event_type="created",
            new_content=None,
        )
    )
    record_audit(
        db,
        request,
        "memory.created",
        actor=principal.user,
        session=principal.session,
        target_type="memory",
        target_id=item.id,
        metadata={"kind": item.kind, "content_length": len(payload.content)},
    )
    db.commit()
    return _memory_out(item, settings)


@router.get("/{memory_id}", response_model=MemoryOut)
def get_memory(
    memory_id: uuid.UUID,
    principal: Annotated[Principal, Depends(get_executive_principal)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> MemoryOut:
    item = owned_memory(db, principal, memory_id)
    output = _memory_out(item, settings)
    db.commit()
    return output


@router.patch("/{memory_id}", response_model=MemoryOut)
def update_memory(
    memory_id: uuid.UUID,
    payload: MemoryUpdate,
    request: Request,
    principal: Annotated[Principal, Depends(get_executive_principal)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> MemoryOut:
    item = owned_memory(db, principal, memory_id)
    changes = payload.model_dump(exclude_unset=True)
    if "content" in changes:
        set_memory_content(item, changes.pop("content"), settings)
    for key, value in changes.items():
        setattr(item, key, value)
    item.version += 1
    db.add(
        MemoryEvent(
            memory_id=item.id,
            actor_user_id=principal.user.id,
            event_type="updated",
            previous_content=None,
            new_content=None,
        )
    )
    record_audit(
        db,
        request,
        "memory.updated",
        actor=principal.user,
        session=principal.session,
        target_type="memory",
        target_id=item.id,
        metadata={"fields": sorted(changes)},
    )
    db.commit()
    db.refresh(item)
    return _memory_out(item, settings)


@router.delete("/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_memory(
    memory_id: uuid.UUID,
    request: Request,
    principal: Annotated[Principal, Depends(get_executive_principal)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    item = owned_memory(db, principal, memory_id)
    item.status = "deleted"
    item.version += 1
    db.add(MemoryEvent(memory_id=item.id, actor_user_id=principal.user.id, event_type="deleted"))
    record_audit(
        db,
        request,
        "memory.deleted",
        actor=principal.user,
        session=principal.session,
        target_type="memory",
        target_id=item.id,
    )
    db.commit()
