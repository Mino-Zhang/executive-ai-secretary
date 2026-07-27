from __future__ import annotations

import base64
import json
import os
import sys

import pytest
from sqlalchemy import select

from executive_ai_api import rotate_integration_keys as rotation_cli
from executive_ai_api.anspire import (
    ANSPIRE_ENDPOINT_URL,
    AnspireConfigurationError,
    decrypt_anspire_api_key,
    encrypt_anspire_api_key,
)
from executive_ai_api.config import Settings
from executive_ai_api.database import SessionLocal
from executive_ai_api.integration_key_rotation import (
    rotate_integration_keys,
    verify_integration_key_version,
)
from executive_ai_api.models import AuditEvent, ModelProviderConfig


def encoded_key(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode()


def test_integration_key_ring_loads_historical_versions_and_rejects_unsafe_file(
    tmp_path,
) -> None:
    ring_file = tmp_path / "integration-ring.json"
    ring_file.write_text(
        json.dumps({"v1": encoded_key(b"O" * 32)}),
        encoding="utf-8",
    )
    os.chmod(ring_file, 0o600)
    settings = Settings(
        _env_file=None,
        app_env="test",
        app_mode="demo",
        integration_encryption_key=encoded_key(b"N" * 32),
        integration_encryption_key_version="v2",
        integration_encryption_key_ring_file=ring_file,
    )
    assert settings.integration_encryption_keys() == {
        "v1": b"O" * 32,
        "v2": b"N" * 32,
    }

    os.chmod(ring_file, 0o644)
    with pytest.raises(RuntimeError, match="group or others"):
        settings.integration_encryption_keys()


def test_historical_integration_credential_decrypts_only_when_version_is_loaded(seeded) -> None:
    old_settings = Settings(
        _env_file=None,
        app_env="test",
        app_mode="demo",
        integration_encryption_key=encoded_key(b"O" * 32),
        integration_encryption_key_version="v1",
    )
    encrypted = encrypt_anspire_api_key(
        "unit-test-historical-anspire-key-123456",
        enterprise_id=seeded["enterprise_id"],
        settings=old_settings,
    )
    config = ModelProviderConfig(
        enterprise_id=seeded["enterprise_id"],
        provider="anspire",
        endpoint_url=ANSPIRE_ENDPOINT_URL,
        model_id="glm-5.2",
        api_key_ciphertext=encrypted.ciphertext,
        api_key_nonce=encrypted.nonce,
        api_key_hint=encrypted.hint,
        encryption_key_version="v1",
    )
    rotated_settings = Settings(
        _env_file=None,
        app_env="test",
        app_mode="demo",
        integration_encryption_key=encoded_key(b"N" * 32),
        integration_encryption_key_version="v2",
        integration_encryption_key_ring=json.dumps({"v1": encoded_key(b"O" * 32)}),
    )
    assert (
        decrypt_anspire_api_key(config, rotated_settings)
        == "unit-test-historical-anspire-key-123456"
    )

    missing_history = Settings(
        _env_file=None,
        app_env="test",
        app_mode="demo",
        integration_encryption_key=encoded_key(b"N" * 32),
        integration_encryption_key_version="v2",
    )
    with pytest.raises(AnspireConfigurationError) as unavailable:
        decrypt_anspire_api_key(config, missing_history)
    assert unavailable.value.code == "anspire_key_version_unavailable"


def test_integration_rotation_requires_backup_is_resumable_and_idempotent(seeded) -> None:
    api_key = "unit-test-rotation-anspire-key-123456"
    old_settings = Settings(
        _env_file=None,
        app_env="test",
        app_mode="demo",
        integration_encryption_key=encoded_key(b"O" * 32),
        integration_encryption_key_version="v1",
    )
    encrypted = encrypt_anspire_api_key(
        api_key,
        enterprise_id=seeded["enterprise_id"],
        settings=old_settings,
    )
    with SessionLocal.begin() as db:
        db.add(
            ModelProviderConfig(
                enterprise_id=seeded["enterprise_id"],
                provider="anspire",
                endpoint_url=ANSPIRE_ENDPOINT_URL,
                model_id="glm-5.2",
                api_key_ciphertext=encrypted.ciphertext,
                api_key_nonce=encrypted.nonce,
                api_key_hint=encrypted.hint,
                encryption_key_version="v1",
            )
        )

    rotation_settings = Settings(
        _env_file=None,
        app_env="test",
        app_mode="demo",
        integration_encryption_key=encoded_key(b"N" * 32),
        integration_encryption_key_version="v2",
        integration_encryption_key_ring=json.dumps({"v1": encoded_key(b"O" * 32)}),
    )
    with SessionLocal() as db:
        with pytest.raises(ValueError, match="backup evidence"):
            rotate_integration_keys(
                db,
                rotation_settings,
                source_key_version="v1",
                target_key_version="v2",
                backup_reference="",
            )
        dry_run = rotate_integration_keys(
            db,
            rotation_settings,
            source_key_version="v1",
            target_key_version="v2",
            backup_reference="",
            dry_run=True,
        )
        assert dry_run.inspected == 1
        assert dry_run.rotated == 0
        assert dry_run.remaining == 1

    with SessionLocal() as db:
        completed = rotate_integration_keys(
            db,
            rotation_settings,
            source_key_version="v1",
            target_key_version="v2",
            backup_reference="test:verified-backup",
        )
        assert completed.inspected == 1
        assert completed.rotated == 1
        assert completed.remaining == 0
        assert (
            verify_integration_key_version(
                db,
                rotation_settings,
                key_version="v2",
            )
            == 1
        )

    with SessionLocal() as db:
        repeated = rotate_integration_keys(
            db,
            rotation_settings,
            source_key_version="v1",
            target_key_version="v2",
            backup_reference="test:verified-backup",
        )
        assert repeated.inspected == 0
        assert repeated.rotated == 0
        assert repeated.remaining == 0
        stored = db.scalar(select(ModelProviderConfig))
        assert stored is not None
        assert stored.encryption_key_version == "v2"
        assert decrypt_anspire_api_key(stored, rotation_settings) == api_key
        audits = db.scalars(
            select(AuditEvent).where(AuditEvent.action == "integration.credential_reencrypted")
        ).all()
        assert len(audits) == 1
        assert api_key not in json.dumps(audits[0].metadata_json)


def test_integration_rotation_cli_requires_exact_confirmation_and_backup(
    monkeypatch,
) -> None:
    settings = Settings(
        _env_file=None,
        app_env="test",
        app_mode="demo",
        integration_encryption_key=encoded_key(b"N" * 32),
        integration_encryption_key_version="v2",
        integration_encryption_key_ring=json.dumps({"v1": encoded_key(b"O" * 32)}),
    )
    monkeypatch.setattr(rotation_cli, "get_settings", lambda: settings)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "rotate-integration-keys",
            "--from-version",
            "v1",
            "--to-version",
            "v2",
        ],
    )
    with pytest.raises(SystemExit, match="pass --confirm"):
        rotation_cli.main()

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "rotate-integration-keys",
            "--from-version",
            "v1",
            "--to-version",
            "v2",
            "--confirm",
            "ROTATE INTEGRATION KEYS v1 TO v2",
        ],
    )
    with pytest.raises(SystemExit, match="backup-dir"):
        rotation_cli.main()
