from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from .models import Job, Message

ASSISTANT_NOT_CONFIGURED_CONTENT = "未配置真实处理器"


def close_assistant_placeholder(
    db: Session,
    job: Job,
    *,
    status: str,
    content: str | None = None,
) -> Message | None:
    """Close a persisted assistant placeholder without inventing an answer."""
    raw_id = (job.payload_json or {}).get("assistant_message_id")
    try:
        message_id = uuid.UUID(str(raw_id))
    except (TypeError, ValueError):
        return None
    message = db.get(Message, message_id)
    if message is None or message.role != "assistant":
        return None
    message.status = status
    if content is not None:
        message.content = content
    return message
