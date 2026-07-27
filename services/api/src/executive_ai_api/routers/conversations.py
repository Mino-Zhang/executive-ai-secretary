from __future__ import annotations

import asyncio
import json
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, status
from fastapi.responses import ORJSONResponse, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..audit import record_audit
from ..authz import (
    Principal,
    assert_org_scope,
    build_assistant_scope_snapshot,
    build_scope_snapshot,
    get_executive_principal,
)
from ..config import get_settings
from ..database import SessionLocal, get_db
from ..errors import AppError
from ..idempotency import replay, save_response
from ..models import (
    Clarification,
    Conversation,
    Job,
    Message,
    MessageEvidence,
    Project,
    ProjectConversation,
)
from ..pagination import decode_cursor, encode_cursor
from ..schemas import (
    ClarificationOut,
    ClarificationResolve,
    ConversationCreate,
    ConversationOut,
    ConversationUpdate,
    MessageCreate,
    MessageEvidenceOut,
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
    if item.organization_unit_id is not None:
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
    if payload.organization_unit_id is not None:
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


@router.get("/{conversation_id}/stream")
async def stream_conversation(
    conversation_id: uuid.UUID,
    request: Request,
    principal: Annotated[Principal, Depends(get_executive_principal)],
    db: Annotated[Session, Depends(get_db)],
    after_sequence: int = Query(default=0, ge=0),
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    owned_conversation(db, principal, conversation_id)
    resume_sequence = 0
    resume_updated_ms = 0
    try:
        if last_event_id and ":" in last_event_id:
            resume_sequence_text, resume_updated_text = last_event_id.split(":", 1)
            resume_sequence = int(resume_sequence_text)
            resume_updated_ms = int(resume_updated_text)
        elif last_event_id:
            resume_sequence = int(last_event_id)
    except ValueError:
        resume_sequence = 0
        resume_updated_ms = 0
    cursor = max(after_sequence, resume_sequence)
    enterprise_id = principal.enterprise_id
    owner_user_id = principal.user.id

    async def events():
        nonlocal cursor
        seen_updates: dict[uuid.UUID, str] = {}
        idle_cycles = 0
        while not await request.is_disconnected():
            with SessionLocal() as stream_db:
                conversation = stream_db.scalar(
                    select(Conversation).where(
                        Conversation.id == conversation_id,
                        Conversation.enterprise_id == enterprise_id,
                        Conversation.owner_user_id == owner_user_id,
                    )
                )
                if conversation is None:
                    yield 'event: error\ndata: {"code":"conversation_not_found"}\n\n'
                    return
                rows = stream_db.scalars(
                    select(Message)
                    .where(
                        Message.conversation_id == conversation_id,
                        Message.sequence >= max(1, cursor),
                    )
                    .order_by(Message.sequence)
                ).all()
                emitted = False
                for item in rows:
                    updated_marker = item.updated_at.isoformat()
                    updated_ms = int(item.updated_at.timestamp() * 1000)
                    is_resumed_item = (
                        item.sequence == resume_sequence and updated_ms <= resume_updated_ms
                    )
                    if (
                        item.sequence < cursor
                        or is_resumed_item
                        or seen_updates.get(item.id) == updated_marker
                    ):
                        continue
                    seen_updates[item.id] = updated_marker
                    cursor = item.sequence
                    emitted = True
                    payload = MessageOut.model_validate(item).model_dump(mode="json")
                    yield (
                        f"id: {cursor}:{updated_ms}\nevent: message\ndata: "
                        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                        + "\n\n"
                    )
            if emitted:
                idle_cycles = 0
            else:
                idle_cycles += 1
                if idle_cycles % 20 == 0:
                    yield "event: heartbeat\ndata: {}\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/{conversation_id}/clarifications/{clarification_id}",
    response_model=ClarificationOut,
)
def resolve_clarification(
    conversation_id: uuid.UUID,
    clarification_id: uuid.UUID,
    payload: ClarificationResolve,
    request: Request,
    principal: Annotated[Principal, Depends(get_executive_principal)],
    db: Annotated[Session, Depends(get_db)],
) -> ClarificationOut:
    conversation = owned_conversation(db, principal, conversation_id, lock=True)
    clarification = db.scalar(
        select(Clarification)
        .where(
            Clarification.id == clarification_id,
            Clarification.conversation_id == conversation.id,
        )
        .with_for_update()
    )
    if clarification is None:
        raise AppError(404, "clarification_not_found", "范围确认不存在")
    if clarification.status != "pending":
        raise AppError(409, "clarification_resolved", "该范围确认已经处理")
    clarification.status = "resolved"
    option = next(
        (
            item
            for item in clarification.options_json
            if isinstance(item, dict) and str(item.get("value")) == payload.value
        ),
        None,
    )
    if option is None:
        raise AppError(422, "clarification_option_invalid", "请选择系统提供的有效查询范围")
    try:
        selected_organization_id = uuid.UUID(payload.value)
    except ValueError as exc:
        raise AppError(422, "clarification_option_invalid", "查询范围格式无效") from exc
    assert_org_scope(db, principal, selected_organization_id)
    clarification.selected_value = payload.value
    clarification.resolved_by_user_id = principal.user.id
    clarification.resolved_at = utc_now()
    original_message = db.get(Message, clarification.message_id)
    original_question = original_message.content if original_message else ""
    sequence = (
        db.scalar(
            select(func.coalesce(func.max(Message.sequence), 0)).where(
                Message.conversation_id == conversation.id
            )
        )
        or 0
    ) + 1
    user_message = Message(
        conversation_id=conversation.id,
        author_user_id=principal.user.id,
        role="user",
        content=(
            f"{original_question}\n\n已确认查询范围：{option.get('label', payload.value)}"
            if original_question
            else payload.value
        ),
        content_json={
            "clarification_id": str(clarification.id),
            "selected_value": payload.value,
            "original_message_id": str(clarification.message_id),
        },
        sequence=sequence,
        status="completed",
    )
    assistant_message = Message(
        conversation_id=conversation.id,
        role="assistant",
        content="",
        content_json={},
        sequence=sequence + 1,
        status="queued",
    )
    db.add_all([user_message, assistant_message])
    db.flush()
    db.add(
        Job(
            enterprise_id=principal.enterprise_id,
            created_by_user_id=principal.user.id,
            job_type="assistant_response",
            payload_json={
                "conversation_id": str(conversation.id),
                "message_id": str(user_message.id),
                "assistant_message_id": str(assistant_message.id),
                "clarification_id": str(clarification.id),
            },
            scope_snapshot_json=build_scope_snapshot(
                db, principal, selected_organization_id
            ),
            status="queued",
            max_attempts=get_settings().worker_job_max_attempts,
        )
    )
    conversation.last_message_at = utc_now()
    record_audit(
        db,
        request,
        "clarification.resolved",
        actor=principal.user,
        session=principal.session,
        target_type="clarification",
        target_id=clarification.id,
    )
    db.commit()
    return ClarificationOut.model_validate(clarification)


@router.get(
    "/{conversation_id}/messages/{message_id}/evidence",
    response_model=list[MessageEvidenceOut],
)
def get_message_evidence(
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    principal: Annotated[Principal, Depends(get_executive_principal)],
    db: Annotated[Session, Depends(get_db)],
) -> list[MessageEvidenceOut]:
    owned_conversation(db, principal, conversation_id)
    message = db.scalar(
        select(Message).where(
            Message.id == message_id,
            Message.conversation_id == conversation_id,
        )
    )
    if message is None:
        raise AppError(404, "message_not_found", "消息不存在")
    rows = db.scalars(
        select(MessageEvidence)
        .where(MessageEvidence.message_id == message.id)
        .order_by(MessageEvidence.created_at, MessageEvidence.id)
    ).all()
    return [MessageEvidenceOut.model_validate(row) for row in rows]


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
    if payload.file_ids:
        raise AppError(410, "file_upload_disabled", "当前阶段不支持在会话中使用文件")
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
        content_json={},
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
    allowed_scope = build_assistant_scope_snapshot(
        db, principal, conversation.organization_unit_id
    )
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
        scope_snapshot_json=allowed_scope,
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
