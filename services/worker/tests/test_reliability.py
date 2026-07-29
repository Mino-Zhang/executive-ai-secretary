from __future__ import annotations

import os
import time
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
from sqlalchemy import select

from executive_ai_worker import main as worker


def _seed_job(*, status: str = "queued", attempt_count: int = 0, max_attempts: int = 3):
    with SessionLocal.begin() as db:
        enterprise = Enterprise(name="可靠性测试企业", slug="reliability-test")
        db.add(enterprise)
        db.flush()
        unit = OrganizationUnit(
            enterprise_id=enterprise.id,
            name="经营事业部",
            code="operations",
            enabled_for_analysis=True,
            data_connected=True,
        )
        user = User(
            enterprise_id=enterprise.id,
            email="worker@example.com",
            display_name="Worker Test",
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
            title="Worker reliability",
        )
        db.add(conversation)
        db.flush()
        placeholder = Message(
            conversation_id=conversation.id,
            role="assistant",
            content="",
            sequence=1,
            status="running" if status == "running" else "queued",
        )
        db.add(placeholder)
        db.flush()
        now = utc_now()
        lease_token = "expired-lease-token" if status == "running" else None
        job = Job(
            enterprise_id=enterprise.id,
            created_by_user_id=user.id,
            job_type="system.noop",
            status=status,
            attempt_count=attempt_count,
            max_attempts=max_attempts,
            payload_json={"assistant_message_id": str(placeholder.id)},
            scope_snapshot_json={
                "enterprise_wide": False,
                "organization_unit_ids": [str(unit.id)],
            },
            lease_owner=worker.worker_id if status == "running" else None,
            lease_token=lease_token,
            heartbeat_at=now - timedelta(minutes=2) if status == "running" else None,
            lease_expires_at=now - timedelta(minutes=1) if status == "running" else None,
        )
        db.add(job)
        db.flush()
        if status == "running":
            db.add(
                JobAttempt(
                    job_id=job.id,
                    attempt=attempt_count,
                    worker_id=worker.worker_id,
                    lease_token=lease_token,
                    status="running",
                    started_at=now - timedelta(minutes=2),
                    heartbeat_at=now - timedelta(minutes=2),
                    lease_expires_at=now - timedelta(minutes=1),
                )
            )
        return job.id, placeholder.id


def test_claim_heartbeat_and_successful_completion() -> None:
    Base.metadata.create_all(engine)
    try:
        job_id, placeholder_id = _seed_job()
        claimed = worker.claim_one()
        assert claimed is not None
        assert claimed.job_id == str(job_id)
        with SessionLocal() as db:
            job = db.get(Job, job_id)
            assert job.status == "running"
            assert job.attempt_count == 1
            original_deadline = job.lease_expires_at
            attempt = db.scalar(select(JobAttempt).where(JobAttempt.job_id == job_id))
            assert attempt.status == "running"
            assert attempt.lease_token == claimed.lease_token
        assert worker.renew_lease(str(job_id), "wrong-token") is False
        assert worker.heartbeat(str(job_id), claimed.lease_token) is True
        with SessionLocal() as db:
            assert db.get(Job, job_id).lease_expires_at >= original_deadline
        assert worker.process(claimed.job_id, claimed.lease_token) is True
        with SessionLocal() as db:
            job = db.get(Job, job_id)
            placeholder = db.get(Message, placeholder_id)
            attempt = db.scalar(select(JobAttempt).where(JobAttempt.job_id == job_id))
            assert job.status == "completed"
            assert job.lease_token is None
            assert placeholder.status == "completed"
            assert attempt.status == "completed"
            assert attempt.completed_at is not None
    finally:
        Base.metadata.drop_all(engine)


def test_expired_lease_is_requeued_with_bounded_backoff() -> None:
    Base.metadata.create_all(engine)
    try:
        job_id, placeholder_id = _seed_job(status="running", attempt_count=1)
        assert worker.recover_expired_leases() == 1
        with SessionLocal() as db:
            job = db.get(Job, job_id)
            placeholder = db.get(Message, placeholder_id)
            attempt = db.scalar(select(JobAttempt).where(JobAttempt.job_id == job_id))
            assert job.status == "queued"
            assert job.scheduled_at is not None
            assert job.lease_token is None
            assert job.error_code == "lease_expired"
            assert placeholder.status == "queued"
            assert attempt.status == "lease_expired"
            assert attempt.completed_at is not None
        assert worker.retry_delay_seconds(1) == worker.settings.worker_retry_base_seconds
        assert worker.retry_delay_seconds(100) == worker.settings.worker_retry_max_seconds
    finally:
        Base.metadata.drop_all(engine)


def test_expired_final_attempt_dead_letters_and_closes_placeholder() -> None:
    Base.metadata.create_all(engine)
    try:
        job_id, placeholder_id = _seed_job(
            status="running",
            attempt_count=3,
            max_attempts=3,
        )
        assert worker.recover_expired_leases() == 1
        with SessionLocal() as db:
            job = db.get(Job, job_id)
            placeholder = db.get(Message, placeholder_id)
            attempt = db.scalar(select(JobAttempt).where(JobAttempt.job_id == job_id))
            assert job.status == "failed"
            assert job.dead_lettered_at is not None
            assert job.completed_at is not None
            assert job.lease_token is None
            assert placeholder.status == "failed"
            assert placeholder.content == worker.RETRY_EXHAUSTED_CONTENT
            assert attempt.status == "failed"
            assert attempt.completed_at is not None
    finally:
        Base.metadata.drop_all(engine)


def test_unexpected_process_exception_closes_attempt_and_retries(monkeypatch) -> None:
    Base.metadata.create_all(engine)
    try:
        job_id, placeholder_id = _seed_job()
        claimed = worker.claim_one()
        assert claimed is not None

        def explode(_: Job) -> dict:
            raise RuntimeError("synthetic handler failure")

        monkeypatch.setattr(worker, "execute_job_handler", explode)
        assert worker.process(claimed.job_id, claimed.lease_token) is True
        with SessionLocal() as db:
            job = db.get(Job, job_id)
            placeholder = db.get(Message, placeholder_id)
            attempt = db.scalar(select(JobAttempt).where(JobAttempt.job_id == job_id))
            assert job.status == "queued"
            assert job.error_code == "processing_error"
            assert job.lease_token is None
            assert placeholder.status == "queued"
            assert attempt.status == "failed"
            assert attempt.completed_at is not None
        assert worker.process(claimed.job_id, claimed.lease_token) is False
    finally:
        Base.metadata.drop_all(engine)


def test_long_handler_receives_periodic_heartbeat(monkeypatch) -> None:
    Base.metadata.create_all(engine)
    try:
        job_id, _ = _seed_job()
        claimed = worker.claim_one()
        assert claimed is not None

        def slow_handler(_: Job) -> dict:
            time.sleep(0.08)
            return {"ok": True}

        heartbeat_calls = 0
        real_renew = worker.renew_lease

        def tracked_renew(job_id: str, lease_token: str) -> bool:
            nonlocal heartbeat_calls
            heartbeat_calls += 1
            return real_renew(job_id, lease_token)

        monkeypatch.setattr(worker.settings, "worker_heartbeat_seconds", 0.02)
        monkeypatch.setattr(worker, "execute_job_handler", slow_handler)
        monkeypatch.setattr(worker, "renew_lease", tracked_renew)
        assert worker.process(claimed.job_id, claimed.lease_token) is True
        with SessionLocal() as db:
            attempt = db.scalar(select(JobAttempt).where(JobAttempt.job_id == job_id))
            assert attempt.status == "completed"
            assert heartbeat_calls >= 2
            assert attempt.heartbeat_at >= attempt.started_at
    finally:
        Base.metadata.drop_all(engine)
