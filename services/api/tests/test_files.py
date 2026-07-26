from __future__ import annotations

from pathlib import Path

from executive_ai_api.config import get_settings

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
    file_id = upload.json()["id"]
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
