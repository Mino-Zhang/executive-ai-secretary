from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel):
    items: list[Any]
    next_cursor: str | None = None


class UserOut(ORMModel):
    id: uuid.UUID
    email: str
    display_name: str
    preferred_name: str | None
    role: str
    locale: str
    timezone: str
    password_change_required: bool


class EnterpriseOut(ORMModel):
    id: uuid.UUID
    name: str
    slug: str


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if (
            normalized.count("@") != 1
            or normalized.startswith("@")
            or normalized.endswith("@")
        ):
            raise ValueError("invalid email")
        return normalized


class LoginResponse(BaseModel):
    user: UserOut
    csrf_token: str
    expires_at: datetime
    app_env: str
    app_mode: str


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=10, max_length=256)


class MeResponse(BaseModel):
    user: UserOut
    enterprise: EnterpriseOut
    scopes: list[OrganizationUnitOut]
    csrf_token: str
    app_env: str
    app_mode: str


class SessionOut(ORMModel):
    id: uuid.UUID
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    ip_address: str | None
    user_agent: str | None
    is_current: bool = False


class OrganizationUnitOut(ORMModel):
    id: uuid.UUID
    name: str
    code: str
    parent_id: uuid.UUID | None
    unit_type: str
    enabled_for_analysis: bool
    data_connected: bool
    sort_order: int


class OrganizationUnitCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    code: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9._-]+$")
    parent_id: uuid.UUID | None = None
    unit_type: str = Field(default="division", max_length=40)
    enabled_for_analysis: bool = False
    data_connected: bool = False
    sort_order: int = 0


class OrganizationUnitUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    parent_id: uuid.UUID | None = None
    enabled_for_analysis: bool | None = None
    data_connected: bool | None = None
    sort_order: int | None = None
    is_active: bool | None = None


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    organization_unit_id: uuid.UUID | None = None


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    organization_unit_id: uuid.UUID | None = None


class ProjectOut(ORMModel):
    id: uuid.UUID
    name: str
    description: str | None
    organization_unit_id: uuid.UUID | None
    pinned_at: datetime | None
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ConversationCreate(BaseModel):
    title: str = Field(default="新会话", min_length=1, max_length=300)
    organization_unit_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None


class ConversationUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    organization_unit_id: uuid.UUID | None = None
    status: Literal["active", "archived"] | None = None


class ConversationOut(ORMModel):
    id: uuid.UUID
    title: str
    organization_unit_id: uuid.UUID | None
    status: str
    pinned_at: datetime | None
    archived_at: datetime | None
    last_message_at: datetime | None
    created_at: datetime
    updated_at: datetime


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=12000)
    file_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)


class MessageOut(ORMModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    role: str
    content: str
    content_json: dict[str, Any]
    sequence: int
    status: str
    model_name: str | None
    source_data_as_of: datetime | None
    created_at: datetime


class FileOut(ORMModel):
    id: uuid.UUID
    original_name: str
    media_type: str
    size_bytes: int
    sha256: str
    encryption_key_version: str
    status: str
    created_at: datetime
    deleted_at: datetime | None


class MemoryCreate(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    content: str = Field(min_length=1, max_length=20000)
    kind: str = Field(default="preference", max_length=50)
    organization_unit_id: uuid.UUID | None = None
    source_conversation_id: uuid.UUID | None = None


class MemoryUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=240)
    content: str | None = Field(default=None, min_length=1, max_length=20000)
    status: Literal["active", "disabled", "deleted"] | None = None


class MemoryOut(ORMModel):
    id: uuid.UUID
    title: str
    content: str
    kind: str
    organization_unit_id: uuid.UUID | None
    source_conversation_id: uuid.UUID | None
    status: str
    version: int
    created_at: datetime
    updated_at: datetime


class ReportOut(ORMModel):
    id: uuid.UUID
    kind: str
    title: str
    status: str
    organization_unit_id: uuid.UUID | None
    period_start: date
    period_end: date
    data_as_of: datetime | None
    published_at: datetime | None
    created_at: datetime
    latest_version: int | None = None
    content: dict[str, Any] | None = None


class JobCreate(BaseModel):
    job_type: str = Field(min_length=1, max_length=80)
    payload: dict[str, Any] = Field(default_factory=dict)
    scheduled_at: datetime | None = None


class JobOut(ORMModel):
    id: uuid.UUID
    job_type: str
    status: str
    payload_json: dict[str, Any]
    scope_snapshot_json: dict[str, Any]
    result_json: dict[str, Any]
    error_code: str | None
    error_message: str | None
    attempt_count: int
    max_attempts: int
    scheduled_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    dead_lettered_at: datetime | None
    created_at: datetime


class UserCreate(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    display_name: str = Field(min_length=1, max_length=160)
    preferred_name: str | None = Field(default=None, max_length=100)
    role: Literal["executive", "enterprise_admin", "fde"] = "executive"
    temporary_password: str = Field(min_length=10, max_length=256)
    organization_unit_ids: list[uuid.UUID] = Field(default_factory=list)
    enterprise_wide_scope: bool = False

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if (
            normalized.count("@") != 1
            or normalized.startswith("@")
            or normalized.endswith("@")
        ):
            raise ValueError("invalid email")
        return normalized


class UserUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=160)
    preferred_name: str | None = Field(default=None, max_length=100)
    role: Literal["executive", "enterprise_admin", "fde"] | None = None
    is_active: bool | None = None
    locale: Literal["zh-CN", "zh-TW", "en-US"] | None = None
    timezone: str | None = Field(default=None, max_length=64)


class TemporaryPasswordRequest(BaseModel):
    temporary_password: str = Field(min_length=10, max_length=256)


class DataScopeUpdate(BaseModel):
    enterprise_wide_scope: bool = False
    organization_unit_ids: list[uuid.UUID] = Field(default_factory=list)


class AuditEventOut(ORMModel):
    id: uuid.UUID
    actor_user_id: uuid.UUID | None
    action: str
    target_type: str | None
    target_id: str | None
    outcome: str
    environment: str
    actor_role: str | None
    failure_reason_code: str | None
    request_id: str | None
    metadata_json: dict[str, Any]
    scope_summary_json: dict[str, Any]
    chain_scope: str | None
    chain_sequence: int | None
    previous_integrity_hash: str | None
    integrity_hash: str
    created_at: datetime


class AuditVerification(BaseModel):
    valid: bool
    checked_count: int
    invalid_event_ids: list[uuid.UUID]
    errors: list[str] = Field(default_factory=list)


class RuntimeStatus(BaseModel):
    app_env: str
    app_mode: str
    version: str
    database: str
    storage: str
    demo_data_enabled: bool
