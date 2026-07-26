from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import select

from executive_ai_api.audit_integrity import (
    calculate_integrity_hash,
    verify_audit_chain,
    verify_audit_event,
)
from executive_ai_api.config import get_settings
from executive_ai_api.database import SessionLocal
from executive_ai_api.models import (
    AuditChainHead,
    AuditEvent,
    Conversation,
    DataScopeGrant,
    Job,
    JobAttempt,
    Message,
    OrganizationUnit,
    Project,
    Report,
    ReportVersion,
)

from .conftest import login, login_and_change_password


def test_organization_scope_is_filtered_and_enforced(client, seeded) -> None:
    auth = login_and_change_password(client)
    scopes = client.get("/api/v1/organization-units?enabled_for_analysis=true")
    assert scopes.status_code == 200
    assert [item["code"] for item in scopes.json()["items"]] == ["east"]

    forbidden = client.post(
        "/api/v1/conversations",
        headers={"X-CSRF-Token": auth["csrf_token"]},
        json={"title": "越权事业部", "organization_unit_id": str(seeded["west_id"])},
    )
    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "data_scope_forbidden"


def test_conversation_message_project_and_idempotency(client, seeded) -> None:
    auth = login_and_change_password(client)
    headers = {"X-CSRF-Token": auth["csrf_token"], "Idempotency-Key": "project-001"}
    first = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"name": "回款与现金流", "organization_unit_id": str(seeded["east_id"])},
    )
    assert first.status_code == 201, first.text
    repeated = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"name": "回款与现金流", "organization_unit_id": str(seeded["east_id"])},
    )
    assert repeated.status_code == 201
    assert repeated.json()["id"] == first.json()["id"]

    conflict = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"name": "不同项目", "organization_unit_id": str(seeded["east_id"])},
    )
    assert conflict.status_code == 409

    conversation = client.post(
        "/api/v1/conversations",
        headers={"X-CSRF-Token": auth["csrf_token"]},
        json={
            "title": "本月经营情况",
            "organization_unit_id": str(seeded["east_id"]),
            "project_id": first.json()["id"],
        },
    )
    assert conversation.status_code == 201
    message = client.post(
        f"/api/v1/conversations/{conversation.json()['id']}/messages",
        headers={"X-CSRF-Token": auth["csrf_token"]},
        json={"content": "本月回款差距来自哪些客户？", "file_ids": []},
    )
    assert message.status_code == 202
    assert message.json()["role"] == "user"
    assert message.json()["status"] == "completed"
    messages = client.get(f"/api/v1/conversations/{conversation.json()['id']}/messages").json()[
        "items"
    ]
    assert [item["role"] for item in messages] == ["user", "assistant"]
    assert messages[0]["content"] == "本月回款差距来自哪些客户？"
    assert messages[1]["content"] == ""
    assert messages[1]["status"] == "queued"
    with SessionLocal() as db:
        assert db.scalar(select(AuditEvent).where(AuditEvent.action == "message.created"))
        job = db.scalar(select(Job).where(Job.job_type == "assistant_response"))
        assert job.payload_json["assistant_message_id"] == messages[1]["id"]


def test_admin_and_fde_cannot_read_executive_conversation(client, seeded) -> None:
    with SessionLocal.begin() as db:
        secret = Conversation(
            enterprise_id=seeded["enterprise_id"],
            owner_user_id=seeded["users"]["other@example.com"],
            organization_unit_id=seeded["east_id"],
            title="董事长私密会话",
        )
        db.add(secret)
        db.flush()
        secret_id = secret.id

    login(client, "admin@example.com")
    assert client.get(f"/api/v1/conversations/{secret_id}").status_code == 403
    with client.__class__(client.app) as fde_client:
        login(fde_client, "fde@example.com")
        assert fde_client.get(f"/api/v1/conversations/{secret_id}").status_code == 403


def test_admin_and_fde_cannot_create_business_resources(client, seeded) -> None:
    for email in ("admin@example.com", "fde@example.com"):
        with client.__class__(client.app) as role_client:
            login(role_client, email)
            for path in (
                "/api/v1/organization-units",
                "/api/v1/conversations",
                "/api/v1/projects",
                "/api/v1/files",
                "/api/v1/memories",
                "/api/v1/reports",
                "/api/v1/jobs",
            ):
                response = role_client.get(path)
                assert response.status_code == 403, (email, path, response.text)
                assert response.json()["error"]["code"] == "role_forbidden"


def test_audit_hmac_verification_detects_tampering(client, seeded) -> None:
    auth = login(client, "admin@example.com")
    with SessionLocal() as db:
        audit_count_before = len(
            db.scalars(
                select(AuditEvent).where(
                    AuditEvent.enterprise_id == seeded["enterprise_id"]
                )
            ).all()
        )
    verified = client.post(
        "/api/v1/admin/audit-events/verify",
        headers={"X-CSRF-Token": auth["csrf_token"]},
    )
    assert verified.status_code == 200, verified.text
    assert verified.json()["valid"] is True
    with SessionLocal.begin() as db:
        audit_events = db.scalars(
            select(AuditEvent)
            .where(AuditEvent.enterprise_id == seeded["enterprise_id"])
            .order_by(AuditEvent.chain_sequence)
        ).all()
        assert len(audit_events) == audit_count_before + 1
        assert audit_events[-1].action == "admin.audit_integrity_verified"
        assert verify_audit_chain(db, seeded["enterprise_id"]).valid is True
        event = db.scalar(select(AuditEvent).order_by(AuditEvent.created_at).limit(1))
        assert event.integrity_hash and len(event.integrity_hash) == 64
        assert event.environment == "test"
        assert verify_audit_event(event)
        assert event.scope_summary_json["enterprise_wide"] is True
        event.metadata_json = {"tampered": True}
    detected = client.post(
        "/api/v1/admin/audit-events/verify",
        headers={"X-CSRF-Token": auth["csrf_token"]},
    )
    assert detected.status_code == 200
    assert detected.json()["valid"] is False
    assert detected.json()["invalid_event_ids"]
    with SessionLocal() as db:
        audit_count_after_tampering = len(
            db.scalars(
                select(AuditEvent).where(
                    AuditEvent.enterprise_id == seeded["enterprise_id"]
                )
            ).all()
        )
    detected_again = client.post(
        "/api/v1/admin/audit-events/verify",
        headers={"X-CSRF-Token": auth["csrf_token"]},
    )
    assert detected_again.status_code == 200
    assert detected_again.json() == detected.json()
    with SessionLocal() as db:
        assert len(
            db.scalars(
                select(AuditEvent).where(
                    AuditEvent.enterprise_id == seeded["enterprise_id"]
                )
            ).all()
        ) == audit_count_after_tampering


def test_audit_chain_detects_middle_event_deletion(client, seeded) -> None:
    auth = login(client, "admin@example.com")
    first_check = client.post(
        "/api/v1/admin/audit-events/verify",
        headers={"X-CSRF-Token": auth["csrf_token"]},
    )
    assert first_check.status_code == 200
    with SessionLocal.begin() as db:
        events = db.scalars(
            select(AuditEvent)
            .where(AuditEvent.enterprise_id == seeded["enterprise_id"])
            .order_by(AuditEvent.chain_sequence)
        ).all()
        assert len(events) >= 2
        db.delete(events[0])
    with SessionLocal() as db:
        result = verify_audit_chain(db, seeded["enterprise_id"])
        assert result.valid is False
        assert "chain_sequence_gap" in result.errors
        assert "chain_previous_hash_mismatch" in result.errors


def test_audit_chain_detects_tail_deletion_and_anchor_tampering(client, seeded) -> None:
    auth = login(client, "admin@example.com")
    with SessionLocal.begin() as db:
        tail = db.scalar(
            select(AuditEvent)
            .where(AuditEvent.enterprise_id == seeded["enterprise_id"])
            .order_by(AuditEvent.chain_sequence.desc())
            .limit(1)
        )
        assert tail is not None
        db.delete(tail)
    with SessionLocal() as db:
        deleted = verify_audit_chain(db, seeded["enterprise_id"])
        assert deleted.valid is False
        assert "chain_head_sequence_mismatch" in deleted.errors
        assert "chain_head_hash_mismatch" in deleted.errors
    with SessionLocal.begin() as db:
        head = db.get(AuditChainHead, f"enterprise:{seeded['enterprise_id']}")
        assert head is not None
        head.anchor_hash = "f" * 64
    with SessionLocal() as db:
        tampered = verify_audit_chain(db, seeded["enterprise_id"])
        assert tampered.valid is False
        assert "chain_anchor_hmac_mismatch" in tampered.errors
    with SessionLocal.begin() as db:
        for event in db.scalars(
            select(AuditEvent).where(AuditEvent.enterprise_id == seeded["enterprise_id"])
        ).all():
            db.delete(event)
        head = db.get(AuditChainHead, f"enterprise:{seeded['enterprise_id']}")
        assert head is not None
        db.delete(head)
    with SessionLocal() as db:
        fully_deleted = verify_audit_chain(db, seeded["enterprise_id"])
        assert fully_deleted.valid is False
        assert fully_deleted.errors == ["chain_anchor_missing"]

    for _ in range(2):
        response = client.post(
            "/api/v1/admin/audit-events/verify",
            headers={"X-CSRF-Token": auth["csrf_token"]},
        )
        assert response.status_code == 200, response.text
        assert response.json()["valid"] is False
        assert response.json()["errors"] == ["chain_anchor_missing"]
        with SessionLocal() as db:
            assert db.scalars(
                select(AuditEvent).where(
                    AuditEvent.enterprise_id == seeded["enterprise_id"]
                )
            ).all() == []
            assert (
                db.get(AuditChainHead, f"enterprise:{seeded['enterprise_id']}")
                is None
            )


def test_legacy_v1_audit_signature_remains_verifiable() -> None:
    event = AuditEvent(
        id=uuid.uuid4(),
        created_at=datetime.now(UTC),
        enterprise_id=None,
        actor_user_id=None,
        session_id=None,
        environment="test",
        actor_role=None,
        action="legacy.imported",
        target_type=None,
        target_id=None,
        outcome="success",
        failure_reason_code=None,
        request_id=None,
        ip_address=None,
        user_agent=None,
        metadata_json={},
        scope_summary_json={},
        chain_scope=None,
        chain_sequence=None,
        previous_integrity_hash=None,
        integrity_hash="",
    )
    key = get_settings().audit_hmac_key.get_secret_value()
    event.integrity_hash = calculate_integrity_hash(event, key)
    assert verify_audit_event(event)


def test_unscoped_means_current_authorized_set_and_requires_a_grant(client, seeded) -> None:
    auth = login_and_change_password(client)
    with SessionLocal.begin() as db:
        for grant in db.scalars(
            select(DataScopeGrant).where(
                DataScopeGrant.user_id == seeded["users"]["executive@example.com"]
            )
        ).all():
            db.delete(grant)
    denied = client.post(
        "/api/v1/conversations",
        headers={"X-CSRF-Token": auth["csrf_token"]},
        json={"title": "无范围会话"},
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "data_scope_forbidden"


def test_disconnected_and_disabled_units_are_rejected_even_when_granted(client, seeded) -> None:
    auth = login_and_change_password(client)
    with SessionLocal.begin() as db:
        west = db.get(OrganizationUnit, seeded["west_id"])
        west.enabled_for_analysis = False
        for unit_id in (seeded["west_id"], seeded["pending_id"]):
            db.add(
                DataScopeGrant(
                    user_id=seeded["users"]["executive@example.com"],
                    scope_kind="organization_unit",
                    organization_unit_id=unit_id,
                )
            )
    for unit_id in (seeded["west_id"], seeded["pending_id"]):
        denied = client.post(
            "/api/v1/conversations",
            headers={"X-CSRF-Token": auth["csrf_token"]},
            json={"title": "不可分析事业部", "organization_unit_id": str(unit_id)},
        )
        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "data_scope_forbidden"


def test_internal_job_type_is_not_client_callable_and_scope_is_snapshotted(client, seeded) -> None:
    auth = login_and_change_password(client)
    headers = {"X-CSRF-Token": auth["csrf_token"]}
    forbidden = client.post(
        "/api/v1/jobs",
        headers=headers,
        json={"job_type": "assistant_response", "payload": {}},
    )
    assert forbidden.status_code == 403
    with SessionLocal.begin() as db:
        report = Report(
            enterprise_id=seeded["enterprise_id"],
            organization_unit_id=seeded["east_id"],
            created_by_user_id=seeded["users"]["executive@example.com"],
            kind="daily",
            title="范围快照测试",
            status="published",
            period_start=date(2026, 7, 26),
            period_end=date(2026, 7, 26),
        )
        db.add(report)
        db.flush()
        db.add(
            ReportVersion(
                report_id=report.id,
                version=1,
                content_json={},
                created_by_user_id=seeded["users"]["executive@example.com"],
            )
        )
        report_id = report.id
    queued = client.post(
        "/api/v1/jobs",
        headers=headers,
        json={"job_type": "report.generate", "payload": {"report_id": str(report_id)}},
    )
    assert queued.status_code == 202, queued.text
    assert queued.json()["scope_snapshot_json"] == {
        "enterprise_wide": False,
        "organization_unit_ids": [str(seeded["east_id"])],
    }


def test_executive_reports_are_read_only(client, seeded) -> None:
    auth = login_and_change_password(client)
    headers = {"X-CSRF-Token": auth["csrf_token"]}
    create = client.post(
        "/api/v1/reports",
        headers=headers,
        json={
            "kind": "daily",
            "title": "不能由高层创建",
            "period_start": "2026-07-26",
            "period_end": "2026-07-26",
        },
    )
    assert create.status_code == 405
    publish = client.post(
        f"/api/v1/reports/{seeded['east_id']}/publish",
        headers=headers,
    )
    assert publish.status_code == 404


def test_job_read_is_denied_after_scope_revocation(client, seeded) -> None:
    login_and_change_password(client)
    with SessionLocal.begin() as db:
        job = Job(
            enterprise_id=seeded["enterprise_id"],
            created_by_user_id=seeded["users"]["executive@example.com"],
            job_type="report.generate",
            scope_snapshot_json={
                "enterprise_wide": False,
                "organization_unit_ids": [str(seeded["east_id"])],
            },
        )
        db.add(job)
        db.flush()
        job_id = job.id
    assert client.get(f"/api/v1/jobs/{job_id}").status_code == 200
    with SessionLocal.begin() as db:
        for grant in db.scalars(
            select(DataScopeGrant).where(
                DataScopeGrant.user_id == seeded["users"]["executive@example.com"]
            )
        ).all():
            db.delete(grant)
    denied = client.get(f"/api/v1/jobs/{job_id}")
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "data_scope_forbidden"
    assert client.get("/api/v1/jobs").json()["items"] == []


def test_cancel_running_job_closes_attempt_and_placeholder(client, seeded) -> None:
    auth = login_and_change_password(client)
    with SessionLocal.begin() as db:
        conversation = Conversation(
            enterprise_id=seeded["enterprise_id"],
            owner_user_id=seeded["users"]["executive@example.com"],
            organization_unit_id=seeded["east_id"],
            title="取消任务",
        )
        db.add(conversation)
        db.flush()
        placeholder = Message(
            conversation_id=conversation.id,
            role="assistant",
            content="",
            sequence=1,
            status="queued",
        )
        db.add(placeholder)
        db.flush()
        job = Job(
            enterprise_id=seeded["enterprise_id"],
            created_by_user_id=seeded["users"]["executive@example.com"],
            job_type="assistant_response",
            status="running",
            payload_json={"assistant_message_id": str(placeholder.id)},
            scope_snapshot_json={
                "enterprise_wide": False,
                "organization_unit_ids": [str(seeded["east_id"])],
            },
        )
        db.add(job)
        db.flush()
        attempt = JobAttempt(
            job_id=job.id,
            attempt=1,
            worker_id="test-worker",
            status="running",
            started_at=job.created_at,
        )
        db.add(attempt)
        job_id = job.id
        placeholder_id = placeholder.id
    canceled = client.post(
        f"/api/v1/jobs/{job_id}/cancel",
        headers={"X-CSRF-Token": auth["csrf_token"]},
    )
    assert canceled.status_code == 200, canceled.text
    assert canceled.json()["status"] == "canceled"
    with SessionLocal() as db:
        attempt = db.scalar(select(JobAttempt).where(JobAttempt.job_id == job_id))
        placeholder = db.get(Message, placeholder_id)
        assert attempt.status == "canceled"
        assert attempt.completed_at is not None
        assert placeholder.status == "failed"
        assert placeholder.content == "请求已取消"


def test_cursor_pagination_does_not_skip_conversations_or_projects(client, seeded) -> None:
    login_and_change_password(client)
    owner_id = seeded["users"]["executive@example.com"]
    with SessionLocal.begin() as db:
        for index in range(3):
            db.add(
                Conversation(
                    enterprise_id=seeded["enterprise_id"],
                    owner_user_id=owner_id,
                    organization_unit_id=seeded["east_id"],
                    title=f"会话 {index}",
                )
            )
            db.add(
                Project(
                    enterprise_id=seeded["enterprise_id"],
                    owner_user_id=owner_id,
                    organization_unit_id=seeded["east_id"],
                    name=f"项目 {index}",
                )
            )

    def collect(path: str) -> list[str]:
        found: list[str] = []
        cursor = None
        while True:
            response = client.get(
                path, params={"limit": 1, **({"cursor": cursor} if cursor else {})}
            )
            assert response.status_code == 200
            body = response.json()
            found.extend(item["id"] for item in body["items"])
            cursor = body["next_cursor"]
            if not cursor:
                return found

    conversation_ids = collect("/api/v1/conversations")
    project_ids = collect("/api/v1/projects")
    assert len(conversation_ids) == len(set(conversation_ids)) == 3
    assert len(project_ids) == len(set(project_ids)) == 3
