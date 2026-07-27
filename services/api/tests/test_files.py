from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from executive_ai_api.database import SessionLocal
from executive_ai_api.models import AuditEvent

from .conftest import login_and_change_password


@pytest.mark.parametrize(
    ("name", "media_type"),
    [
        ("report.pdf", "application/pdf"),
        (
            "brief.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        (
            "ledger.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ),
        (
            "deck.pptx",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ),
    ],
)
def test_file_upload_is_closed_for_every_formerly_supported_format(
    client, seeded, name: str, media_type: str
) -> None:
    auth = login_and_change_password(client)
    response = client.post(
        "/api/v1/files",
        headers={"X-CSRF-Token": auth["csrf_token"]},
        files={"file": (name, b"disabled payload", media_type)},
    )
    assert response.status_code == 410
    assert response.json()["error"]["code"] == "file_upload_disabled"
    with SessionLocal() as db:
        event = db.scalar(
            select(AuditEvent)
            .where(AuditEvent.action == "file.upload_rejected")
            .order_by(AuditEvent.created_at.desc())
        )
        assert event is not None
        assert event.failure_reason_code == "file_upload_disabled"


def test_file_attachment_is_rejected_at_the_message_boundary(client, seeded) -> None:
    auth = login_and_change_password(client)
    conversation = client.post(
        "/api/v1/conversations",
        headers={"X-CSRF-Token": auth["csrf_token"]},
        json={"title": "无文件会话"},
    )
    assert conversation.status_code == 201, conversation.text
    response = client.post(
        f"/api/v1/conversations/{conversation.json()['id']}/messages",
        headers={"X-CSRF-Token": auth["csrf_token"]},
        json={"content": "分析这个文件", "file_ids": [str(uuid.uuid4())]},
    )
    assert response.status_code == 410
    assert response.json()["error"]["code"] == "file_upload_disabled"


def test_file_registry_stays_empty_when_upload_is_disabled(client, seeded) -> None:
    login_and_change_password(client)
    response = client.get("/api/v1/files")
    assert response.status_code == 200
    assert response.json()["items"] == []
