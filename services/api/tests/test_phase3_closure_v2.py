from __future__ import annotations

import uuid

from sqlalchemy import select

from executive_ai_api.database import SessionLocal
from executive_ai_api.models import (
    EnterpriseModelAuthorization,
    Job,
    Message,
    ProjectConversation,
)
from executive_ai_api.security import utc_now

from .conftest import NEW_PASSWORD, login, login_and_change_password


def test_message_send_is_blocked_without_an_authorized_model(client, seeded) -> None:
    auth = login_and_change_password(client)
    headers = {"X-CSRF-Token": auth["csrf_token"]}
    conversation = client.post(
        "/api/v1/conversations",
        headers=headers,
        json={"title": "尚无授权模型"},
    )
    assert conversation.status_code == 201, conversation.text
    response = client.post(
        f"/api/v1/conversations/{conversation.json()['id']}/messages",
        headers=headers,
        json={"content": "你好"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "authorized_model_missing"


def test_model_test_authorize_default_and_credential_rotation(
    client, seeded, monkeypatch
) -> None:
    admin = login(client, "admin@example.com")
    headers = {"X-CSRF-Token": admin["csrf_token"]}
    configured = client.put(
        "/api/v1/admin/model-provider",
        headers=headers,
        json={
            "model_id": "glm-5.2",
            "api_key": "unit-test-model-authorization-key-123456",
        },
    )
    assert configured.status_code == 200, configured.text

    monkeypatch.setattr(
        "executive_ai_api.routers.admin_models.test_anspire_provider",
        lambda *args, **kwargs: {"latency_ms": 26},
    )

    unauthorized = client.patch(
        "/api/v1/admin/models/qwen3.5-plus/authorization",
        headers=headers,
        json={"is_authorized": True},
    )
    assert unauthorized.status_code == 409
    assert unauthorized.json()["error"]["code"] == "anspire_test_required"

    for model_id in ("glm-5.2", "qwen3.5-plus"):
        tested = client.post(f"/api/v1/admin/models/{model_id}/test", headers=headers)
        assert tested.status_code == 200, tested.text
        authorized = client.patch(
            f"/api/v1/admin/models/{model_id}/authorization",
            headers=headers,
            json={"is_authorized": True},
        )
        assert authorized.status_code == 200, authorized.text

    catalog = client.get("/api/v1/admin/models", headers=headers).json()["models"]
    authorized_models = [item for item in catalog if item["is_authorized"]]
    assert {item["model_id"] for item in authorized_models} == {
        "glm-5.2",
        "qwen3.5-plus",
    }
    assert [item["model_id"] for item in authorized_models if item["is_default"]] == [
        "glm-5.2"
    ]

    changed_default = client.patch(
        "/api/v1/admin/models/qwen3.5-plus/default",
        headers=headers,
        json={"is_default": True},
    )
    assert changed_default.status_code == 200, changed_default.text
    assert changed_default.json()["is_default"] is True

    login_and_change_password(client)
    available = client.get("/api/v1/models")
    assert available.status_code == 200
    assert [item["model_id"] for item in available.json()] == [
        "qwen3.5-plus",
        "glm-5.2",
    ]

    admin = login(client, "admin@example.com")
    headers = {"X-CSRF-Token": admin["csrf_token"]}
    rotated = client.put(
        "/api/v1/admin/model-provider",
        headers=headers,
        json={
            "model_id": "qwen3.5-plus",
            "api_key": "unit-test-model-authorization-rotated-654321",
        },
    )
    assert rotated.status_code == 200, rotated.text
    assert rotated.json()["credential_version"] == 2

    login(client, "executive@example.com", NEW_PASSWORD)
    assert client.get("/api/v1/models").json() == []
    with SessionLocal() as db:
        rows = db.scalars(select(EnterpriseModelAuthorization)).all()
        assert rows
        assert all(not row.is_authorized and not row.is_default for row in rows)


def test_conversation_requires_model_and_snapshots_retry(
    client, seeded, authorized_model
) -> None:
    auth = login_and_change_password(client)
    headers = {"X-CSRF-Token": auth["csrf_token"]}
    conversation = client.post(
        "/api/v1/conversations",
        headers=headers,
        json={"title": "模型快照", "model_id": authorized_model},
    )
    assert conversation.status_code == 201, conversation.text
    sent = client.post(
        f"/api/v1/conversations/{conversation.json()['id']}/messages",
        headers=headers,
        json={"content": "请概括当前经营情况", "model_id": authorized_model},
    )
    assert sent.status_code == 202, sent.text
    with SessionLocal.begin() as db:
        job = db.scalar(select(Job).where(Job.job_type == "assistant_response"))
        assert job is not None
        job.status = "failed"
        job.completed_at = utc_now()
        source_message = db.get(Message, uuid.UUID(sent.json()["id"]))
        assert source_message is not None
        assert source_message.requested_model_id == authorized_model
        original_job_id = job.id

    retried = client.post(
        f"/api/v1/jobs/{original_job_id}/retry",
        headers=headers,
    )
    assert retried.status_code == 202, retried.text
    assert retried.json()["payload_json"]["model_id"] == authorized_model
    with SessionLocal() as db:
        assistant_id = retried.json()["payload_json"]["assistant_message_id"]
        assistant = db.get(Message, uuid.UUID(assistant_id))
        assert assistant is not None
        assert assistant.requested_model_id == authorized_model


def test_project_and_recent_placement_are_mutually_exclusive(
    client, seeded, authorized_model
) -> None:
    auth = login_and_change_password(client)
    headers = {"X-CSRF-Token": auth["csrf_token"]}
    project = client.post(
        "/api/v1/projects",
        headers={**headers, "Idempotency-Key": "phase3-project"},
        json={"name": "年度经营计划"},
    )
    assert project.status_code == 201, project.text
    unassigned = client.post(
        "/api/v1/conversations",
        headers=headers,
        json={"title": "随性讨论", "model_id": authorized_model},
    )
    project_chat = client.post(
        "/api/v1/conversations",
        headers=headers,
        json={
            "title": "项目经营复盘",
            "project_id": project.json()["id"],
            "model_id": authorized_model,
        },
    )
    assert unassigned.status_code == project_chat.status_code == 201

    recent_ids = {
        item["id"]
        for item in client.get("/api/v1/conversations?placement=unassigned").json()[
            "items"
        ]
    }
    assert unassigned.json()["id"] in recent_ids
    assert project_chat.json()["id"] not in recent_ids

    moved = client.patch(
        f"/api/v1/conversations/{unassigned.json()['id']}/project",
        headers=headers,
        json={"project_id": project.json()["id"]},
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["project_id"] == project.json()["id"]
    project_items = client.get(
        f"/api/v1/conversations?placement=project&project_id={project.json()['id']}"
    ).json()["items"]
    assert {item["id"] for item in project_items} == {
        unassigned.json()["id"],
        project_chat.json()["id"],
    }
    assert client.get("/api/v1/conversations?placement=unassigned").json()["items"] == []

    moved_out = client.patch(
        f"/api/v1/conversations/{project_chat.json()['id']}/project",
        headers=headers,
        json={"project_id": None},
    )
    assert moved_out.status_code == 200, moved_out.text
    assert moved_out.json()["project_id"] is None
    recent_ids = {
        item["id"]
        for item in client.get("/api/v1/conversations?placement=unassigned").json()[
            "items"
        ]
    }
    assert recent_ids == {project_chat.json()["id"]}
    with SessionLocal() as db:
        assert db.scalar(
            select(ProjectConversation).where(
                ProjectConversation.conversation_id
                == uuid.UUID(unassigned.json()["id"])
            )
        )
