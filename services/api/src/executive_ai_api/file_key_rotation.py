from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy import func, select, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session

from .models import AuditEvent, FileAsset, FileEvent
from .storage import LocalEncryptedStorage

ROTATION_ADVISORY_LOCK = 718_994_731


@dataclass
class RotationSummary:
    inspected: int = 0
    rewritten: int = 0
    reconciled: int = 0
    remaining: int = 0


def _acquire_rotation_lock(connection: Connection) -> bool:
    return bool(
        connection.scalar(
            text("SELECT pg_try_advisory_lock(:lock_id)"),
            {"lock_id": ROTATION_ADVISORY_LOCK},
        )
    )


def _release_rotation_lock(connection: Connection) -> bool:
    return bool(
        connection.scalar(
            text("SELECT pg_advisory_unlock(:lock_id)"),
            {"lock_id": ROTATION_ADVISORY_LOCK},
        )
    )


def _session_engine(db: Session) -> Engine:
    bind = db.get_bind()
    if isinstance(bind, Connection):
        return bind.engine
    return bind


@contextmanager
def _exclusive_rotation_session(db: Session) -> Iterator[Session]:
    """Keep the PostgreSQL advisory lock and all rotation commits on one backend.

    A session-level PostgreSQL advisory lock belongs to the physical database
    connection, not to a SQLAlchemy ``Session``. A normal ``Session.commit()``
    can return its connection to the pool, so acquiring the lock through the
    caller's session and then committing each file can silently lose ownership
    of the locked backend.

    PostgreSQL rotations therefore use a dedicated ``Connection`` for both the
    advisory lock and an internally-bound ``Session``. Per-file commits remain
    durable and resumable, while the physical connection stays checked out for
    the complete rotation. SQLite keeps using the caller's session unchanged.
    """

    bind = db.get_bind()
    if bind.dialect.name != "postgresql":
        yield db
        return
    if db.in_transaction() or db.new or db.dirty or db.deleted:
        raise RuntimeError("PostgreSQL file-key rotation requires an idle database session")

    engine = _session_engine(db)
    with engine.connect() as connection:
        if not _acquire_rotation_lock(connection):
            connection.rollback()
            raise RuntimeError("another file-key rotation is already running")

        # End the transaction opened by the SELECT without releasing the
        # session-level lock. This lets the bound Session own and commit each
        # subsequent transaction while the same physical connection is pinned.
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
                    raise RuntimeError("file-key rotation advisory lock ownership was lost")
            except BaseException:
                # Closing an invalidated backend is the final PostgreSQL
                # guarantee that any remaining session-level lock is released.
                connection.invalidate()
                if not operation_failed:
                    raise


def rotate_file_keys(
    db: Session,
    storage: LocalEncryptedStorage,
    *,
    source_key_version: str,
    target_key_version: str,
    backup_reference: str,
    batch_size: int = 25,
    max_files: int | None = None,
    dry_run: bool = False,
) -> RotationSummary:
    if source_key_version == target_key_version:
        raise ValueError("source and target key versions must differ")
    if not backup_reference and not dry_run:
        raise ValueError("verified backup evidence is required before file-key rotation")
    if not 1 <= batch_size <= 500:
        raise ValueError("batch size must be between 1 and 500")
    if max_files is not None and max_files < 1:
        raise ValueError("max files must be positive")

    with _exclusive_rotation_session(db) as rotation_db:
        return _rotate_file_keys_locked(
            rotation_db,
            storage,
            source_key_version=source_key_version,
            target_key_version=target_key_version,
            backup_reference=backup_reference,
            batch_size=batch_size,
            max_files=max_files,
            dry_run=dry_run,
        )


def _rotate_file_keys_locked(
    db: Session,
    storage: LocalEncryptedStorage,
    *,
    source_key_version: str,
    target_key_version: str,
    backup_reference: str,
    batch_size: int,
    max_files: int | None,
    dry_run: bool,
) -> RotationSummary:
    summary = RotationSummary()
    cursor: uuid.UUID | None = None
    try:
        while max_files is None or summary.inspected < max_files:
            page_size = batch_size
            if max_files is not None:
                page_size = min(page_size, max_files - summary.inspected)
            statement = (
                select(FileAsset)
                .where(
                    FileAsset.deleted_at.is_(None),
                    FileAsset.encryption_key_version == source_key_version,
                )
                .order_by(FileAsset.id)
                .limit(page_size)
            )
            if cursor is not None:
                statement = statement.where(FileAsset.id > cursor)
            if db.bind is not None and db.bind.dialect.name == "postgresql" and not dry_run:
                statement = statement.with_for_update(skip_locked=True)
            rows = db.scalars(statement).all()
            if not rows:
                break
            for item in rows:
                cursor = item.id
                actual_version = storage.verify_integrity(
                    item.storage_key,
                    database_key_version=item.encryption_key_version,
                    expected_size_bytes=item.size_bytes,
                    expected_sha256=item.sha256,
                    allowed_embedded_versions={source_key_version, target_key_version},
                )
                summary.inspected += 1
                if dry_run:
                    if actual_version == target_key_version:
                        summary.reconciled += 1
                    continue
                result = storage.reencrypt_atomic(
                    item.storage_key,
                    source_key_version=source_key_version,
                    target_key_version=target_key_version,
                    expected_size_bytes=item.size_bytes,
                    expected_sha256=item.sha256,
                )
                item.encryption_key_version = target_key_version
                db.add(
                    FileEvent(
                        file_id=item.id,
                        event_type="reencrypted",
                        metadata_json={
                            "source_key_version": source_key_version,
                            "target_key_version": target_key_version,
                            "rewritten": result.rewritten,
                            "backup_reference": backup_reference,
                        },
                    )
                )
                db.add(
                    AuditEvent(
                        enterprise_id=item.enterprise_id,
                        action="file.reencrypted",
                        target_type="file",
                        target_id=str(item.id),
                        outcome="success",
                        environment="",
                        integrity_hash="",
                        metadata_json={
                            "source_key_version": source_key_version,
                            "target_key_version": target_key_version,
                            "rewritten": result.rewritten,
                            "backup_reference": backup_reference,
                        },
                        scope_summary_json={
                            "enterprise_wide": False,
                            "organization_unit_ids": [],
                        },
                    )
                )
                db.commit()
                if result.rewritten:
                    summary.rewritten += 1
                else:
                    summary.reconciled += 1
        summary.remaining = int(
            db.scalar(
                select(func.count())
                .select_from(FileAsset)
                .where(
                    FileAsset.deleted_at.is_(None),
                    FileAsset.encryption_key_version == source_key_version,
                )
            )
            or 0
        )
        return summary
    except BaseException:
        db.rollback()
        raise


def verify_file_key_version(
    db: Session,
    storage: LocalEncryptedStorage,
    *,
    key_version: str,
) -> int:
    rows = db.scalars(
        select(FileAsset).where(
            FileAsset.deleted_at.is_(None),
            FileAsset.encryption_key_version == key_version,
        )
    ).all()
    for item in rows:
        actual = storage.verify_integrity(
            item.storage_key,
            database_key_version=item.encryption_key_version,
            expected_size_bytes=item.size_bytes,
            expected_sha256=item.sha256,
            allowed_embedded_versions={key_version},
        )
        if actual != key_version:
            raise RuntimeError("file key-version verification failed")
    return len(rows)
