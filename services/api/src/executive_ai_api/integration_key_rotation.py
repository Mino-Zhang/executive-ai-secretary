from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy import func, select, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session

from .anspire import (
    ANSPIRE_PROVIDER,
    decrypt_anspire_api_key,
    encrypt_anspire_api_key,
)
from .config import Settings
from .models import AuditEvent, ModelProviderConfig

INTEGRATION_ROTATION_ADVISORY_LOCK = 718_994_732


@dataclass
class IntegrationRotationSummary:
    inspected: int = 0
    rotated: int = 0
    remaining: int = 0


def _acquire_rotation_lock(connection: Connection) -> bool:
    return bool(
        connection.scalar(
            text("SELECT pg_try_advisory_lock(:lock_id)"),
            {"lock_id": INTEGRATION_ROTATION_ADVISORY_LOCK},
        )
    )


def _release_rotation_lock(connection: Connection) -> bool:
    return bool(
        connection.scalar(
            text("SELECT pg_advisory_unlock(:lock_id)"),
            {"lock_id": INTEGRATION_ROTATION_ADVISORY_LOCK},
        )
    )


def _session_engine(db: Session) -> Engine:
    bind = db.get_bind()
    if isinstance(bind, Connection):
        return bind.engine
    return bind


@contextmanager
def _exclusive_rotation_session(db: Session) -> Iterator[Session]:
    bind = db.get_bind()
    if bind.dialect.name != "postgresql":
        yield db
        return
    if db.in_transaction() or db.new or db.dirty or db.deleted:
        raise RuntimeError("PostgreSQL integration-key rotation requires an idle database session")

    engine = _session_engine(db)
    with engine.connect() as connection:
        if not _acquire_rotation_lock(connection):
            connection.rollback()
            raise RuntimeError("another integration-key rotation is already running")
        connection.commit()
        rotation_db = Session(bind=connection, autoflush=False, expire_on_commit=False)
        operation_failed = False
        try:
            yield rotation_db
        except BaseException:
            operation_failed = True
            rotation_db.rollback()
            raise
        finally:
            rotation_db.close()
            try:
                unlocked = _release_rotation_lock(connection)
                connection.commit()
                if not unlocked and not operation_failed:
                    raise RuntimeError("integration-key rotation advisory lock ownership was lost")
            except BaseException:
                connection.invalidate()
                if not operation_failed:
                    raise


def _configured_with_version(key_version: str):
    return (
        ModelProviderConfig.provider == ANSPIRE_PROVIDER,
        ModelProviderConfig.api_key_ciphertext.is_not(None),
        ModelProviderConfig.api_key_nonce.is_not(None),
        ModelProviderConfig.encryption_key_version == key_version,
    )


def rotate_integration_keys(
    db: Session,
    settings: Settings,
    *,
    source_key_version: str,
    target_key_version: str,
    backup_reference: str,
    batch_size: int = 25,
    max_configs: int | None = None,
    dry_run: bool = False,
) -> IntegrationRotationSummary:
    if source_key_version == target_key_version:
        raise ValueError("source and target key versions must differ")
    if target_key_version != settings.integration_encryption_key_version:
        raise ValueError("target key version must equal INTEGRATION_ENCRYPTION_KEY_VERSION")
    if not backup_reference and not dry_run:
        raise ValueError("verified backup evidence is required before integration-key rotation")
    if not 1 <= batch_size <= 500:
        raise ValueError("batch size must be between 1 and 500")
    if max_configs is not None and max_configs < 1:
        raise ValueError("max configs must be positive")
    keys = settings.integration_encryption_keys()
    for version in (source_key_version, target_key_version):
        if version not in keys:
            raise ValueError(f"integration key version {version!r} is unavailable")

    with _exclusive_rotation_session(db) as rotation_db:
        return _rotate_integration_keys_locked(
            rotation_db,
            settings,
            source_key_version=source_key_version,
            target_key_version=target_key_version,
            backup_reference=backup_reference,
            batch_size=batch_size,
            max_configs=max_configs,
            dry_run=dry_run,
        )


def _rotate_integration_keys_locked(
    db: Session,
    settings: Settings,
    *,
    source_key_version: str,
    target_key_version: str,
    backup_reference: str,
    batch_size: int,
    max_configs: int | None,
    dry_run: bool,
) -> IntegrationRotationSummary:
    summary = IntegrationRotationSummary()
    cursor: uuid.UUID | None = None
    try:
        while max_configs is None or summary.inspected < max_configs:
            page_size = batch_size
            if max_configs is not None:
                page_size = min(page_size, max_configs - summary.inspected)
            id_statement = (
                select(ModelProviderConfig.id)
                .where(*_configured_with_version(source_key_version))
                .order_by(ModelProviderConfig.id)
                .limit(page_size)
            )
            if cursor is not None:
                id_statement = id_statement.where(ModelProviderConfig.id > cursor)
            candidate_ids = list(db.scalars(id_statement))
            if not candidate_ids:
                break

            for config_id in candidate_ids:
                cursor = config_id
                statement = select(ModelProviderConfig).where(
                    ModelProviderConfig.id == config_id,
                    *_configured_with_version(source_key_version),
                )
                if db.bind is not None and db.bind.dialect.name == "postgresql" and not dry_run:
                    statement = statement.with_for_update()
                item = db.scalar(statement)
                if item is None:
                    continue

                plaintext = decrypt_anspire_api_key(item, settings)
                summary.inspected += 1
                if dry_run:
                    continue

                encrypted = encrypt_anspire_api_key(
                    plaintext,
                    enterprise_id=item.enterprise_id,
                    settings=settings,
                )
                if encrypted.key_version != target_key_version:
                    raise RuntimeError(
                        "integration-key rotation produced an unexpected key version"
                    )
                item.api_key_ciphertext = encrypted.ciphertext
                item.api_key_nonce = encrypted.nonce
                item.api_key_hint = encrypted.hint
                item.encryption_key_version = encrypted.key_version
                db.add(
                    AuditEvent(
                        enterprise_id=item.enterprise_id,
                        action="integration.credential_reencrypted",
                        target_type="model_provider_config",
                        target_id=str(item.id),
                        outcome="success",
                        environment="",
                        integrity_hash="",
                        metadata_json={
                            "provider": item.provider,
                            "source_key_version": source_key_version,
                            "target_key_version": target_key_version,
                            "backup_reference": backup_reference,
                        },
                        scope_summary_json={
                            "enterprise_wide": False,
                            "organization_unit_ids": [],
                        },
                    )
                )
                db.commit()
                summary.rotated += 1

        summary.remaining = int(
            db.scalar(
                select(func.count())
                .select_from(ModelProviderConfig)
                .where(*_configured_with_version(source_key_version))
            )
            or 0
        )
        return summary
    except BaseException:
        db.rollback()
        raise


def verify_integration_key_version(
    db: Session,
    settings: Settings,
    *,
    key_version: str,
) -> int:
    if key_version not in settings.integration_encryption_keys():
        raise ValueError(f"integration key version {key_version!r} is unavailable")
    rows = db.scalars(
        select(ModelProviderConfig).where(*_configured_with_version(key_version))
    ).all()
    for item in rows:
        decrypt_anspire_api_key(item, settings)
    return len(rows)
