from __future__ import annotations

import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

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

import pytest
from executive_ai_api.database import Base
from executive_ai_api.models import (
    DataScopeGrant,
    Enterprise,
    Job,
    JobAttempt,
    OrganizationUnit,
    User,
)
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker

from executive_ai_worker import main as worker


@pytest.mark.postgres
def test_postgres_concurrent_claim_has_one_lease_and_one_attempt(monkeypatch) -> None:
    database_url = os.getenv("POSTGRES_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("POSTGRES_TEST_DATABASE_URL is required")
    schema = f"worker_lease_{uuid.uuid4().hex}"
    admin_engine = create_engine(database_url, pool_pre_ping=True)
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    test_engine = create_engine(
        database_url,
        pool_pre_ping=True,
        connect_args={"options": f"-csearch_path={schema}"},
    )
    session_factory = sessionmaker(bind=test_engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(worker, "SessionLocal", session_factory)
    try:
        Base.metadata.create_all(test_engine)
        with session_factory.begin() as db:
            enterprise = Enterprise(name="Worker PG", slug=f"worker-{uuid.uuid4().hex}")
            db.add(enterprise)
            db.flush()
            unit = OrganizationUnit(
                enterprise_id=enterprise.id,
                name="PG 事业部",
                code="pg-unit",
                enabled_for_analysis=True,
                data_connected=True,
            )
            user = User(
                enterprise_id=enterprise.id,
                email="worker-pg@example.com",
                display_name="Worker PG",
                role="executive",
                password_change_required=False,
            )
            db.add_all([unit, user])
            db.flush()
            db.add(DataScopeGrant(user_id=user.id, scope_kind="enterprise"))
            job = Job(
                enterprise_id=enterprise.id,
                created_by_user_id=user.id,
                job_type="system.noop",
                status="queued",
                scope_snapshot_json={
                    "enterprise_wide": True,
                    "organization_unit_ids": [str(unit.id)],
                },
            )
            db.add(job)
            db.flush()
            job_id = job.id

        barrier = threading.Barrier(2)

        def compete(_):
            barrier.wait(timeout=5)
            return worker.claim_one()

        with ThreadPoolExecutor(max_workers=2) as executor:
            claims = list(executor.map(compete, range(2)))
        winners = [claim for claim in claims if claim is not None]
        assert len(winners) == 1
        with session_factory() as db:
            job = db.get(Job, job_id)
            assert job.status == "running"
            assert job.attempt_count == 1
            assert job.lease_token == winners[0].lease_token
            assert db.scalar(select(func.count(JobAttempt.id))) == 1
            attempt = db.scalar(select(JobAttempt))
            assert attempt.status == "running"
            assert attempt.lease_token == job.lease_token
    finally:
        test_engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin_engine.dispose()
