from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import ORJSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..audit import record_audit
from ..authz import (
    Principal,
    assert_org_scope,
    build_scope_snapshot,
    get_executive_principal,
)
from ..config import get_settings
from ..database import get_db
from ..errors import AppError
from ..idempotency import replay, save_response
from ..models import (
    Conversation,
    ConversationFile,
    FileAsset,
    Job,
    Message,
    Project,
    ProjectConversation,
)
from ..pagination import decode_cursor, encode_cursor
from ..schemas import (
    ConversationCreate,
    ConversationOut,
    ConversationUpdate,
    MessageCreate,
    MessageOut,
    Page,
)
from ..security import utc_now

router = APIRouter(prefix="/conversations", tags=["conversations"])


def owned_conversation(
    db: Session,
    principal: Principal,
    conversation_id: uuid.UUID,
    *,
    lock: bool = False,
) -> Conversation:
    statement = select(Conversation).where(
        Conversation.id == conversation_id,
        Conversation.enterprise_id == principal.enterprise_id,
        # Content is always owner-private in phase one, including from admin/FDE.
        Conversation.owner_user_id == principal.user.id,
    )
    if lock:
        statement = statement.with_for_update()
    item = db.scalar(statement)
    if item is None:
        raise AppError(404, "conversation_not_found", "会话不存在")
    assert_org_scope(db, principal, item.organization_unit_id)
    return item


@router.get("", response_model=Page)
def list_conversations(
    principal: Annotated[Principal, Depends(get_executive_principal)],
    db: Annotated[Session, Depends(get_db)],
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    project_id: uuid.UUID | None = None,
    include_archived: bool = False,
) -> Page:
    cursor_id = decode_cursor(cursor)
    statement = select(Conversation).where(
        Conversation.enterprise_id == principal.enterprise_id,
        Conversation.owner_user_id == principal.user.id,
    )
    if project_id:
        statement = statement.join(
            ProjectConversation,
            ProjectConversation.conversation_id == Conversation.id,
        ).where(ProjectConversation.project_id == project_id)
    if not include_archived:
        statement = statement.where(Conversation.archived_at.is_(None))
    if cursor_id:
        statement = statement.where(Conversation.id < cursor_id)
    rows = db.scalars(statement.order_by(Conversation.id.desc()).limit(limit + 1)).all()
    next_cursor = encode_cursor(rows[limit - 1].id) if len(rows) > limit else None
    visible = []
    for item in rows[:limit]:
        try:
            assert_org_scope(db, principal, item.organization_unit_id)
        except AppError:
            continue
        visible.append(ConversationOut.model_validate(item))
    return Page(items=visible, next_cursor=next_cursor)


@router.post("", response_model=ConversationOut, status_code=status.HTTP_201_CREATED)
def create_conversation(
    payload: ConversationCreate,
    request: Request,
    principal: Annotated[Principal, Depends(get_executive_principal)],
    db: Annotated[Session, Depends(get_db)],
):
    previous = replay(db, request, principal, payload)
    if previous:
        return ORJSONResponse(status_code=previous[0], content=previous[1])
    assert_org_scope(db, principal, payload.organization_unit_id)
    project = None
    if payload.project_id:
        project = db.scalar(
            select(Project).where(
                Project.id == payload.project_id,
                Project.enterprise_id == principal.enterprise_id,
                Project.owner_user_id == principal.user.id,
                Project.archived_at.is_(None),
            )
        )
        if project is None:
            raise AppError(404, "project_not_found", "项目不存在")
        assert_org_scope(db, principal, project.organization_unit_id)
    item = Conversation(
        enterprise_id=principal.enterprise_id,
        owner_user_id=principal.user.id,
        organization_unit_id=payload.organization_unit_id,
        title=payload.title,
    )
    db.add(item)
    db.flush()
    if project:
        db.add(ProjectConversation(project_id=project.id, conversation_id=item.id))
    output = ConversationOut.model_validate(item)
    record_audit(
        db,
        request,
        "conversation.created",
        actor=principal.user,
        session=principal.session,
        target_type="conversation",
        target_id=item.id,
        metadata={"project_id": str(project.id) if project else None},
    )
    save_response(db, request, principal, payload, 201, output)
    db.commit()
    return output


@router.get("/{conversation_id}", response_model=ConversationOut)
def get_conversation(
    conversation_id: uuid.UUID,
    principal: Annotated[Principal, Depends(get_executive_principal)],
    db: Annotated[Session, Depends(get_db)],
) -> ConversationOut:
    return ConversationOut.model_validate(owned_conversation(db, principal, conversation_id))


@router.patch("/{conversation_id}", response_model=ConversationOut)
def update_conversation(
    conversation_id: uuid.UUID,
    payload: ConversationUpdate,
    request: Request,
    principal: Annotated[Principal, Depends(get_executive_principal)],
    db: Annotated[Session, Depends(get_db)],
) -> ConversationOut:
    item = owned_conversation(db, principal, conversation_id)
    changes = payload.model_dump(exclude_unset=True)
    if "organization_unit_id" in changes:
        assert_org_scope(db, principal, changes["organization_unit_id"])
    for key, value in changes.items():
        setattr(item, key, value)
    if changes.get("status") == "archived":
        item.archived_at = utc_now()
    elif changes.get("status") == "active":
        item.archived_at = None
    record_audit(
        db,
        request,
        "conversation.updated",
        actor=principal.user,
        session=principal.session,
        target_type="conversation",
        target_id=item.id,
        metadata={"fields": sorted(changes)},
    )
    db.commit()
    db.refresh(item)
    return ConversationOut.model_validate(item)


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def archive_conversation(
    conversation_id: uuid.UUID,
    request: Request,
    principal: Annotated[Principal, Depends(get_executive_principal)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    item = owned_conversation(db, principal, conversation_id)
    item.archived_at = utc_now()
    item.status = "archived"
    record_audit(
        db,
        request,
        "conversation.archived",
        actor=principal.user,
        session=principal.session,
        target_type="conversation",
        target_id=item.id,
    )
    db.commit()


@router.post("/{conversation_id}/pin", response_model=ConversationOut)
def pin_conversation(
    conversation_id: uuid.UUID,
    request: Request,
    principal: Annotated[Principal, Depends(get_executive_principal)],
    db: Annotated[Session, Depends(get_db)],
) -> ConversationOut:
    item = owned_conversation(db, principal, conversation_id)
    item.pinned_at = utc_now()
    record_audit(
        db,
        request,
        "conversation.pinned",
        actor=principal.user,
        session=principal.session,
        target_type="conversation",
        target_id=item.id,
    )
    db.commit()
    db.refresh(item)
    return ConversationOut.model_validate(item)


@router.delete("/{conversation_id}/pin", response_model=ConversationOut)
def unpin_conversation(
    conversation_id: uuid.UUID,
    request: Request,
    principal: Annotated[Principal, Depends(get_executive_principal)],
    db: Annotated[Session, Depends(get_db)],
) -> ConversationOut:
    item = owned_conversation(db, principal, conversation_id)
    item.pinned_at = None
    record_audit(
        db,
        request,
        "conversation.unpinned",
        actor=principal.user,
        session=principal.session,
        target_type="conversation",
        target_id=item.id,
    )
    db.commit()
    db.refresh(item)
    return ConversationOut.model_validate(item)


@router.get("/{conversation_id}/messages", response_model=Page)
def list_messages(
    conversation_id: uuid.UUID,
    principal: Annotated[Principal, Depends(get_executive_principal)],
    db: Annotated[Session, Depends(get_db)],
    after_sequence: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
) -> Page:
    owned_conversation(db, principal, conversation_id)
    rows = db.scalars(
        select(Message)
        .where(
            Message.conversation_id == conversation_id,
            Message.sequence > after_sequence,
        )
        .order_by(Message.sequence)
        .limit(limit + 1)
    ).all()
    next_cursor = str(rows[limit - 1].sequence) if len(rows) > limit else None
    return Page(
        items=[MessageOut.model_validate(item) for item in rows[:limit]],
        next_cursor=next_cursor,
    )


@router.post(
    "/{conversation_id}/messages",
    response_model=MessageOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_message(
    conversation_id: uuid.UUID,
    payload: MessageCreate,
    request: Request,
    principal: Annotated[Principal, Depends(get_executive_principal)],
    db: Annotated[Session, Depends(get_db)],
):
    previous = replay(db, request, principal, payload)
    if previous:
        return ORJSONResponse(status_code=previous[0], content=previous[1])
    conversation = owned_conversation(db, principal, conversation_id, lock=True)
    if conversation.archived_at:
        raise AppError(409, "conversation_archived", "已归档会话不能继续发送消息")
    files: list[FileAsset] = []
    if payload.file_ids:
        files = db.scalars(
            select(FileAsset).where(
                FileAsset.id.in_(payload.file_ids),
                FileAsset.enterprise_id == principal.enterprise_id,
                FileAsset.uploaded_by_user_id == principal.user.id,
                FileAsset.deleted_at.is_(None),
            )
        ).all()
        if {item.id for item in files} != set(payload.file_ids):
            raise AppError(404, "file_not_found", "一个或多个文件不存在")
    sequence = (
        db.scalar(
            select(func.coalesce(func.max(Message.sequence), 0)).where(
                Message.conversation_id == conversation.id
            )
        )
        or 0
    ) + 1
    message = Message(
        conversation_id=conversation.id,
        author_user_id=principal.user.id,
        role="user",
        content=payload.content,
        content_json={"file_ids": [str(item.id) for item in files]},
        sequence=sequence,
        status="completed",
    )
    db.add(message)
    db.flush()
    assistant_message = Message(
        conversation_id=conversation.id,
        role="assistant",
        content="",
        content_json={},
        sequence=sequence + 1,
        status="queued",
    )
    db.add(assistant_message)
    db.flush()
    for item in files:
        exists = db.scalar(
            select(ConversationFile).where(
                ConversationFile.conversation_id == conversation.id,
                ConversationFile.file_id == item.id,
            )
        )
        if exists is None:
            db.add(ConversationFile(conversation_id=conversation.id, file_id=item.id))
    job = Job(
        enterprise_id=principal.enterprise_id,
        created_by_user_id=principal.user.id,
        job_type="assistant_response",
        payload_json={
            "conversation_id": str(conversation.id),
            "message_id": str(message.id),
            "assistant_message_id": str(assistant_message.id),
            "organization_unit_id": (
                str(conversation.organization_unit_id)
                if conversation.organization_unit_id
                else None
            ),
        },
        scope_snapshot_json=build_scope_snapshot(
            db,
            principal,
            conversation.organization_unit_id,
        ),
        status="queued",
        max_attempts=get_settings().worker_job_max_attempts,
    )
    db.add(job)
    conversation.last_message_at = utc_now()
    db.flush()
    output = MessageOut.model_validate(message)
    record_audit(
        db,
        request,
        "message.created",
        actor=principal.user,
        session=principal.session,
        target_type="message",
        target_id=message.id,
        metadata={
            "conversation_id": str(conversation.id),
            "job_id": str(job.id),
            "assistant_message_id": str(assistant_message.id),
        },
    )
    save_response(db, request, principal, payload, 202, output)
    db.commit()
    return output
