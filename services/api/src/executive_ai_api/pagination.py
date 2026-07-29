from __future__ import annotations

import base64
import uuid

from .errors import AppError


def encode_cursor(item_id: uuid.UUID) -> str:
    return base64.urlsafe_b64encode(str(item_id).encode()).decode().rstrip("=")


def decode_cursor(cursor: str | None) -> uuid.UUID | None:
    if not cursor:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        return uuid.UUID(base64.urlsafe_b64decode(padded).decode())
    except (ValueError, UnicodeError) as exc:
        raise AppError(422, "invalid_cursor", "分页游标无效") from exc
