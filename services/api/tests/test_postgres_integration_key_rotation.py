from __future__ import annotations

import base64
import json
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from executive_ai_api import integration_key_rotation
from executive_ai_api.anspire import ANSPIRE_ENDPOINT_URL, encrypt_anspire_api_key
from executive_ai_api.config import Settings
from executive_ai_api.database import Base
from executive_ai_api.integration_key_rotation import (
    INTEGRATION_ROTATION_ADVISORY_LOCK,
    rotate_integration_keys,
)
from executive_ai_api.models import Enterprise, ModelProviderConfig


def encoded_key(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode()


@pytest.mark.postgres
def test_postgres_integration_rotation_lock_survives_per_config_commit(
    monkeypatch,
) -> None:
    database_url = os.getenv("POSTGRES_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("POSTGRES_TEST_DATABASE_URL is required for the PostgreSQL rotation test")

    schema = f"integration_rotation_{uuid.uuid4().hex}"
    admin_engine = create_engine(database_url, pool_pre_ping=True)
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    test_engine = create_engine(
        database_url,
        pool_pre_ping=True,
        poolclass=NullPool,
        connect_args={"options": f"-csearch_path={schema},public"},
    )
    session_factory = sessionmaker(bind=test_engine, autoflush=False, expire_on_commit=False)
    old_settings = Settings(
        _env_file=None,
        app_env="test",
        app_mode="demo",
        integration_encryption_key=encoded_key(b"O" * 32),
        integration_encryption_key_version="v1",
    )
    rotation_settings = Settings(
        _env_file=None,
        app_env="test",
        app_mode="demo",
        integration_encryption_key=encoded_key(b"N" * 32),
        integration_encryption_key_version="v2",
        integration_encryption_key_ring=json.dumps({"v1": encoded_key(b"O" * 32)}),
    )

    try:
        Base.metadata.create_all(test_engine)
        with session_factory.begin() as db:
            for number in range(2):
                enterprise = Enterprise(
                    name=f"Integration Rotation {number}",
                    slug=f"integration-rotation-{uuid.uuid4().hex}",
                )
                db.add(enterprise)
                db.flush()
                encrypted = encrypt_anspire_api_key(
                    f"unit-test-postgres-integration-key-{number}-123456",
                    enterprise_id=enterprise.id,
                    settings=old_settings,
                )
                db.add(
                    ModelProviderConfig(
                        enterprise_id=enterprise.id,
                        provider="anspire",
                        endpoint_url=ANSPIRE_ENDPOINT_URL,
                        model_id="glm-5.2",
                        api_key_ciphertext=encrypted.ciphertext,
                        api_key_nonce=encrypted.nonce,
                        api_key_hint=encrypted.hint,
                        encryption_key_version="v1",
                    )
                )

        first_commit_completed = threading.Event()
        release_first_rotation = threading.Event()
        call_lock = threading.Lock()
        encryption_calls = 0
        original_encrypt = integration_key_rotation.encrypt_anspire_api_key

        def gated_encrypt(*args, **kwargs):
            nonlocal encryption_calls
            with call_lock:
                encryption_calls += 1
                call_number = encryption_calls
            if call_number == 2:
                first_commit_completed.set()
                assert release_first_rotation.wait(timeout=10)
            return original_encrypt(*args, **kwargs)

        monkeypatch.setattr(
            integration_key_rotation,
            "encrypt_anspire_api_key",
            gated_encrypt,
        )

        def run_first_rotation():
            with session_factory() as db:
                return rotate_integration_keys(
                    db,
                    rotation_settings,
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
                        select(func.count(ModelProviderConfig.id)).where(
                            ModelProviderConfig.encryption_key_version == "v2"
                        )
                    )
                    assert committed == 1
                with session_factory() as competitor_db:
                    with pytest.raises(
                        RuntimeError,
                        match="another integration-key rotation is already running",
                    ):
                        rotate_integration_keys(
                            competitor_db,
                            rotation_settings,
                            source_key_version="v1",
                            target_key_version="v2",
                            backup_reference="dry-run",
                            dry_run=True,
                        )
            finally:
                release_first_rotation.set()

            summary = future.result(timeout=10)

        assert summary.rotated == 2
        assert summary.remaining == 0
        with test_engine.connect() as probe:
            assert probe.scalar(
                text("SELECT pg_try_advisory_lock(:lock_id)"),
                {"lock_id": INTEGRATION_ROTATION_ADVISORY_LOCK},
            )
            assert probe.scalar(
                text("SELECT pg_advisory_unlock(:lock_id)"),
                {"lock_id": INTEGRATION_ROTATION_ADVISORY_LOCK},
            )
            probe.commit()
    finally:
        test_engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin_engine.dispose()
