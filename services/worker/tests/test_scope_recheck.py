from __future__ import annotations

import os
import uuid
from datetime import timedelta

os.environ.update(
    {
        "APP_ENV": "test",
        "APP_MODE": "demo",
        "DATABASE_URL": "sqlite+pysqlite:///:memory:",
        "SESSION_SECRET": "worker-test-session-secret-at-least-32-chars",
        "CSRF_SECRET": "worker-test-csrf-secret-at-least-32-chars",
        "AUDIT_HMAC_KEY": "worker-test-audit-key-at-least-32-characters",
    }
)

from executive_ai_api.database import Base, SessionLocal, engine
from executive_ai_api.models import (
    Conversation,
    DataScopeGrant,
    Enterprise,
    Job,
    JobAttempt,
    Message,
    OrganizationUnit,
    User,
)
from executive_ai_api.security import utc_now
from sqlalchemy import delete, select

from executive_ai_worker.main import authorization_is_current, process, worker_id


def test_worker_rechecks_scope_snapshot_before_processing() -> None:
    Base.metadata.create_all(engine)
    try:
        with SessionLocal.begin() as db:
            enterprise = Enterprise(name="测试企业", slug="worker-test")
            db.add(enterprise)
            db.flush()
            unit = OrganizationUnit(
                enterprise_id=enterprise.id,
                name="华东事业部",
                code="east",
                enabled_for_analysis=True,
                data_connected=True,
            )
            user = User(
                enterprise_id=enterprise.id,
                email="executive@example.com",
                display_name="Executive",
                role="executive",
                password_change_required=False,
            )
            db.add_all([unit, user])
            db.flush()
            db.add(
                DataScopeGrant(
                    user_id=user.id,
                    scope_kind="organization_unit",
                    organization_unit_id=unit.id,
                )
            )
            job = Job(
                enterprise_id=enterprise.id,
                created_by_user_id=user.id,
                job_type="report.generate",
                payload_json={"report_id": "synthetic"},
                scope_snapshot_json={
                    "enterprise_wide": False,
                    "organization_unit_ids": [str(unit.id)],
                },
            )
            db.add(job)
            db.flush()
            job_id = job.id
            unit_id = unit.id
        with SessionLocal() as db:
            assert authorization_is_current(db, db.get(Job, job_id)) is True
        with SessionLocal.begin() as db:
            db.get(OrganizationUnit, unit_id).data_connected = False
        with SessionLocal() as db:
            assert authorization_is_current(db, db.get(Job, job_id)) is False
        with SessionLocal.begin() as db:
            db.get(OrganizationUnit, unit_id).data_connected = True
        with SessionLocal.begin() as db:
            db.execute(delete(DataScopeGrant))
        with SessionLocal() as db:
            assert authorization_is_current(db, db.get(Job, job_id)) is False
    finally:
        Base.metadata.drop_all(engine)


def test_worker_closes_assistant_placeholder_when_handler_is_not_configured() -> None:
    Base.metadata.create_all(engine)
    try:
        with SessionLocal.begin() as db:
            enterprise = Enterprise(name="测试企业", slug="worker-placeholder-test")
            db.add(enterprise)
            db.flush()
            unit = OrganizationUnit(
                enterprise_id=enterprise.id,
                name="华东事业部",
                code="east",
                enabled_for_analysis=True,
                data_connected=True,
            )
            user = User(
                enterprise_id=enterprise.id,
                email="executive@example.com",
                display_name="Executive",
                role="executive",
                password_change_required=False,
            )
            db.add_all([unit, user])
            db.flush()
            db.add(
                DataScopeGrant(
                    user_id=user.id,
                    scope_kind="organization_unit",
                    organization_unit_id=unit.id,
                )
            )
            conversation = Conversation(
                enterprise_id=enterprise.id,
                owner_user_id=user.id,
                organization_unit_id=unit.id,
                title="异步回答",
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
            now = utc_now()
            lease_token = uuid.uuid4().hex
            job = Job(
                enterprise_id=enterprise.id,
                created_by_user_id=user.id,
                job_type="assistant_response",
                status="running",
                attempt_count=1,
                lease_owner=worker_id,
                lease_token=lease_token,
                lease_expires_at=now + timedelta(minutes=1),
                heartbeat_at=now,
                payload_json={"assistant_message_id": str(placeholder.id)},
                scope_snapshot_json={
                    "enterprise_wide": False,
                    "organization_unit_ids": [str(unit.id)],
                },
            )
            db.add(job)
            db.flush()
            db.add(
                JobAttempt(
                    job_id=job.id,
                    attempt=1,
                    worker_id=worker_id,
                    lease_token=lease_token,
                    status="running",
                    started_at=now,
                    heartbeat_at=now,
                    lease_expires_at=now + timedelta(minutes=1),
                )
            )
            job_id = job.id
            placeholder_id = placeholder.id

        process(str(job_id), lease_token)

        with SessionLocal() as db:
            job = db.get(Job, job_id)
            placeholder = db.get(Message, placeholder_id)
            attempt = db.scalar(select(JobAttempt).where(JobAttempt.job_id == job_id))
            assert job.status == "failed"
            assert job.error_code == "integration_not_configured"
            assert placeholder.status == "failed"
            assert placeholder.content == "未配置真实处理器"
            assert attempt.status == "failed"
            assert attempt.completed_at is not None
    finally:
        Base.metadata.drop_all(engine)


def test_system_noop_completes_linked_assistant_placeholder_without_fake_content() -> None:
    Base.metadata.create_all(engine)
    try:
        with SessionLocal.begin() as db:
            enterprise = Enterprise(name="测试企业", slug="worker-noop-test")
            db.add(enterprise)
            db.flush()
            unit = OrganizationUnit(
                enterprise_id=enterprise.id,
                name="华东事业部",
                code="east",
                enabled_for_analysis=True,
                data_connected=True,
            )
            user = User(
                enterprise_id=enterprise.id,
                email="executive@example.com",
                display_name="Executive",
                role="executive",
                password_change_required=False,
            )
            db.add_all([unit, user])
            db.flush()
            db.add(
                DataScopeGrant(
                    user_id=user.id,
                    scope_kind="organization_unit",
                    organization_unit_id=unit.id,
                )
            )
            conversation = Conversation(
                enterprise_id=enterprise.id,
                owner_user_id=user.id,
                organization_unit_id=unit.id,
                title="无操作任务",
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
            now = utc_now()
            lease_token = uuid.uuid4().hex
            job = Job(
                enterprise_id=enterprise.id,
                created_by_user_id=user.id,
                job_type="system.noop",
                status="running",
                attempt_count=1,
                lease_owner=worker_id,
                lease_token=lease_token,
                lease_expires_at=now + timedelta(minutes=1),
                heartbeat_at=now,
                payload_json={"assistant_message_id": str(placeholder.id)},
                scope_snapshot_json={
                    "enterprise_wide": False,
                    "organization_unit_ids": [str(unit.id)],
                },
            )
            db.add(job)
            db.flush()
            db.add(
                JobAttempt(
                    job_id=job.id,
                    attempt=1,
                    worker_id=worker_id,
                    lease_token=lease_token,
                    status="running",
                    started_at=now,
                    heartbeat_at=now,
                    lease_expires_at=now + timedelta(minutes=1),
                )
            )
            job_id = job.id
            placeholder_id = placeholder.id

        process(str(job_id), lease_token)

        with SessionLocal() as db:
            job = db.get(Job, job_id)
            placeholder = db.get(Message, placeholder_id)
            assert job.status == "completed"
            assert placeholder.status == "completed"
            assert placeholder.content == ""
    finally:
        Base.metadata.drop_all(engine)
