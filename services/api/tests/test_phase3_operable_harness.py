from __future__ import annotations

import uuid
from copy import deepcopy

from sqlalchemy import select

from executive_ai_api.database import SessionLocal
from executive_ai_api.models import (
    AuditEvent,
    Conversation,
    ExecutivePersonalProfile,
    HarnessConfigVersion,
    HarnessDiagnosticGrant,
    Job,
    Memory,
    Message,
    OrganizationUnit,
)

from .conftest import login


def _csrf(session: dict) -> dict[str, str]:
    return {"X-CSRF-Token": session["csrf_token"]}


def test_multi_organization_scope_is_atomic_and_snapshotted(
    client, seeded, authorized_model
) -> None:
    with SessionLocal.begin() as db:
        pending = db.get(OrganizationUnit, seeded["pending_id"])
        pending.data_connected = True

    session = login(client, "other@example.com")
    created = client.post(
        "/api/v1/conversations",
        headers=_csrf(session),
        json={
            "title": "多事业部经营比较",
            "organization_scope": {
                "mode": "selected",
                "organization_unit_ids": [
                    str(seeded["east_id"]),
                    str(seeded["west_id"]),
                ],
            },
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["organization_scope"]["mode"] == "selected"
    assert set(body["organization_scope"]["organization_unit_ids"]) == {
        str(seeded["east_id"]),
        str(seeded["west_id"]),
    }

    sent = client.post(
        f"/api/v1/conversations/{body['id']}/messages",
        headers={**_csrf(session), "Idempotency-Key": "phase3-scope-message"},
        json={
            "content": "只看西部事业部的回款",
            "file_ids": [],
            "organization_scope": {
                "mode": "selected",
                "organization_unit_ids": [str(seeded["west_id"])],
            },
        },
    )
    assert sent.status_code == 202, sent.text
    messages = client.get(
        f"/api/v1/conversations/{body['id']}/messages"
    ).json()["items"]
    assert [item["role"] for item in messages] == ["system", "user", "assistant"]
    assert messages[0]["content_json"]["event"] == "organization_scope_changed"

    with SessionLocal() as db:
        conversation = db.get(Conversation, uuid.UUID(body["id"]))
        job = db.scalar(select(Job).where(Job.enterprise_id == seeded["enterprise_id"]))
        assert conversation.scope_mode == "selected"
        assert conversation.organization_unit_id == seeded["west_id"]
        assert job.scope_snapshot_json["organization_unit_ids"] == [str(seeded["west_id"])]
        assert job.harness_version_id is not None


def test_scope_compatibility_rejects_mixed_fields_and_collapses_full_selection(
    client, seeded
) -> None:
    session = login(client, "other@example.com")
    mixed = client.post(
        "/api/v1/conversations",
        headers=_csrf(session),
        json={
            "title": "无效请求",
            "organization_unit_id": str(seeded["east_id"]),
            "organization_scope": {
                "mode": "selected",
                "organization_unit_ids": [str(seeded["east_id"])],
            },
        },
    )
    assert mixed.status_code == 422

    collapsed = client.post(
        "/api/v1/conversations",
        headers=_csrf(session),
        json={
            "title": "全部授权范围",
            "organization_scope": {
                "mode": "selected",
                "organization_unit_ids": [
                    str(seeded["east_id"]),
                    str(seeded["west_id"]),
                ],
            },
        },
    )
    assert collapsed.status_code == 201, collapsed.text
    assert collapsed.json()["organization_scope"]["mode"] == "all_authorized"
    assert collapsed.json()["organization_scope"]["organization_unit_ids"] == []


def test_harness_config_versions_are_immutable_validated_and_restorable(
    client, seeded
) -> None:
    session = login(client, "admin@example.com")
    current = client.get("/api/v1/admin/harness/config")
    assert current.status_code == 200, current.text
    first = current.json()
    assert first["safety_kernel"]["internet_access"] is False
    assert first["safety_kernel"]["tool_allowlist"] == "registered_enabled_mcp_only"

    updated_config = deepcopy(first["config"])
    updated_config["prompts"]["system"] += " 回答前必须区分事实、推断与建议。"
    updated = client.patch(
        "/api/v1/admin/harness/config",
        headers=_csrf(session),
        json={"base_version": first["version"], "config": updated_config},
    )
    assert updated.status_code == 200, updated.text
    second = updated.json()
    assert second["version"] == first["version"] + 1
    assert second["config_hash"] != first["config_hash"]

    stale = client.patch(
        "/api/v1/admin/harness/config",
        headers=_csrf(session),
        json={"base_version": first["version"], "config": updated_config},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "harness_version_conflict"

    invalid = deepcopy(second["config"])
    invalid["fast_rules"][0]["candidate_tools"] = ["arbitrary_sql"]
    rejected = client.patch(
        "/api/v1/admin/harness/config",
        headers=_csrf(session),
        json={"base_version": second["version"], "config": invalid},
    )
    assert rejected.status_code == 422

    restored = client.post(
        f"/api/v1/admin/harness/versions/{first['id']}/restore",
        headers=_csrf(session),
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["version"] == second["version"] + 1
    assert restored.json()["config"] == first["config"]
    with SessionLocal() as db:
        rows = db.scalars(
            select(HarnessConfigVersion).order_by(HarnessConfigVersion.version)
        ).all()
        assert [row.is_active for row in rows] == [False, False, True]


def test_executive_profile_and_memory_are_encrypted_at_rest(client, seeded) -> None:
    session = login(client, "other@example.com")
    profile = client.put(
        "/api/v1/auth/personal-profile",
        headers=_csrf(session),
        json={
            "salutation": "Ryan 总",
            "amount_unit": "yi",
            "response_style": "concise",
            "locale": "zh-CN",
            "memory_enabled": True,
        },
    )
    assert profile.status_code == 200, profile.text
    assert profile.json()["salutation"] == "Ryan 总"

    memory = client.post(
        "/api/v1/memories",
        headers={**_csrf(session), "Idempotency-Key": "phase3-private-memory"},
        json={"title": "表达偏好", "content": "我不喜欢夸张的战略口号", "kind": "preference"},
    )
    assert memory.status_code == 201, memory.text
    assert memory.json()["content"] == "我不喜欢夸张的战略口号"

    with SessionLocal() as db:
        profile_row = db.scalar(select(ExecutivePersonalProfile))
        memory_row = db.scalar(select(Memory))
        assert profile_row is not None and "Ryan" not in profile_row.profile_ciphertext
        assert profile_row.profile_nonce
        assert memory_row is not None and memory_row.content == ""
        assert "夸张" not in memory_row.content_ciphertext
        assert memory_row.content_nonce

    with client.__class__(client.app) as admin_client:
        login(admin_client, "admin@example.com")
        assert admin_client.get("/api/v1/auth/personal-profile").status_code == 403
        assert admin_client.get("/api/v1/memories").status_code == 403


def test_diagnostic_share_requires_owner_and_is_revocable(
    client, seeded, authorized_model
) -> None:
    session = login(client, "other@example.com")
    conversation = client.post(
        "/api/v1/conversations",
        headers=_csrf(session),
        json={"title": "诊断授权"},
    ).json()
    client.post(
        f"/api/v1/conversations/{conversation['id']}/messages",
        headers={**_csrf(session), "Idempotency-Key": "phase3-diagnostic-message"},
        json={"content": "本月回款如何？", "file_ids": []},
    )
    messages = client.get(
        f"/api/v1/conversations/{conversation['id']}/messages"
    ).json()["items"]
    assistant = next(item for item in messages if item["role"] == "assistant")
    shared = client.post(
        f"/api/v1/conversations/{conversation['id']}/messages/{assistant['id']}/diagnostic-share",
        headers=_csrf(session),
    )
    assert shared.status_code == 200, shared.text

    with SessionLocal() as db:
        grant = db.scalar(select(HarnessDiagnosticGrant))
        assert grant is not None and grant.revoked_at is None

    revoked = client.delete(
        f"/api/v1/conversations/{conversation['id']}/messages/{assistant['id']}/diagnostic-share",
        headers=_csrf(session),
    )
    assert revoked.status_code == 204
    with SessionLocal() as db:
        grant = db.scalar(select(HarnessDiagnosticGrant))
        actions = set(db.scalars(select(AuditEvent.action)).all())
        assert grant.revoked_at is not None
        assert {"harness.diagnostic_shared", "harness.diagnostic_revoked"}.issubset(actions)

        # Content remains in owner-private tables; no grant can widen direct admin access.
        assert db.scalar(
            select(Message).where(Message.id == uuid.UUID(assistant["id"]))
        ) is not None
