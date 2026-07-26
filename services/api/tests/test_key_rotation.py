from __future__ import annotations

import base64
import hashlib
import io
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from pydantic import ValidationError
from sqlalchemy import select

from executive_ai_api import audit_integrity
from executive_ai_api.audit_integrity import (
    calculate_integrity_hash,
    canonical_payload,
    verify_audit_chain,
    verify_audit_event,
)
from executive_ai_api.backup_evidence import verify_backup_evidence
from executive_ai_api.config import Settings, get_settings
from executive_ai_api.database import SessionLocal
from executive_ai_api.errors import AppError
from executive_ai_api.file_key_rotation import rotate_file_keys, verify_file_key_version
from executive_ai_api.models import (
    AuditChainHead,
    AuditEvent,
    FileAsset,
    FileEvent,
)
from executive_ai_api.storage import MAGIC_V1, LocalEncryptedStorage


def encoded_key(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode()


def test_settings_load_current_and_historical_keys_without_database_storage(tmp_path) -> None:
    file_ring = tmp_path / "file-ring.json"
    file_ring.write_text(json.dumps({"v1": encoded_key(b"O" * 32)}), encoding="utf-8")
    os.chmod(file_ring, 0o600)
    settings = Settings(
        _env_file=None,
        app_env="test",
        app_mode="demo",
        file_encryption_key=encoded_key(b"N" * 32),
        file_encryption_key_version="v2",
        file_encryption_key_ring_file=file_ring,
        audit_hmac_key="B" * 40,
        audit_hmac_key_version="audit-v2",
        audit_hmac_legacy_key_version="audit-v1",
        audit_hmac_key_ring=json.dumps({"audit-v1": "A" * 40}),
    )
    assert settings.file_encryption_keys() == {"v1": b"O" * 32, "v2": b"N" * 32}
    assert settings.audit_hmac_keys() == {"audit-v1": "A" * 40, "audit-v2": "B" * 40}

    os.chmod(file_ring, 0o644)
    with pytest.raises(RuntimeError, match="group or others"):
        settings.file_encryption_keys()


def test_protected_debug_and_missing_legacy_audit_key_fail_closed() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            app_env="customer-template",
            app_mode="production",
            service_role="bootstrap",
            debug=True,
            audit_hmac_key="B" * 40,
        )
    with pytest.raises(ValidationError, match="AUDIT_HMAC_LEGACY_KEY_VERSION"):
        Settings(
            _env_file=None,
            app_env="customer-template",
            app_mode="production",
            service_role="bootstrap",
            audit_hmac_key="B" * 40,
            audit_hmac_key_version="v2",
            audit_hmac_legacy_key_version="v1",
        )


def write_legacy_object(root: Path, storage_key: str, key: bytes, plaintext: bytes) -> None:
    path = root / storage_key
    path.parent.mkdir(parents=True, exist_ok=True)
    nonce = os.urandom(12)
    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(storage_key.encode())
    ciphertext = encryptor.update(plaintext) + encryptor.finalize()
    path.write_bytes(MAGIC_V1 + nonce + ciphertext + encryptor.tag)


def test_storage_rotates_legacy_blob_and_recovers_after_database_update_interruption(
    tmp_path,
) -> None:
    old_key = b"O" * 32
    new_key = b"N" * 32
    plaintext = b"board-only confidential material"
    storage_key = "aa/bb/legacy.bin"
    write_legacy_object(tmp_path, storage_key, old_key, plaintext)
    storage = LocalEncryptedStorage(
        tmp_path,
        current_key_version="v2",
        key_ring={"v1": old_key, "v2": new_key},
    )
    digest = hashlib.sha256(plaintext).hexdigest()
    first = storage.reencrypt_atomic(
        storage_key,
        source_key_version="v1",
        target_key_version="v2",
        expected_size_bytes=len(plaintext),
        expected_sha256=digest,
    )
    assert first.rewritten is True
    assert storage.inspect_key_version(storage_key) == "v2"
    assert storage.open_decrypted(storage_key, "v2") == plaintext
    with pytest.raises(AppError) as mismatch:
        storage.open_decrypted(storage_key, "v1")
    assert mismatch.value.code == "file_key_version_mismatch"

    # Simulates a crash after atomic blob replacement but before DB version commit.
    resumed = storage.reencrypt_atomic(
        storage_key,
        source_key_version="v1",
        target_key_version="v2",
        expected_size_bytes=len(plaintext),
        expected_sha256=digest,
    )
    assert resumed.rewritten is False


def test_rotation_is_resumable_and_records_file_and_audit_events(seeded) -> None:
    root = get_settings().file_storage_root
    old_key = b"O" * 32
    new_key = b"N" * 32
    old_storage = LocalEncryptedStorage(
        root,
        current_key_version="v1",
        key_ring={"v1": old_key},
    )
    rotation_storage = LocalEncryptedStorage(
        root,
        current_key_version="v2",
        key_ring={"v1": old_key, "v2": new_key},
    )
    first_plaintext = b"first file"
    second_plaintext = b"second file already rewritten"
    first = old_storage.put(io.BytesIO(first_plaintext), 1024)
    second = rotation_storage.put(io.BytesIO(second_plaintext), 1024)
    with SessionLocal.begin() as db:
        for stored, name in ((first, "first.pdf"), (second, "second.pdf")):
            db.add(
                FileAsset(
                    enterprise_id=seeded["enterprise_id"],
                    uploaded_by_user_id=seeded["users"]["executive@example.com"],
                    storage_key=stored.storage_key,
                    original_name=name,
                    media_type="application/pdf",
                    size_bytes=stored.size_bytes,
                    sha256=stored.sha256,
                    encryption_key_version="v1",
                    status="ready",
                )
            )

    with SessionLocal() as db:
        partial = rotate_file_keys(
            db,
            rotation_storage,
            source_key_version="v1",
            target_key_version="v2",
            backup_reference="test:verified-backup",
            max_files=1,
        )
        assert partial.inspected == 1
        assert partial.remaining == 1
    with SessionLocal() as db:
        resumed = rotate_file_keys(
            db,
            rotation_storage,
            source_key_version="v1",
            target_key_version="v2",
            backup_reference="test:verified-backup",
        )
        assert resumed.inspected == 1
        assert resumed.remaining == 0
        assert verify_file_key_version(db, rotation_storage, key_version="v2") == 2
    with SessionLocal() as db:
        assert set(db.scalars(select(FileAsset.encryption_key_version)).all()) == {"v2"}
        assert (
            len(db.scalars(select(FileEvent).where(FileEvent.event_type == "reencrypted")).all())
            == 2
        )
        assert (
            len(db.scalars(select(AuditEvent).where(AuditEvent.action == "file.reencrypted")).all())
            == 2
        )


def test_backup_evidence_requires_valid_signature_and_artifact_hashes(tmp_path) -> None:
    backup = tmp_path / "backup"
    backup.mkdir()
    database = backup / "database.dump.enc"
    files = backup / "files.tar.enc"
    database.write_bytes(b"encrypted database")
    files.write_bytes(b"encrypted files")
    manifest = backup / "manifest.env"
    manifest.write_text(
        "\n".join(
            (
                "format_version=1",
                "environment=local-demo",
                "created_at_utc=20260727T120000Z",
                "alembic_revision=a83f4c91d720",
                "consistency=application-quiesced",
                "database_file=database.dump.enc",
                f"database_sha256={hashlib.sha256(database.read_bytes()).hexdigest()}",
                "files_file=files.tar.enc",
                f"files_sha256={hashlib.sha256(files.read_bytes()).hexdigest()}",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    private_key = Ed25519PrivateKey.generate()
    public_key = tmp_path / "backup-signing-public.pem"
    public_key.write_bytes(
        private_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    (backup / "manifest.sig").write_bytes(private_key.sign(manifest.read_bytes()))
    evidence = verify_backup_evidence(
        backup,
        public_key,
        expected_environment="local-demo",
        now=datetime(2026, 7, 27, 13, tzinfo=UTC),
    )
    assert evidence.alembic_revision == "a83f4c91d720"
    files.write_bytes(b"tampered")
    with pytest.raises(RuntimeError, match="checksum"):
        verify_backup_evidence(
            backup,
            public_key,
            expected_environment="local-demo",
            now=datetime(2026, 7, 27, 13, tzinfo=UTC),
        )


def test_audit_chain_survives_key_rotation_and_binds_key_versions(monkeypatch, seeded) -> None:
    v1 = Settings(
        _env_file=None,
        app_env="test",
        app_mode="demo",
        audit_hmac_key="A" * 40,
        audit_hmac_key_version="v1",
        audit_hmac_legacy_key_version="v1",
    )
    monkeypatch.setattr(audit_integrity, "get_settings", lambda: v1)
    with SessionLocal.begin() as db:
        first = AuditEvent(
            enterprise_id=seeded["enterprise_id"],
            action="rotation.before",
            environment="",
            integrity_hash="",
        )
        db.add(first)
        db.flush()
        assert first.integrity_hash == calculate_integrity_hash(first, "A" * 40), canonical_payload(
            first
        )
        first_id = first.id

    v2 = Settings(
        _env_file=None,
        app_env="test",
        app_mode="demo",
        audit_hmac_key="B" * 40,
        audit_hmac_key_version="v2",
        audit_hmac_legacy_key_version="v1",
        audit_hmac_key_ring=json.dumps({"v1": "A" * 40}),
    )
    monkeypatch.setattr(audit_integrity, "get_settings", lambda: v2)
    with SessionLocal.begin() as db:
        second = AuditEvent(
            enterprise_id=seeded["enterprise_id"],
            action="rotation.after",
            environment="",
            integrity_hash="",
        )
        db.add(second)
        db.flush()
        second_id = second.id
    with SessionLocal() as db:
        first = db.get(AuditEvent, first_id)
        second = db.get(AuditEvent, second_id)
        head = db.get(AuditChainHead, f"enterprise:{seeded['enterprise_id']}")
        assert first.audit_key_version == "v1"
        assert second.audit_key_version == "v2"
        assert head.anchor_key_version == "v2"
        assert verify_audit_event(first), (
            first.integrity_hash,
            calculate_integrity_hash(first, "A" * 40),
            canonical_payload(first),
        )
        assert verify_audit_event(second)
        assert verify_audit_chain(db, seeded["enterprise_id"]).valid

    with SessionLocal.begin() as db:
        db.get(AuditEvent, first_id).audit_key_version = "v2"
    with SessionLocal() as db:
        assert not verify_audit_chain(db, seeded["enterprise_id"]).valid
