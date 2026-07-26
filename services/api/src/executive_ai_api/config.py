from __future__ import annotations

import base64
import json
import re
import stat
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

KEY_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        populate_by_name=True,
        extra="ignore",
    )

    app_name: str = "董事长人工智能研究员"
    app_version: str = "0.1.0"
    app_env: Literal["development", "test", "local-demo", "customer-template", "production"] = (
        "development"
    )
    app_mode: Literal["demo", "production"] = "production"
    service_role: Literal["api", "worker", "migration", "bootstrap", "seed"] = "api"
    debug: bool = False
    api_prefix: str = "/api/v1"

    database_url: str = "postgresql+psycopg://executive_ai:executive_ai@localhost:5432/executive_ai"
    database_pool_size: int = Field(default=10, ge=1, le=100)
    database_max_overflow: int = Field(default=20, ge=0, le=100)

    session_secret: SecretStr = Field(
        default=SecretStr("development-only-change-me-32-characters"),
        validation_alias="SESSION_SECRET",
    )
    csrf_secret: SecretStr = Field(
        default=SecretStr("development-only-csrf-secret-change-me"),
        validation_alias="CSRF_SECRET",
    )
    audit_hmac_key: SecretStr = SecretStr("development-only-audit-hmac-key-change-me")
    audit_hmac_key_version: str = "v1"
    audit_hmac_legacy_key_version: str = "v1"
    audit_hmac_key_ring: SecretStr = SecretStr("")
    audit_hmac_key_ring_file: Path | None = None
    file_encryption_key: SecretStr = SecretStr("")
    file_encryption_key_version: str = "v1"
    file_encryption_key_ring: SecretStr = SecretStr("")
    file_encryption_key_ring_file: Path | None = None
    file_storage_root: Path = Field(
        default=Path("./runtime/files"),
        validation_alias=AliasChoices("FILE_STORAGE_ROOT", "STORAGE_ROOT"),
    )
    max_upload_bytes: int = Field(default=50 * 1024 * 1024, ge=1)

    session_cookie_name: str = "exec_session"
    csrf_cookie_name: str = "exec_csrf"
    session_cookie_secure: bool = Field(
        default=False,
        validation_alias=AliasChoices("SESSION_COOKIE_SECURE", "COOKIE_SECURE"),
    )
    session_cookie_samesite: Literal["lax", "strict", "none"] = Field(
        default="lax",
        validation_alias=AliasChoices("SESSION_COOKIE_SAMESITE", "COOKIE_SAMESITE"),
    )
    session_ttl_seconds: int = Field(default=8 * 60 * 60, ge=300, le=30 * 24 * 60 * 60)
    session_idle_seconds: int = Field(default=2 * 60 * 60, ge=300)
    password_min_length: int = Field(default=12, ge=10, le=128)
    login_max_attempts: int = Field(default=8, ge=1, le=100)
    login_window_seconds: int = Field(default=15 * 60, ge=60)

    allowed_origins: Annotated[list[str], NoDecode] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]
    trusted_hosts: Annotated[list[str], NoDecode] = ["localhost", "127.0.0.1", "testserver"]
    seed_demo_data: bool = False
    bootstrap_admin_email: str | None = None
    bootstrap_admin_password: SecretStr | None = None
    log_level: str = "INFO"
    worker_poll_seconds: float = Field(default=2.0, ge=0.1, le=60)
    worker_lease_seconds: int = Field(default=60, ge=5, le=3600)
    worker_heartbeat_seconds: int = Field(default=15, ge=1, le=1800)
    worker_job_max_attempts: int = Field(default=3, ge=1, le=20)
    worker_retry_base_seconds: float = Field(default=2.0, ge=0.1, le=3600)
    worker_retry_max_seconds: float = Field(default=60.0, ge=0.1, le=86_400)

    @field_validator("allowed_origins", "trusted_hosts", mode="before")
    @classmethod
    def split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @field_validator(
        "audit_hmac_key_version",
        "audit_hmac_legacy_key_version",
        "file_encryption_key_version",
    )
    @classmethod
    def validate_key_version(cls, value: str) -> str:
        if not KEY_VERSION_PATTERN.fullmatch(value):
            raise ValueError(
                "key version must use 1-64 letters, numbers, dot, underscore or dash characters"
            )
        return value

    @model_validator(mode="after")
    def validate_environment_guards(self) -> Settings:
        protected = (
            self.app_env in {"customer-template", "production"} or self.app_mode == "production"
        )
        if protected and self.seed_demo_data:
            raise ValueError(
                "Demo seed is forbidden in production and customer-template environments"
            )
        if protected and self.debug:
            raise ValueError("Debug mode is forbidden in protected environments")
        if self.worker_heartbeat_seconds >= self.worker_lease_seconds:
            raise ValueError("WORKER_HEARTBEAT_SECONDS must be shorter than WORKER_LEASE_SECONDS")
        if self.worker_retry_base_seconds > self.worker_retry_max_seconds:
            raise ValueError(
                "WORKER_RETRY_BASE_SECONDS must not exceed WORKER_RETRY_MAX_SECONDS"
            )
        if (
            self.service_role == "api"
            and self.session_cookie_samesite == "none"
            and not self.session_cookie_secure
        ):
            raise ValueError("SameSite=None requires a secure session cookie")
        if protected:
            insecure_values = {
                "development-only-change-me-32-characters",
                "development-only-csrf-secret-change-me",
                "development-only-audit-hmac-key-change-me",
                "change-me",
                "password",
                "demo",
            }
            if self.service_role == "api":
                if (
                    len(self.session_secret.get_secret_value()) < 32
                    or self.session_secret.get_secret_value().lower() in insecure_values
                ):
                    raise ValueError(
                        "SESSION_SECRET must be a non-default value of at least 32 characters"
                    )
                if (
                    len(self.csrf_secret.get_secret_value()) < 32
                    or self.csrf_secret.get_secret_value().lower() in insecure_values
                ):
                    raise ValueError(
                        "CSRF_SECRET must be a non-default value of at least 32 characters"
                    )
            if self.bootstrap_admin_password:
                password = self.bootstrap_admin_password.get_secret_value().lower()
                if password in insecure_values or "demo" in password:
                    raise ValueError("Demo or unsafe bootstrap passwords are forbidden")
            if self.service_role in {"api", "worker", "bootstrap", "seed"}:
                if (
                    len(self.audit_hmac_key.get_secret_value()) < 32
                    or self.audit_hmac_key.get_secret_value().lower() in insecure_values
                ):
                    raise ValueError(
                        "AUDIT_HMAC_KEY must be a non-default value of at least 32 characters"
                    )
                try:
                    self.audit_hmac_keys()
                except RuntimeError as exc:
                    raise ValueError(str(exc)) from exc
            if self.service_role == "api":
                if (
                    len(
                        {
                            self.session_secret.get_secret_value(),
                            self.csrf_secret.get_secret_value(),
                            self.audit_hmac_key.get_secret_value(),
                        }
                    )
                    != 3
                ):
                    raise ValueError(
                        "SESSION_SECRET, CSRF_SECRET and AUDIT_HMAC_KEY must be distinct"
                    )
            if self.service_role in {"api", "worker"}:
                try:
                    self.file_encryption_keys()
                except RuntimeError as exc:
                    raise ValueError(str(exc)) from exc
        return self

    def _load_key_ring(
        self,
        inline: SecretStr,
        file_path: Path | None,
        variable_name: str,
    ) -> dict[str, str]:
        raw_inline = inline.get_secret_value().strip()
        if raw_inline and file_path is not None:
            raise RuntimeError(
                f"{variable_name} and {variable_name}_FILE cannot both be configured"
            )
        if file_path is not None:
            path = file_path.expanduser().resolve()
            if not path.is_file():
                raise RuntimeError(f"{variable_name}_FILE must reference a readable regular file")
            if stat.S_IMODE(path.stat().st_mode) & 0o077:
                raise RuntimeError(f"{variable_name}_FILE must not be readable by group or others")
            raw = path.read_text(encoding="utf-8").strip()
        else:
            raw = raw_inline
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"{variable_name} must be a JSON object") from exc
        if not isinstance(parsed, dict) or len(parsed) > 32:
            raise RuntimeError(f"{variable_name} must be a JSON object with at most 32 keys")
        ring: dict[str, str] = {}
        for version, key in parsed.items():
            if not isinstance(version, str) or not KEY_VERSION_PATTERN.fullmatch(version):
                raise RuntimeError(f"{variable_name} contains an invalid key version")
            if not isinstance(key, str) or not key:
                raise RuntimeError(f"{variable_name} contains an invalid key value")
            ring[version] = key
        return ring

    def decoded_file_encryption_key(self) -> bytes:
        raw = self.file_encryption_key.get_secret_value()
        if not raw:
            if self.app_env == "test":
                return b"T" * 32
            raise RuntimeError("FILE_ENCRYPTION_KEY is required")
        try:
            value = base64.urlsafe_b64decode(raw.encode("ascii"))
        except (ValueError, UnicodeError) as exc:
            raise RuntimeError("FILE_ENCRYPTION_KEY must be URL-safe base64") from exc
        if len(value) != 32:
            raise RuntimeError("FILE_ENCRYPTION_KEY must decode to exactly 32 bytes")
        return value

    def file_encryption_keys(self) -> dict[str, bytes]:
        encoded_ring = self._load_key_ring(
            self.file_encryption_key_ring,
            self.file_encryption_key_ring_file,
            "FILE_ENCRYPTION_KEY_RING",
        )
        decoded: dict[str, bytes] = {}
        for version, encoded in encoded_ring.items():
            try:
                value = base64.urlsafe_b64decode(encoded.encode("ascii"))
            except (ValueError, UnicodeError) as exc:
                raise RuntimeError(
                    f"FILE_ENCRYPTION_KEY_RING key {version!r} must be URL-safe base64"
                ) from exc
            if len(value) != 32:
                raise RuntimeError(
                    f"FILE_ENCRYPTION_KEY_RING key {version!r} must decode to exactly 32 bytes"
                )
            decoded[version] = value
        current = self.decoded_file_encryption_key()
        existing = decoded.get(self.file_encryption_key_version)
        if existing is not None and existing != current:
            raise RuntimeError("current file key conflicts with the same version in the key ring")
        decoded[self.file_encryption_key_version] = current
        return decoded

    def audit_hmac_keys(self) -> dict[str, str]:
        ring = self._load_key_ring(
            self.audit_hmac_key_ring,
            self.audit_hmac_key_ring_file,
            "AUDIT_HMAC_KEY_RING",
        )
        for version, key in ring.items():
            if len(key) < 32:
                raise RuntimeError(
                    f"AUDIT_HMAC_KEY_RING key {version!r} is shorter than 32 characters"
                )
        current = self.audit_hmac_key.get_secret_value()
        existing = ring.get(self.audit_hmac_key_version)
        if existing is not None and existing != current:
            raise RuntimeError("current audit key conflicts with the same version in the key ring")
        ring[self.audit_hmac_key_version] = current
        if self.audit_hmac_legacy_key_version not in ring:
            raise RuntimeError(
                "AUDIT_HMAC_LEGACY_KEY_VERSION is missing from the configured audit key ring"
            )
        return ring


@lru_cache
def get_settings() -> Settings:
    return Settings()
