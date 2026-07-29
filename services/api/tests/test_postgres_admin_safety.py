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
from executive_ai_api.errors import AppError
from executive_ai_api.models import Enterprise, User, UserSession
from executive_ai_api.routers import admin as admin_router
from executive_ai_api.routers.admin import update_user
from executive_ai_api.schemas import UserUpdate
from executive_ai_api.security import utc_now


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "PATCH",
            "scheme": "http",
            "path": "/api/v1/admin/users/concurrent",
            "raw_path": b"/api/v1/admin/users/concurrent",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
            "state": {"request_id": uuid.uuid4().hex},
        }
    )


@pytest.mark.postgres
@pytest.mark.parametrize("change", [{"role": "executive"}, {"is_active": False}])
def test_postgres_concurrent_admin_removals_leave_one_active_admin(monkeypatch, change) -> None:
    database_url = os.getenv("POSTGRES_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("POSTGRES_TEST_DATABASE_URL is required for the PostgreSQL concurrency test")

    schema = f"admin_safety_{uuid.uuid4().hex}"
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
            enterprise = Enterprise(name="Admin Concurrency", slug=f"test-{uuid.uuid4().hex}")
            db.add(enterprise)
            db.flush()
            now = utc_now()
            identities: list[tuple[uuid.UUID, uuid.UUID]] = []
            for number in range(2):
                user = User(
                    enterprise_id=enterprise.id,
                    email=f"admin-{number}@example.com",
                    display_name=f"Admin {number}",
                    role="enterprise_admin",
                    password_change_required=False,
                )
                db.add(user)
                db.flush()
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
                identities.append((user.id, user_session.id))

        barrier = threading.Barrier(2)
        first_mutation_reached = threading.Event()
        release_first_mutation = threading.Event()
        gate_lock = threading.Lock()
        first_mutation_selected = False
        original_record_audit = admin_router.record_audit

        def gated_record_audit(*args, **kwargs):
            nonlocal first_mutation_selected
            should_wait = False
            with gate_lock:
                if not first_mutation_selected:
                    first_mutation_selected = True
                    should_wait = True
            if should_wait:
                first_mutation_reached.set()
                assert release_first_mutation.wait(timeout=5)
            return original_record_audit(*args, **kwargs)

        monkeypatch.setattr(admin_router, "record_audit", gated_record_audit)

        def demote(actor_index: int) -> str:
            target_index = 1 - actor_index
            with session_factory() as db:
                actor = db.get(User, identities[actor_index][0])
                actor_session = db.get(UserSession, identities[actor_index][1])
                assert actor is not None and actor_session is not None
                principal = Principal(user=actor, session=actor_session)
                barrier.wait(timeout=5)
                try:
                    update_user(
                        identities[target_index][0],
                        UserUpdate(**change),
                        _request(),
                        principal,
                        db,
                    )
                except AppError as exc:
                    db.rollback()
                    return exc.code
                return "updated"

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(demote, index) for index in range(2)]
            assert first_mutation_reached.wait(timeout=5)
            # The first transaction is paused after the safety check and before
            # commit. A correct enterprise row lock keeps the competitor inside
            # SELECT .. FOR UPDATE. Without the lock, it can also pass the stale
            # count and complete its demotion while the first transaction waits.
            time.sleep(0.25)
            competitor_was_serialized = not any(future.done() for future in futures)
            release_first_mutation.set()
            outcomes = [future.result(timeout=5) for future in futures]

        assert competitor_was_serialized
        assert sorted(outcomes) == ["last_admin_required", "updated"]
        with session_factory() as db:
            remaining = db.scalar(
                select(func.count())
                .select_from(User)
                .where(User.role == "enterprise_admin", User.is_active.is_(True))
            )
            assert remaining == 1
    finally:
        test_engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin_engine.dispose()
