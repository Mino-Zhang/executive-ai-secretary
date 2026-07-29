from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..database import get_db
from ..errors import AppError
from ..storage import LocalEncryptedStorage

router = APIRouter(tags=["health"])
EXPECTED_DATABASE_REVISION = "c5d91f4a8b72"


@router.get("/health/live", include_in_schema=False)
def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready", include_in_schema=False)
def ready(
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, str]:
    try:
        db.execute(text("SELECT 1"))
        if db.bind is not None and db.bind.dialect.name != "sqlite":
            revision = db.scalar(text("SELECT version_num FROM alembic_version LIMIT 1"))
            if revision != EXPECTED_DATABASE_REVISION:
                raise RuntimeError(
                    f"database revision {revision!r} does not match {EXPECTED_DATABASE_REVISION}"
                )
        storage = LocalEncryptedStorage(
            settings.file_storage_root,
            current_key_version=settings.file_encryption_key_version,
            key_ring=settings.file_encryption_keys(),
        )
        storage.self_test()
    except Exception as exc:
        raise AppError(503, "not_ready", "服务尚未就绪") from exc
    return {
        "status": "ready",
        "database": "ok",
        "database_revision": EXPECTED_DATABASE_REVISION,
        "storage": "encrypted-round-trip-ok",
    }
