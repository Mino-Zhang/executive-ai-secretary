from __future__ import annotations

import uuid
from pathlib import Path

from sqlalchemy import select

from executive_ai_api.config import get_settings
from executive_ai_api.database import SessionLocal
from executive_ai_api.models import FileAsset, FileChunk, FileExtraction

from .conftest import login, login_and_change_password


def test_upload_contract_encryption_download_and_owner_privacy(client, seeded) -> None:
    auth = login_and_change_password(client)
    plaintext = b"confidential executive report content"
    upload = client.post(
        "/api/v1/files",
        headers={"X-CSRF-Token": auth["csrf_token"]},
        files={"file": ("report.pdf", plaintext, "application/pdf")},
    )
    assert upload.status_code == 201, upload.text
    file_id = uuid.UUID(upload.json()["id"])
    assert upload.json()["size_bytes"] == len(plaintext)
    assert upload.json()["status"] == "ready"
    assert upload.json()["encryption_key_version"] == "v1"

    stored_bytes = b"".join(
        path.read_bytes() for path in Path(get_settings().file_storage_root).rglob("*.bin")
    )
    assert plaintext not in stored_bytes
    assert stored_bytes.startswith(b"EAIF2")

    downloaded = client.get(f"/api/v1/files/{file_id}/content")
    assert downloaded.status_code == 200
    assert downloaded.content == plaintext

    with client.__class__(client.app) as admin_client:
        login(admin_client, "admin@example.com")
        assert admin_client.get(f"/api/v1/files/{file_id}").status_code == 403
        assert admin_client.get(f"/api/v1/files/{file_id}/content").status_code == 403


def test_upload_field_name_is_file(client, seeded) -> None:
    auth = login_and_change_password(client)
    wrong = client.post(
        "/api/v1/files",
        headers={"X-CSRF-Token": auth["csrf_token"]},
        files={"upload": ("report.pdf", b"x", "application/pdf")},
    )
    assert wrong.status_code == 422


def test_delete_file_removes_derived_text_and_vectors(client, seeded) -> None:
    auth = login_and_change_password(client)
    upload = client.post(
        "/api/v1/files",
        headers={"X-CSRF-Token": auth["csrf_token"]},
        files={
            "file": (
                "board-brief.docx",
                b"synthetic office payload",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )
    assert upload.status_code == 201, upload.text
    file_id = uuid.UUID(upload.json()["id"])
    with SessionLocal.begin() as db:
        asset = db.get(FileAsset, file_id)
        assert asset is not None
        stored_path = Path(get_settings().file_storage_root) / asset.storage_key
        extraction = db.scalar(select(FileExtraction).where(FileExtraction.file_id == asset.id))
        assert extraction is not None
        extraction.status = "completed"
        extraction.chunk_count = 1
        db.add(
            FileChunk(
                extraction_id=extraction.id,
                file_id=asset.id,
                chunk_index=0,
                content="仅属于当前会话的经营材料",
                locator_json={"type": "paragraph", "paragraph": 1},
                token_count=8,
                embedding=[0.0] * 512,
            )
        )
    assert stored_path.exists()

    deleted = client.delete(
        f"/api/v1/files/{file_id}",
        headers={"X-CSRF-Token": auth["csrf_token"]},
    )
    assert deleted.status_code == 204, deleted.text
    with SessionLocal() as db:
        asset = db.get(FileAsset, file_id)
        assert asset is not None and asset.status == "deleted"
        assert db.scalar(select(FileExtraction).where(FileExtraction.file_id == asset.id)) is None
        assert db.scalars(select(FileChunk).where(FileChunk.file_id == asset.id)).all() == []
    assert not stored_path.exists()
