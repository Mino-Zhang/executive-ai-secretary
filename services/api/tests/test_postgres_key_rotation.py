from __future__ import annotations

import io
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from executive_ai_api.database import Base
from executive_ai_api.file_key_rotation import ROTATION_ADVISORY_LOCK, rotate_file_keys
from executive_ai_api.models import Enterprise, FileAsset, User
from executive_ai_api.storage import LocalEncryptedStorage


@pytest.mark.postgres
def test_postgres_rotation_lock_survives_file_commit_and_is_released(tmp_path) -> None:
    database_url = os.getenv("POSTGRES_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("POSTGRES_TEST_DATABASE_URL is required for the PostgreSQL rotation test")

    schema = f"key_rotation_{uuid.uuid4().hex}"
    admin_engine = create_engine(database_url, pool_pre_ping=True)
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    test_engine = create_engine(
        database_url,
        pool_pre_ping=True,
        poolclass=NullPool,
        connect_args={"options": f"-csearch_path={schema}"},
    )
    session_factory = sessionmaker(bind=test_engine, autoflush=False, expire_on_commit=False)
    storage = LocalEncryptedStorage(
        tmp_path / "objects",
        current_key_version="v2",
        key_ring={"v1": b"O" * 32, "v2": b"N" * 32},
    )

    try:
        Base.metadata.create_all(test_engine)
        with session_factory.begin() as db:
            enterprise = Enterprise(name="Key Rotation", slug=f"rotation-{uuid.uuid4().hex}")
            db.add(enterprise)
            db.flush()
            user = User(
                enterprise_id=enterprise.id,
                email=f"rotation-{uuid.uuid4().hex}@example.com",
                display_name="Key Rotation",
                role="enterprise_admin",
                password_change_required=False,
            )
            db.add(user)
            db.flush()
            for number in range(2):
                stored = storage.put(io.BytesIO(f"file-{number}".encode()), 1024)
                # Objects are written with the current version. Set the database
                # to v1 to model a resumable rotation whose first atomic rewrite
                # already happened before the database commit.
                db.add(
                    FileAsset(
                        enterprise_id=enterprise.id,
                        uploaded_by_user_id=user.id,
                        storage_key=stored.storage_key,
                        original_name=f"file-{number}.txt",
                        media_type="text/plain",
                        size_bytes=stored.size_bytes,
                        sha256=stored.sha256,
                        encryption_key_version="v1",
                        status="ready",
                    )
                )

        first_commit_completed = threading.Event()
        release_first_rotation = threading.Event()
        call_lock = threading.Lock()
        reencrypt_calls = 0
        original_reencrypt = storage.reencrypt_atomic

        def gated_reencrypt(*args, **kwargs):
            nonlocal reencrypt_calls
            with call_lock:
                reencrypt_calls += 1
                call_number = reencrypt_calls
            if call_number == 2:
                # rotate_file_keys commits after every object, so reaching the
                # second object proves that the first commit has completed.
                first_commit_completed.set()
                assert release_first_rotation.wait(timeout=10)
            return original_reencrypt(*args, **kwargs)

        storage.reencrypt_atomic = gated_reencrypt  # type: ignore[method-assign]

        def run_first_rotation():
            with session_factory() as db:
                return rotate_file_keys(
                    db,
                    storage,
                    source_key_version="v1",
                    target_key_version="v2",
                    backup_reference="test:verified-backup",
                    batch_size=2,
                )

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(run_first_rotation)
            try:
                assert first_commit_completed.wait(timeout=10)
                with session_factory() as db:
                    committed = db.scalar(
                        select(func.count(FileAsset.id)).where(
                            FileAsset.encryption_key_version == "v2"
                        )
                    )
                    assert committed == 1

                # The first per-file commit must not return the advisory-lock
                # connection to the pool. A competing rotation must still fail.
                with session_factory() as competitor_db:
                    with pytest.raises(
                        RuntimeError, match="another file-key rotation is already running"
                    ):
                        rotate_file_keys(
                            competitor_db,
                            storage,
                            source_key_version="v1",
                            target_key_version="v2",
                            backup_reference="dry-run",
                            dry_run=True,
                        )
            finally:
                release_first_rotation.set()

            summary = future.result(timeout=10)

        assert summary.inspected == 2
        assert summary.reconciled == 2
        assert summary.remaining == 0

        # NullPool guarantees a fresh PostgreSQL backend for this probe. It can
        # acquire the lock only if the rotation released (or closed) its owner.
        with test_engine.connect() as probe:
            assert probe.scalar(
                text("SELECT pg_try_advisory_lock(:lock_id)"),
                {"lock_id": ROTATION_ADVISORY_LOCK},
            )
            assert probe.scalar(
                text("SELECT pg_advisory_unlock(:lock_id)"),
                {"lock_id": ROTATION_ADVISORY_LOCK},
            )
            probe.commit()
    finally:
        test_engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin_engine.dispose()
