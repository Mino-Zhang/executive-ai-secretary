from __future__ import annotations

import hashlib
import io
import os
import re
import secrets
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .errors import AppError

MAGIC_V1 = b"EAIF1"
MAGIC_V2 = b"EAIF2"
NONCE_SIZE = 12
TAG_SIZE = 16
CHUNK_SIZE = 1024 * 1024
KEY_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


@dataclass(frozen=True)
class StoredObject:
    storage_key: str
    size_bytes: int
    sha256: str
    encryption_key_version: str


@dataclass(frozen=True)
class ReencryptionResult:
    storage_key: str
    source_key_version: str
    target_key_version: str
    rewritten: bool
    size_bytes: int
    sha256: str


class PrivateFileStorage(Protocol):
    def put(self, source: BinaryIO, max_bytes: int) -> StoredObject: ...

    def open_decrypted(self, storage_key: str, expected_key_version: str) -> bytes: ...

    def delete(self, storage_key: str) -> None: ...


class LocalEncryptedStorage:
    """Versioned AES-256-GCM storage with legacy EAIF1 read compatibility."""

    def __init__(
        self,
        root: Path,
        key: bytes | None = None,
        *,
        current_key_version: str = "v1",
        key_ring: dict[str, bytes] | None = None,
    ) -> None:
        self._validate_version(current_key_version)
        keys = dict(key_ring or {})
        if key is not None:
            existing = keys.get(current_key_version)
            if existing is not None and existing != key:
                raise ValueError("current key conflicts with the same key-ring version")
            keys[current_key_version] = key
        if current_key_version not in keys:
            raise ValueError("current encryption key version is missing from the key ring")
        for version, value in keys.items():
            self._validate_version(version)
            if len(value) != 32:
                raise ValueError("AES-256 keys must contain exactly 32 bytes")
        self.root = root.resolve()
        self.current_key_version = current_key_version
        self._keys = keys
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    @staticmethod
    def _validate_version(version: str) -> None:
        if not KEY_VERSION_PATTERN.fullmatch(version):
            raise ValueError("invalid encryption key version")

    def _key_for(self, version: str) -> bytes:
        key = self._keys.get(version)
        if key is None:
            raise AppError(
                500,
                "file_key_unavailable",
                "文件所需的历史解密密钥未配置",
            )
        return key

    def _path(self, storage_key: str) -> Path:
        candidate = (self.root / storage_key).resolve()
        if self.root not in candidate.parents:
            raise AppError(400, "invalid_storage_key", "文件存储标识无效")
        return candidate

    @staticmethod
    def _aad(storage_key: str, version: str, *, legacy: bool) -> bytes:
        if legacy:
            return storage_key.encode()
        return b"EAIF2\x00" + storage_key.encode() + b"\x00" + version.encode("ascii")

    @staticmethod
    def _v2_header(version: str, nonce: bytes) -> bytes:
        encoded_version = version.encode("ascii")
        return MAGIC_V2 + bytes([len(encoded_version)]) + encoded_version + nonce

    def _write_v2(
        self,
        source: BinaryIO,
        target: Path,
        storage_key: str,
        version: str,
        max_bytes: int,
    ) -> tuple[int, str]:
        self._validate_version(version)
        key = self._key_for(version)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = target.with_suffix(f".tmp-{secrets.token_hex(8)}")
        nonce = os.urandom(NONCE_SIZE)
        encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
        encryptor.authenticate_additional_data(self._aad(storage_key, version, legacy=False))
        digest = hashlib.sha256()
        size = 0
        try:
            with temporary.open("wb") as output:
                output.write(self._v2_header(version, nonce))
                while chunk := source.read(CHUNK_SIZE):
                    size += len(chunk)
                    if size > max_bytes:
                        raise AppError(413, "file_too_large", "文件超过允许的大小")
                    digest.update(chunk)
                    output.write(encryptor.update(chunk))
                output.write(encryptor.finalize())
                output.write(encryptor.tag)
                output.flush()
                os.fsync(output.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, target)
            directory_fd = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        return size, digest.hexdigest()

    def put(self, source: BinaryIO, max_bytes: int) -> StoredObject:
        object_id = uuid.uuid4().hex
        storage_key = f"{object_id[:2]}/{object_id[2:4]}/{object_id}.bin"
        target = self._path(storage_key)
        size, digest = self._write_v2(
            source,
            target,
            storage_key,
            self.current_key_version,
            max_bytes,
        )
        return StoredObject(
            storage_key=storage_key,
            size_bytes=size,
            sha256=digest,
            encryption_key_version=self.current_key_version,
        )

    def _read_encrypted(self, storage_key: str) -> bytes:
        path = self._path(storage_key)
        if not path.is_file():
            raise AppError(404, "file_content_missing", "文件内容不存在")
        return path.read_bytes()

    def inspect_key_version(
        self,
        storage_key: str,
        *,
        legacy_key_version: str | None = None,
    ) -> str:
        encrypted = self._read_encrypted(storage_key)
        if encrypted.startswith(MAGIC_V1):
            if legacy_key_version is None:
                raise AppError(500, "file_key_version_missing", "旧版文件缺少密钥版本元数据")
            self._validate_version(legacy_key_version)
            return legacy_key_version
        if not encrypted.startswith(MAGIC_V2) or len(encrypted) < len(MAGIC_V2) + 1:
            raise AppError(500, "file_integrity_error", "文件存储格式无效")
        version_length = encrypted[len(MAGIC_V2)]
        header_length = len(MAGIC_V2) + 1 + version_length + NONCE_SIZE
        if version_length == 0 or len(encrypted) < header_length + TAG_SIZE:
            raise AppError(500, "file_integrity_error", "文件存储格式无效")
        try:
            version = encrypted[len(MAGIC_V2) + 1 : len(MAGIC_V2) + 1 + version_length].decode(
                "ascii"
            )
        except UnicodeDecodeError as exc:
            raise AppError(500, "file_integrity_error", "文件密钥版本标识无效") from exc
        try:
            self._validate_version(version)
        except ValueError as exc:
            raise AppError(500, "file_integrity_error", "文件密钥版本标识无效") from exc
        return version

    def open_decrypted(self, storage_key: str, expected_key_version: str) -> bytes:
        self._validate_version(expected_key_version)
        encrypted = self._read_encrypted(storage_key)
        legacy = encrypted.startswith(MAGIC_V1)
        if legacy:
            version = expected_key_version
            header_length = len(MAGIC_V1) + NONCE_SIZE
            if len(encrypted) < header_length + TAG_SIZE:
                raise AppError(500, "file_integrity_error", "文件存储格式无效")
            nonce = encrypted[len(MAGIC_V1) : header_length]
        elif encrypted.startswith(MAGIC_V2):
            version_length = encrypted[len(MAGIC_V2)] if len(encrypted) > len(MAGIC_V2) else 0
            header_length = len(MAGIC_V2) + 1 + version_length + NONCE_SIZE
            if version_length == 0 or len(encrypted) < header_length + TAG_SIZE:
                raise AppError(500, "file_integrity_error", "文件存储格式无效")
            try:
                version = encrypted[len(MAGIC_V2) + 1 : len(MAGIC_V2) + 1 + version_length].decode(
                    "ascii"
                )
                self._validate_version(version)
            except (UnicodeDecodeError, ValueError) as exc:
                raise AppError(500, "file_integrity_error", "文件密钥版本标识无效") from exc
            if version != expected_key_version:
                raise AppError(500, "file_key_version_mismatch", "文件密钥版本与数据库记录不一致")
            nonce = encrypted[header_length - NONCE_SIZE : header_length]
        else:
            raise AppError(500, "file_integrity_error", "文件存储格式无效")
        ciphertext = encrypted[header_length:-TAG_SIZE]
        tag = encrypted[-TAG_SIZE:]
        decryptor = Cipher(
            algorithms.AES(self._key_for(version)), modes.GCM(nonce, tag)
        ).decryptor()
        decryptor.authenticate_additional_data(self._aad(storage_key, version, legacy=legacy))
        try:
            return decryptor.update(ciphertext) + decryptor.finalize()
        except InvalidTag as exc:
            raise AppError(500, "file_integrity_error", "文件完整性校验失败") from exc

    def reencrypt_atomic(
        self,
        storage_key: str,
        *,
        source_key_version: str,
        target_key_version: str,
        expected_size_bytes: int,
        expected_sha256: str,
    ) -> ReencryptionResult:
        if source_key_version == target_key_version:
            raise ValueError("source and target key versions must differ")
        self._key_for(source_key_version)
        self._key_for(target_key_version)
        actual_version = self.verify_integrity(
            storage_key,
            database_key_version=source_key_version,
            expected_size_bytes=expected_size_bytes,
            expected_sha256=expected_sha256,
            allowed_embedded_versions={source_key_version, target_key_version},
        )
        if actual_version not in {source_key_version, target_key_version}:
            raise AppError(
                409,
                "file_rotation_version_conflict",
                "文件当前密钥版本不属于本次轮换范围",
            )
        plaintext = self.open_decrypted(storage_key, actual_version)
        rewritten = actual_version == source_key_version
        if rewritten:
            size, rewritten_digest = self._write_v2(
                io.BytesIO(plaintext),
                self._path(storage_key),
                storage_key,
                target_key_version,
                expected_size_bytes,
            )
            if size != expected_size_bytes or rewritten_digest != expected_sha256:
                raise AppError(500, "file_integrity_error", "轮换写入后的文件校验失败")
        verified = self.open_decrypted(storage_key, target_key_version)
        if (
            len(verified) != expected_size_bytes
            or hashlib.sha256(verified).hexdigest() != expected_sha256
        ):
            raise AppError(500, "file_integrity_error", "轮换后的文件校验失败")
        return ReencryptionResult(
            storage_key=storage_key,
            source_key_version=source_key_version,
            target_key_version=target_key_version,
            rewritten=rewritten,
            size_bytes=expected_size_bytes,
            sha256=expected_sha256,
        )

    def verify_integrity(
        self,
        storage_key: str,
        *,
        database_key_version: str,
        expected_size_bytes: int,
        expected_sha256: str,
        allowed_embedded_versions: set[str] | None = None,
    ) -> str:
        actual_version = self.inspect_key_version(
            storage_key,
            legacy_key_version=database_key_version,
        )
        if (
            allowed_embedded_versions is not None
            and actual_version not in allowed_embedded_versions
        ):
            raise AppError(
                409,
                "file_rotation_version_conflict",
                "文件当前密钥版本不属于本次轮换范围",
            )
        plaintext = self.open_decrypted(storage_key, actual_version)
        if (
            len(plaintext) != expected_size_bytes
            or hashlib.sha256(plaintext).hexdigest() != expected_sha256
        ):
            raise AppError(500, "file_integrity_error", "文件大小或摘要校验失败")
        return actual_version

    def delete(self, storage_key: str) -> None:
        self._path(storage_key).unlink(missing_ok=True)

    def self_test(self) -> None:
        """Prove that the mounted store is writable and the current key can round-trip."""
        plaintext = os.urandom(32)
        stored = self.put(io.BytesIO(plaintext), 1024)
        try:
            if self.open_decrypted(stored.storage_key, stored.encryption_key_version) != plaintext:
                raise RuntimeError("encrypted storage round-trip mismatch")
        finally:
            self.delete(stored.storage_key)
