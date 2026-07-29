from __future__ import annotations

import re
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request, UploadFile, status
from fastapi import File as UploadBody
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import record_audit
from ..authz import Principal, get_executive_principal
from ..config import Settings, get_settings
from ..database import get_db
from ..errors import AppError
from ..models import FileAsset, FileEvent
from ..schemas import FileOut, Page
from ..security import utc_now
from ..storage import LocalEncryptedStorage

router = APIRouter(prefix="/files", tags=["files"])
ALLOWED_EXTENSIONS = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".csv", ".txt"}
SAFE_FILENAME = re.compile(r"[^\w.\-()\u4e00-\u9fff ]", re.UNICODE)


@lru_cache
def storage_for(
    root: str,
    current_key_version: str,
    key_ring: tuple[tuple[str, bytes], ...],
) -> LocalEncryptedStorage:
    return LocalEncryptedStorage(
        Path(root),
        current_key_version=current_key_version,
        key_ring=dict(key_ring),
    )


def get_storage(settings: Annotated[Settings, Depends(get_settings)]) -> LocalEncryptedStorage:
    keys = settings.file_encryption_keys()
    return storage_for(
        str(settings.file_storage_root),
        settings.file_encryption_key_version,
        tuple(sorted(keys.items())),
    )


def owned_file(db: Session, principal: Principal, file_id: uuid.UUID) -> FileAsset:
    item = db.scalar(
        select(FileAsset).where(
            FileAsset.id == file_id,
            FileAsset.enterprise_id == principal.enterprise_id,
            # Admin/FDE cannot use their role to read an executive's files.
            FileAsset.uploaded_by_user_id == principal.user.id,
            FileAsset.deleted_at.is_(None),
        )
    )
    if item is None:
        raise AppError(404, "file_not_found", "文件不存在")
    return item


@router.get("", response_model=Page)
def list_files(
    principal: Annotated[Principal, Depends(get_executive_principal)],
    db: Annotated[Session, Depends(get_db)],
) -> Page:
    rows = db.scalars(
        select(FileAsset)
        .where(
            FileAsset.enterprise_id == principal.enterprise_id,
            FileAsset.uploaded_by_user_id == principal.user.id,
            FileAsset.deleted_at.is_(None),
        )
        .order_by(FileAsset.created_at.desc())
        .limit(100)
    ).all()
    return Page(items=[FileOut.model_validate(item) for item in rows])


@router.post("", response_model=FileOut, status_code=status.HTTP_201_CREATED)
def upload_file(
    request: Request,
    principal: Annotated[Principal, Depends(get_executive_principal)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    storage: Annotated[LocalEncryptedStorage, Depends(get_storage)],
    file: Annotated[UploadFile, UploadBody(...)],
) -> FileOut:
    raw_name = Path(file.filename or "file").name
    safe_name = SAFE_FILENAME.sub("_", raw_name).strip()[:500] or "file"
    extension = Path(safe_name).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise AppError(415, "unsupported_file_type", "仅支持 PDF、Word、Excel、PPT、CSV 和文本文件")
    stored = storage.put(file.file, settings.max_upload_bytes)
    item = FileAsset(
        enterprise_id=principal.enterprise_id,
        uploaded_by_user_id=principal.user.id,
        storage_key=stored.storage_key,
        original_name=safe_name,
        media_type=(file.content_type or "application/octet-stream")[:255],
        size_bytes=stored.size_bytes,
        sha256=stored.sha256,
        encryption_key_version=stored.encryption_key_version,
        status="ready",
    )
    try:
        db.add(item)
        db.flush()
        db.add(FileEvent(file_id=item.id, actor_user_id=principal.user.id, event_type="uploaded"))
        record_audit(
            db,
            request,
            "file.uploaded",
            actor=principal.user,
            session=principal.session,
            target_type="file",
            target_id=item.id,
            metadata={
                "size_bytes": item.size_bytes,
                "media_type": item.media_type,
                "sha256_prefix": item.sha256[:12],
            },
        )
        db.commit()
    except Exception:
        db.rollback()
        storage.delete(stored.storage_key)
        raise
    return FileOut.model_validate(item)


@router.get("/{file_id}", response_model=FileOut)
def get_file(
    file_id: uuid.UUID,
    principal: Annotated[Principal, Depends(get_executive_principal)],
    db: Annotated[Session, Depends(get_db)],
) -> FileOut:
    return FileOut.model_validate(owned_file(db, principal, file_id))


@router.get("/{file_id}/content")
def download_file(
    file_id: uuid.UUID,
    request: Request,
    principal: Annotated[Principal, Depends(get_executive_principal)],
    db: Annotated[Session, Depends(get_db)],
    storage: Annotated[LocalEncryptedStorage, Depends(get_storage)],
) -> Response:
    item = owned_file(db, principal, file_id)
    content = storage.open_decrypted(item.storage_key, item.encryption_key_version)
    if len(content) != item.size_bytes:
        raise AppError(500, "file_integrity_error", "文件大小校验失败")
    db.add(FileEvent(file_id=item.id, actor_user_id=principal.user.id, event_type="downloaded"))
    record_audit(
        db,
        request,
        "file.downloaded",
        actor=principal.user,
        session=principal.session,
        target_type="file",
        target_id=item.id,
        metadata={"size_bytes": item.size_bytes, "media_type": item.media_type},
    )
    db.commit()
    encoded = quote(item.original_name)
    return Response(
        content=content,
        media_type=item.media_type,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded}"},
    )


@router.delete("/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_file(
    file_id: uuid.UUID,
    request: Request,
    principal: Annotated[Principal, Depends(get_executive_principal)],
    db: Annotated[Session, Depends(get_db)],
    storage: Annotated[LocalEncryptedStorage, Depends(get_storage)],
) -> None:
    item = owned_file(db, principal, file_id)
    item.deleted_at = utc_now()
    item.status = "deleted"
    db.add(FileEvent(file_id=item.id, actor_user_id=principal.user.id, event_type="deleted"))
    record_audit(
        db,
        request,
        "file.deleted",
        actor=principal.user,
        session=principal.session,
        target_type="file",
        target_id=item.id,
    )
    db.commit()
    storage.delete(item.storage_key)
