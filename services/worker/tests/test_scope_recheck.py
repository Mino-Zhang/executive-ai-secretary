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
    MessageEvidence,
    MessageRun,
    OrganizationUnit,
    User,
)
from executive_ai_api.security import utc_now
from sqlalchemy import delete, func, select

from executive_ai_worker.assistant_orchestrator import _save_answer_with_evidence
from executive_ai_worker.main import authorization_is_current, process, worker_id


def test_answer_and_evidence_save_is_atomic_and_idempotent() -> None:
    Base.metadata.create_all(engine)
    try:
        with SessionLocal.begin() as db:
            enterprise = Enterprise(name="证据测试企业", slug="evidence-transaction-test")
            db.add(enterprise)
            db.flush()
            user = User(
                enterprise_id=enterprise.id,
                email="evidence@example.com",
                display_name="Evidence User",
                role="executive",
                password_change_required=False,
            )
            db.add(user)
            db.flush()
            conversation = Conversation(
                enterprise_id=enterprise.id,
                owner_user_id=user.id,
                title="证据事务",
            )
            db.add(conversation)
            db.flush()
            assistant = Message(
                conversation_id=conversation.id,
                role="assistant",
                content="",
                sequence=1,
                status="queued",
            )
            db.add(assistant)
            db.flush()
            assistant_id = assistant.id

        tool_result = {
            "data": {"collected_amount": 100.0},
            "scope": {"organization_unit_ids": []},
            "evidence": [{"record_id": "COL-001"}],
            "freshness": [
                {
                    "domain": "collection",
                    "source_type": "simulated_generator",
                    "source_display_name": "演示模拟数据",
                    "source_data_as_of": utc_now().isoformat(),
                    "dataset_version": "test-v1",
                }
            ],
        }
        for _ in range(2):
            count = _save_answer_with_evidence(
                assistant_message_id=assistant_id,
                content="回款已核对。",
                response={
                    "provider": "anspire",
                    "model": "glm-5.2",
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                },
                content_json={"freshness": tool_result["freshness"]},
                tool_results=[
                    {
                        "tool": "get_collection_aging",
                        "arguments": {"organization_unit_ids": []},
                        "result": tool_result,
                    }
                ],
            )
            assert count == 1

        with SessionLocal() as db:
            assistant = db.get(Message, assistant_id)
            evidence_count = db.scalar(
                select(func.count())
                .select_from(MessageEvidence)
                .where(MessageEvidence.message_id == assistant_id)
            )
            run_count = db.scalar(
                select(func.count())
                .select_from(MessageRun)
                .where(MessageRun.message_id == assistant_id)
            )
            assert assistant.status == "completed"
            assert assistant.content_json["evidence_count"] == 1
            assert evidence_count == 1
            assert run_count == 1
    finally:
        Base.metadata.drop_all(engine)


def test_canceled_job_cannot_write_a_late_answer() -> None:
    Base.metadata.create_all(engine)
    try:
        with SessionLocal.begin() as db:
            enterprise = Enterprise(name="取消围栏企业", slug="canceled-write-fence")
            db.add(enterprise)
            db.flush()
            user = User(
                enterprise_id=enterprise.id,
                email="fence@example.com",
                display_name="Fence User",
                role="executive",
                password_change_required=False,
            )
            db.add(user)
            db.flush()
            conversation = Conversation(
                enterprise_id=enterprise.id,
                owner_user_id=user.id,
                title="取消围栏",
            )
            db.add(conversation)
            db.flush()
            assistant = Message(
                conversation_id=conversation.id,
                role="assistant",
                content="请求已取消",
                sequence=1,
                status="failed",
            )
            db.add(assistant)
            db.flush()
            job = Job(
                enterprise_id=enterprise.id,
                created_by_user_id=user.id,
                job_type="assistant_response",
                status="canceled",
                lease_token=None,
                scope_snapshot_json={
                    "enterprise_wide": False,
                    "organization_unit_ids": [],
                    "general_only": True,
                },
            )
            db.add(job)
            db.flush()
            job_id = job.id
            assistant_id = assistant.id

        stale_lease_token = uuid.uuid4().hex
        try:
            _save_answer_with_evidence(
                job_id=job_id,
                lease_token=stale_lease_token,
                assistant_message_id=assistant_id,
                content="这条迟到答案不应保存",
                response={"provider": "anspire", "model": "glm-5.2", "usage": {}},
                content_json={},
                tool_results=[],
            )
        except RuntimeError as exc:
            assert "write lease" in str(exc)
        else:  # pragma: no cover - cancellation must fence every late writer.
            raise AssertionError("canceled job overwrote its placeholder")
        with SessionLocal() as db:
            assistant = db.get(Message, assistant_id)
            assert assistant is not None
            assert assistant.content == "请求已取消"
            assert assistant.status == "failed"
    finally:
        Base.metadata.drop_all(engine)


def test_general_only_snapshot_remains_authorized_without_data_grants() -> None:
    Base.metadata.create_all(engine)
    try:
        with SessionLocal.begin() as db:
            enterprise = Enterprise(name="泛化问答企业", slug="general-only-worker")
            db.add(enterprise)
            db.flush()
            user = User(
                enterprise_id=enterprise.id,
                email="general@example.com",
                display_name="General User",
                role="executive",
                password_change_required=False,
            )
            db.add(user)
            db.flush()
            job = Job(
                enterprise_id=enterprise.id,
                created_by_user_id=user.id,
                job_type="assistant_response",
                scope_snapshot_json={
                    "enterprise_wide": False,
                    "organization_unit_ids": [],
                    "general_only": True,
                },
            )
            db.add(job)
            db.flush()
            job_id = job.id
        with SessionLocal() as db:
            assert authorization_is_current(db, db.get(Job, job_id)) is True
    finally:
        Base.metadata.drop_all(engine)


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


def test_worker_closes_assistant_placeholder_when_job_identifiers_are_invalid() -> None:
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
            assert job.error_code == "invalid_assistant_job"
            assert placeholder.status == "failed"
            assert placeholder.content == "请求无法处理"
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
