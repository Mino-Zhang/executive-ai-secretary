from __future__ import annotations

import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from fastapi import Request
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker

from executive_ai_api.authz import Principal
from executive_ai_api.database import Base
from executive_ai_api.idempotency import replay, save_response
from executive_ai_api.models import (
    Enterprise,
    IdempotencyRecord,
    Project,
    User,
    UserSession,
)
from executive_ai_api.security import utc_now


def _request(key: str) -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/v1/projects",
            "raw_path": b"/api/v1/projects",
            "query_string": b"",
            "headers": [(b"idempotency-key", key.encode())],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
        }
    )


@pytest.mark.postgres
def test_postgres_competing_requests_execute_business_mutation_once() -> None:
    database_url = os.getenv("POSTGRES_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("POSTGRES_TEST_DATABASE_URL is required for the PostgreSQL concurrency test")

    schema = f"idempotency_{uuid.uuid4().hex}"
    admin_engine = create_engine(database_url, pool_pre_ping=True)
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    test_engine = create_engine(
        database_url,
        pool_pre_ping=True,
        connect_args={"options": f"-csearch_path={schema}"},
    )
    session_factory = sessionmaker(bind=test_engine, autoflush=False, expire_on_commit=False)
    try:
        Base.metadata.create_all(test_engine)
        with session_factory.begin() as db:
            enterprise = Enterprise(name="Concurrency Test", slug=f"test-{uuid.uuid4().hex}")
            db.add(enterprise)
            db.flush()
            user = User(
                enterprise_id=enterprise.id,
                email="concurrency@example.com",
                display_name="Concurrency",
                role="executive",
                password_change_required=False,
            )
            db.add(user)
            db.flush()
            now = utc_now()
            user_session = UserSession(
                user_id=user.id,
                token_hash=uuid.uuid4().hex + uuid.uuid4().hex,
                csrf_hash=uuid.uuid4().hex + uuid.uuid4().hex,
                expires_at=now + timedelta(hours=1),
                idle_expires_at=now + timedelta(hours=1),
                last_seen_at=now,
            )
            db.add(user_session)
            db.flush()
            enterprise_id = enterprise.id
            user_id = user.id
            session_id = user_session.id

        barrier = threading.Barrier(2)
        execution_count = 0
        execution_lock = threading.Lock()
        payload = {"name": "Only once", "organization_unit_id": None}

        def execute_request() -> dict[str, str]:
            nonlocal execution_count
            with session_factory() as db:
                user = db.get(User, user_id)
                user_session = db.get(UserSession, session_id)
                assert user is not None and user_session is not None
                principal = Principal(user=user, session=user_session)
                request = _request("same-key-at-the-same-time")
                barrier.wait(timeout=5)
                previous = replay(db, request, principal, payload)
                if previous is not None:
                    db.rollback()
                    return previous[1]
                with execution_lock:
                    execution_count += 1
                # Hold the reservation uncommitted long enough for the competitor
                # to collide with the unique index and wait for the replay result.
                time.sleep(0.25)
                project = Project(
                    enterprise_id=enterprise_id,
                    owner_user_id=user_id,
                    name=payload["name"],
                )
                db.add(project)
                db.flush()
                response = {"id": str(project.id), "name": project.name}
                save_response(db, request, principal, payload, 201, response)
                db.commit()
                return response

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: execute_request(), range(2)))

        assert execution_count == 1
        assert results[0] == results[1]
        with session_factory() as db:
            assert db.scalar(select(func.count(Project.id))) == 1
            record = db.scalar(select(IdempotencyRecord))
            assert record is not None
            assert record.response_status == 201
            assert record.response_json == results[0]
    finally:
        test_engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin_engine.dispose()
