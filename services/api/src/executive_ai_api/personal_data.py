from __future__ import annotations

import base64
import json
import os
import uuid
from dataclasses import dataclass
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import Settings
from .errors import AppError
from .models import ExecutivePersonalProfile, Memory


@dataclass(frozen=True)
class EncryptedPersonalValue:
    ciphertext: str
    nonce: str
    key_version: str


def _aad(
    *,
    enterprise_id: uuid.UUID,
    user_id: uuid.UUID,
    record_id: uuid.UUID,
    kind: str,
    key_version: str,
) -> bytes:
    return (
        f"executive-ai-personal\x00{enterprise_id}\x00{user_id}\x00"
        f"{record_id}\x00{kind}\x00{key_version}"
    ).encode()


def encrypt_personal_text(
    value: str,
    *,
    enterprise_id: uuid.UUID,
    user_id: uuid.UUID,
    record_id: uuid.UUID,
    kind: str,
    settings: Settings,
) -> EncryptedPersonalValue:
    version = settings.integration_encryption_key_version
    nonce = os.urandom(12)
    ciphertext = AESGCM(settings.integration_encryption_keys()[version]).encrypt(
        nonce,
        value.encode("utf-8"),
        _aad(
            enterprise_id=enterprise_id,
            user_id=user_id,
            record_id=record_id,
            kind=kind,
            key_version=version,
        ),
    )
    return EncryptedPersonalValue(
        ciphertext=base64.urlsafe_b64encode(ciphertext).decode("ascii"),
        nonce=base64.urlsafe_b64encode(nonce).decode("ascii"),
        key_version=version,
    )


def decrypt_personal_text(
    *,
    ciphertext: str,
    nonce: str,
    key_version: str,
    enterprise_id: uuid.UUID,
    user_id: uuid.UUID,
    record_id: uuid.UUID,
    kind: str,
    settings: Settings,
) -> str:
    key = settings.integration_encryption_keys().get(key_version)
    if key is None:
        raise AppError(
            503,
            "personal_key_unavailable",
            "个人数据所需的历史加密密钥未加载",
        )
    try:
        plaintext = AESGCM(key).decrypt(
            base64.urlsafe_b64decode(nonce.encode("ascii")),
            base64.urlsafe_b64decode(ciphertext.encode("ascii")),
            _aad(
                enterprise_id=enterprise_id,
                user_id=user_id,
                record_id=record_id,
                kind=kind,
                key_version=key_version,
            ),
        )
        return plaintext.decode("utf-8")
    except (InvalidTag, ValueError, UnicodeError) as exc:
        raise AppError(
            500,
            "personal_data_integrity_error",
            "个人数据完整性校验失败",
        ) from exc


def encrypt_profile_payload(
    payload: dict[str, Any],
    *,
    profile: ExecutivePersonalProfile,
    settings: Settings,
) -> None:
    encrypted = encrypt_personal_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        enterprise_id=profile.enterprise_id,
        user_id=profile.user_id,
        record_id=profile.id,
        kind="profile",
        settings=settings,
    )
    profile.profile_ciphertext = encrypted.ciphertext
    profile.profile_nonce = encrypted.nonce
    profile.encryption_key_version = encrypted.key_version


def decrypt_profile_payload(
    profile: ExecutivePersonalProfile,
    settings: Settings,
) -> dict[str, Any]:
    raw = decrypt_personal_text(
        ciphertext=profile.profile_ciphertext,
        nonce=profile.profile_nonce,
        key_version=profile.encryption_key_version,
        enterprise_id=profile.enterprise_id,
        user_id=profile.user_id,
        record_id=profile.id,
        kind="profile",
        settings=settings,
    )
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AppError(500, "personal_data_invalid", "个人配置无法解析") from exc
    if not isinstance(value, dict):
        raise AppError(500, "personal_data_invalid", "个人配置格式无效")
    return value


def set_memory_content(memory: Memory, content: str, settings: Settings) -> None:
    encrypted = encrypt_personal_text(
        content,
        enterprise_id=memory.enterprise_id,
        user_id=memory.user_id,
        record_id=memory.id,
        kind="memory",
        settings=settings,
    )
    memory.content_ciphertext = encrypted.ciphertext
    memory.content_nonce = encrypted.nonce
    memory.encryption_key_version = encrypted.key_version
    # Keep the legacy column only for one release; never persist new plaintext.
    memory.content = ""


def get_memory_content(memory: Memory, settings: Settings) -> str:
    if (
        memory.content_ciphertext
        and memory.content_nonce
        and memory.encryption_key_version
    ):
        return decrypt_personal_text(
            ciphertext=memory.content_ciphertext,
            nonce=memory.content_nonce,
            key_version=memory.encryption_key_version,
            enterprise_id=memory.enterprise_id,
            user_id=memory.user_id,
            record_id=memory.id,
            kind="memory",
            settings=settings,
        )
    # Existing records are lazily migrated by the owner-facing API or worker.
    return memory.content


def ensure_memory_encrypted(memory: Memory, settings: Settings) -> str:
    content = get_memory_content(memory, settings)
    if not memory.content_ciphertext:
        set_memory_content(memory, content, settings)
    return content
