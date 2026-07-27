from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    event,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from .database import Base

JSONType = JSON().with_variant(JSONB(), "postgresql")


def new_uuid() -> uuid.UUID:
    return uuid.uuid4()


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class UUIDMixin:
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)


class Enterprise(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "enterprises"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    settings_json: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)


class OrganizationUnit(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "organization_units"
    __table_args__ = (
        UniqueConstraint("enterprise_id", "code", name="uq_org_unit_enterprise_code"),
        Index("ix_org_unit_enterprise_parent", "enterprise_id", "parent_id"),
    )

    enterprise_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("enterprises.id", ondelete="CASCADE"), nullable=False, index=True
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organization_units.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    unit_type: Mapped[str] = mapped_column(String(40), default="division", nullable=False)
    enabled_for_analysis: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    data_connected: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    config_json: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)

    parent: Mapped[OrganizationUnit | None] = relationship(remote_side="OrganizationUnit.id")


class User(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("email", name="uq_users_email"),
        Index("ix_users_enterprise_role", "enterprise_id", "role"),
    )

    enterprise_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("enterprises.id", ondelete="CASCADE"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    preferred_name: Mapped[str | None] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="executive")
    locale: Mapped[str] = mapped_column(String(20), default="zh-CN", nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Shanghai", nullable=False)
    memory_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    password_change_required: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    credential: Mapped[UserCredential | None] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )


class UserCredential(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "user_credentials"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    user: Mapped[User] = relationship(back_populates="credential")


class UserSession(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "user_sessions"
    __table_args__ = (Index("ix_session_user_active", "user_id", "revoked_at", "expires_at"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    csrf_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    idle_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ip_address: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(500))


class DataScopeGrant(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "data_scope_grants"
    __table_args__ = (
        UniqueConstraint("user_id", "scope_kind", "organization_unit_id", name="uq_user_scope"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    scope_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="organization_unit")
    organization_unit_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organization_units.id", ondelete="CASCADE"), nullable=True
    )
    can_read: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Project(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "projects"
    __table_args__ = (Index("ix_project_enterprise_owner", "enterprise_id", "owner_user_id"),)

    enterprise_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("enterprises.id", ondelete="CASCADE"), nullable=False, index=True
    )
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    organization_unit_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organization_units.id", ondelete="SET NULL")
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    pinned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)


class Conversation(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "conversations"
    __table_args__ = (
        Index("ix_conversation_enterprise_owner", "enterprise_id", "owner_user_id"),
        Index("ix_conversation_enterprise_org", "enterprise_id", "organization_unit_id"),
    )

    enterprise_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("enterprises.id", ondelete="CASCADE"), nullable=False, index=True
    )
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    organization_unit_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organization_units.id", ondelete="SET NULL")
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False, default="新会话")
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    pinned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)


class ProjectConversation(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "project_conversations"
    __table_args__ = (
        UniqueConstraint("project_id", "conversation_id", name="uq_project_conversation"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )


class Message(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("conversation_id", "sequence", name="uq_message_conversation_sequence"),
        Index("ix_message_conversation_created", "conversation_id", "created_at"),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    author_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    role: Mapped[str] = mapped_column(String(24), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_json: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="completed", nullable=False)
    model_name: Mapped[str | None] = mapped_column(String(160))
    source_data_as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MessageRun(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "message_runs"

    message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(24), default="queued", nullable=False)
    provider: Mapped[str | None] = mapped_column(String(80))
    model_name: Mapped[str | None] = mapped_column(String(160))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)


class FileAsset(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "files"
    __table_args__ = (Index("ix_file_enterprise_uploader", "enterprise_id", "uploaded_by_user_id"),)

    enterprise_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("enterprises.id", ondelete="CASCADE"), nullable=False, index=True
    )
    uploaded_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    storage_key: Mapped[str] = mapped_column(String(500), unique=True, nullable=False)
    original_name: Mapped[str] = mapped_column(String(500), nullable=False)
    media_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    encryption_algorithm: Mapped[str] = mapped_column(
        String(40), default="AES-256-GCM", nullable=False
    )
    encryption_key_version: Mapped[str] = mapped_column(String(64), default="v1", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="ready", nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)


class ConversationFile(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "conversation_files"
    __table_args__ = (UniqueConstraint("conversation_id", "file_id", name="uq_conversation_file"),)

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    file_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("files.id", ondelete="CASCADE"), nullable=False
    )


class FileEvent(UUIDMixin, Base):
    __tablename__ = "file_events"

    file_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("files.id", ondelete="CASCADE"), nullable=False, index=True
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class FileExtraction(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "file_extractions"
    __table_args__ = (
        UniqueConstraint("file_id", name="uq_file_extraction_file"),
        Index("ix_file_extraction_status", "status", "updated_at"),
    )

    file_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("files.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="queued", nullable=False)
    parser_name: Mapped[str | None] = mapped_column(String(80))
    parser_version: Mapped[str | None] = mapped_column(String(40))
    page_count: Mapped[int | None] = mapped_column(Integer)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    character_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)


class FileChunk(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "file_chunks"
    __table_args__ = (
        UniqueConstraint("extraction_id", "chunk_index", name="uq_file_chunk_index"),
        Index("ix_file_chunk_file", "file_id", "chunk_index"),
        Index(
            "ix_file_chunk_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    extraction_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("file_extractions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    file_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("files.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    locator_json: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(512))


class Memory(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "memories"
    __table_args__ = (Index("ix_memory_enterprise_user", "enterprise_id", "user_id"),)

    enterprise_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("enterprises.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    organization_unit_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organization_units.id", ondelete="SET NULL")
    )
    source_conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL")
    )
    kind: Mapped[str] = mapped_column(String(50), default="preference", nullable=False)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="active", nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MemoryEvent(UUIDMixin, Base):
    __tablename__ = "memory_events"

    memory_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("memories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    previous_content: Mapped[str | None] = mapped_column(Text)
    new_content: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Report(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "reports"
    __table_args__ = (
        Index("ix_report_enterprise_kind_period", "enterprise_id", "kind", "period_start"),
    )

    enterprise_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("enterprises.id", ondelete="CASCADE"), nullable=False
    )
    organization_unit_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organization_units.id", ondelete="SET NULL")
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="draft", nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    data_as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReportVersion(UUIDMixin, Base):
    __tablename__ = "report_versions"
    __table_args__ = (UniqueConstraint("report_id", "version", name="uq_report_version"),)

    report_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("reports.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_json: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    source_summary: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Job(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_job_status_schedule", "status", "scheduled_at"),
        Index("ix_job_status_lease", "status", "lease_expires_at"),
    )

    enterprise_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("enterprises.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    job_type: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="queued", nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    scope_snapshot_json: Mapped[dict[str, Any]] = mapped_column(
        JSONType, default=dict, nullable=False
    )
    result_json: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_owner: Mapped[str | None] = mapped_column(String(160))
    lease_token: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dead_lettered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class JobAttempt(UUIDMixin, Base):
    __tablename__ = "job_attempts"
    __table_args__ = (
        UniqueConstraint("job_id", "attempt", name="uq_job_attempt_number"),
        Index(
            "uq_job_single_running_attempt",
            "job_id",
            unique=True,
            postgresql_where=text("status = 'running'"),
            sqlite_where=text("status = 'running'"),
        ),
    )

    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    worker_id: Mapped[str] = mapped_column(String(160), nullable=False)
    lease_token: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)


class AppConfig(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "app_configs"
    __table_args__ = (
        UniqueConstraint("enterprise_id", "key", name="uq_app_config_enterprise_key"),
    )

    enterprise_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("enterprises.id", ondelete="CASCADE")
    )
    key: Mapped[str] = mapped_column(String(200), nullable=False)
    value_json: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    is_secret_reference: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class SecretReference(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "secret_references"
    __table_args__ = (
        UniqueConstraint("enterprise_id", "key", name="uq_secret_ref_enterprise_key"),
    )

    enterprise_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("enterprises.id", ondelete="CASCADE")
    )
    key: Mapped[str] = mapped_column(String(200), nullable=False)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    reference: Mapped[str] = mapped_column(String(500), nullable=False)


class ModelProviderConfig(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "model_provider_configs"
    __table_args__ = (
        UniqueConstraint("enterprise_id", name="uq_model_provider_enterprise"),
        Index("ix_model_provider_enterprise_enabled", "enterprise_id", "is_enabled"),
    )

    enterprise_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("enterprises.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(32), default="anspire", nullable=False)
    endpoint_url: Mapped[str] = mapped_column(String(300), nullable=False)
    model_id: Mapped[str] = mapped_column(String(100), nullable=False)
    api_key_ciphertext: Mapped[str | None] = mapped_column(Text)
    api_key_nonce: Mapped[str | None] = mapped_column(String(64))
    api_key_hint: Mapped[str | None] = mapped_column(String(16))
    encryption_key_version: Mapped[str] = mapped_column(String(64), default="v1", nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_test_status: Mapped[str | None] = mapped_column(String(32))
    last_test_latency_ms: Mapped[int | None] = mapped_column(Integer)
    last_test_error: Mapped[str | None] = mapped_column(Text)
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )


class McpToolConfig(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "mcp_tool_configs"
    __table_args__ = (
        UniqueConstraint("enterprise_id", "tool_name", name="uq_mcp_tool_enterprise_name"),
        Index("ix_mcp_tool_enterprise_enabled", "enterprise_id", "is_enabled"),
    )

    enterprise_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("enterprises.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    planner_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=20, nullable=False)
    max_rows: Mapped[int] = mapped_column(Integer, default=50, nullable=False)
    operator_note: Mapped[str | None] = mapped_column(String(500))
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )


class DataSource(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "data_sources"
    __table_args__ = (
        UniqueConstraint("enterprise_id", "key", name="uq_data_source_enterprise_key"),
        Index("ix_data_source_enterprise_enabled", "enterprise_id", "is_enabled"),
    )

    enterprise_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("enterprises.id", ondelete="CASCADE"), nullable=False, index=True
    )
    key: Mapped[str] = mapped_column(String(100), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(32), default="2.0", nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    configuration_json: Mapped[dict[str, Any]] = mapped_column(
        JSONType, default=dict, nullable=False
    )
    secret_reference_key: Mapped[str | None] = mapped_column(String(200))
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_test_status: Mapped[str | None] = mapped_column(String(32))
    last_test_error: Mapped[str | None] = mapped_column(Text)


class ScheduledTask(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "scheduled_tasks"
    __table_args__ = (
        UniqueConstraint("enterprise_id", "key", name="uq_scheduled_task_enterprise_key"),
        Index("ix_scheduled_task_due", "is_enabled", "next_run_at"),
    )

    enterprise_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("enterprises.id", ondelete="CASCADE"), nullable=False, index=True
    )
    data_source_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("data_sources.id", ondelete="CASCADE"), index=True
    )
    key: Mapped[str] = mapped_column(String(120), nullable=False)
    task_type: Mapped[str] = mapped_column(String(80), nullable=False)
    cron_expression: Mapped[str] = mapped_column(String(80), default="0 2 * * *", nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Shanghai", nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_enqueued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    configuration_json: Mapped[dict[str, Any]] = mapped_column(
        JSONType, default=dict, nullable=False
    )


class ScheduleRun(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "schedule_runs"
    __table_args__ = (
        UniqueConstraint("scheduled_task_id", "window_key", name="uq_schedule_run_window"),
        Index("ix_schedule_run_status", "status", "created_at"),
    )

    scheduled_task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("scheduled_tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    enterprise_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("enterprises.id", ondelete="CASCADE"), nullable=False, index=True
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), index=True
    )
    window_key: Mapped[str] = mapped_column(String(160), nullable=False)
    trigger_type: Mapped[str] = mapped_column(String(32), default="schedule", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="enqueued", nullable=False)
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    enqueued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)


class DataSyncRun(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "data_sync_runs"
    __table_args__ = (
        Index("ix_data_sync_enterprise_started", "enterprise_id", "started_at"),
        Index("ix_data_sync_source_status", "data_source_id", "status"),
    )

    enterprise_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("enterprises.id", ondelete="CASCADE"), nullable=False, index=True
    )
    data_source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("data_sources.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), index=True
    )
    trigger_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="queued", nullable=False)
    dataset_version: Mapped[str | None] = mapped_column(String(80))
    source_schema_version: Mapped[str | None] = mapped_column(String(32))
    source_batch_id: Mapped[str | None] = mapped_column(String(160))
    source_data_as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    records_read: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    records_written: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    records_rejected: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    domain_results_json: Mapped[dict[str, Any]] = mapped_column(
        JSONType, default=dict, nullable=False
    )
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)


class DataDomainStatus(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "data_domain_status"
    __table_args__ = (
        UniqueConstraint("enterprise_id", "domain", name="uq_data_domain_enterprise"),
        Index("ix_data_domain_status_enterprise", "enterprise_id", "status"),
    )

    enterprise_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("enterprises.id", ondelete="CASCADE"), nullable=False, index=True
    )
    data_source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("data_sources.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    domain: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="never_synced", nullable=False)
    active_sync_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("data_sync_runs.id", ondelete="SET NULL")
    )
    previous_sync_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("data_sync_runs.id", ondelete="SET NULL")
    )
    source_data_as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    record_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    dataset_version: Mapped[str | None] = mapped_column(String(80))
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    last_error_code: Mapped[str | None] = mapped_column(String(100))
    last_error_message: Mapped[str | None] = mapped_column(Text)


class SourceCheckpoint(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "source_checkpoints"
    __table_args__ = (
        UniqueConstraint("data_source_id", "domain", name="uq_source_checkpoint_domain"),
    )

    data_source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("data_sources.id", ondelete="CASCADE"), nullable=False, index=True
    )
    domain: Mapped[str] = mapped_column(String(40), nullable=False)
    cursor_value: Mapped[str | None] = mapped_column(String(500))
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_batch_id: Mapped[str | None] = mapped_column(String(160))
    checksum: Mapped[str | None] = mapped_column(String(64))


class DimPerson(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "dim_person"
    __table_args__ = (
        UniqueConstraint(
            "enterprise_id", "data_source_id", "source_record_id", name="uq_dim_person_source"
        ),
    )

    enterprise_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("enterprises.id", ondelete="CASCADE"), nullable=False, index=True
    )
    data_source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("data_sources.id", ondelete="RESTRICT"), nullable=False
    )
    organization_unit_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organization_units.id", ondelete="SET NULL"), index=True
    )
    source_record_id: Mapped[str] = mapped_column(String(160), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    role_title: Mapped[str | None] = mapped_column(String(160))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    source_system: Mapped[str] = mapped_column(String(80), nullable=False)
    dataset_version: Mapped[str | None] = mapped_column(String(80))
    source_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DimCustomer(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "dim_customer"
    __table_args__ = (
        UniqueConstraint(
            "enterprise_id", "data_source_id", "source_record_id", name="uq_dim_customer_source"
        ),
    )

    enterprise_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("enterprises.id", ondelete="CASCADE"), nullable=False, index=True
    )
    data_source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("data_sources.id", ondelete="RESTRICT"), nullable=False
    )
    organization_unit_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organization_units.id", ondelete="SET NULL"), index=True
    )
    owner_person_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("dim_person.id", ondelete="SET NULL")
    )
    source_record_id: Mapped[str] = mapped_column(String(160), nullable=False)
    display_name: Mapped[str] = mapped_column(String(240), nullable=False)
    industry: Mapped[str | None] = mapped_column(String(120))
    region: Mapped[str | None] = mapped_column(String(120))
    source_system: Mapped[str] = mapped_column(String(80), nullable=False)
    dataset_version: Mapped[str | None] = mapped_column(String(80))
    source_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class FactOpportunity(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "fact_opportunity"
    __table_args__ = (
        UniqueConstraint("sync_run_id", "source_record_id", name="uq_fact_opportunity_run_source"),
        Index(
            "ix_fact_opportunity_scope_current",
            "enterprise_id",
            "organization_unit_id",
            "is_current",
        ),
        Index("ix_fact_opportunity_stage_close", "stage", "expected_close_date"),
    )

    enterprise_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("enterprises.id", ondelete="CASCADE"), nullable=False, index=True
    )
    data_source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("data_sources.id", ondelete="RESTRICT"), nullable=False
    )
    sync_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("data_sync_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    organization_unit_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organization_units.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("dim_customer.id", ondelete="SET NULL")
    )
    owner_person_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("dim_person.id", ondelete="SET NULL")
    )
    source_record_id: Mapped[str] = mapped_column(String(160), nullable=False)
    opportunity_code: Mapped[str] = mapped_column(String(120), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    stage: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    probability: Mapped[int] = mapped_column(Integer, nullable=False)
    expected_amount: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    expected_gross_profit: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    created_date: Mapped[date] = mapped_column(Date, nullable=False)
    expected_close_date: Mapped[date] = mapped_column(Date, nullable=False)
    closed_date: Mapped[date | None] = mapped_column(Date)
    source_system: Mapped[str] = mapped_column(String(80), nullable=False)
    source_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    dataset_version: Mapped[str | None] = mapped_column(String(80))
    is_current: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class FactDelivery(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "fact_delivery"
    __table_args__ = (
        UniqueConstraint("sync_run_id", "source_record_id", name="uq_fact_delivery_run_source"),
        Index(
            "ix_fact_delivery_scope_current", "enterprise_id", "organization_unit_id", "is_current"
        ),
        Index("ix_fact_delivery_risk_status", "risk_level", "status"),
    )

    enterprise_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("enterprises.id", ondelete="CASCADE"), nullable=False, index=True
    )
    data_source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("data_sources.id", ondelete="RESTRICT"), nullable=False
    )
    sync_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("data_sync_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    organization_unit_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organization_units.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("dim_customer.id", ondelete="SET NULL")
    )
    manager_person_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("dim_person.id", ondelete="SET NULL")
    )
    source_record_id: Mapped[str] = mapped_column(String(160), nullable=False)
    opportunity_source_record_id: Mapped[str] = mapped_column(String(160), nullable=False)
    project_code: Mapped[str] = mapped_column(String(120), nullable=False)
    project_name: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(40), nullable=False)
    completion_percent: Mapped[int] = mapped_column(Integer, nullable=False)
    contract_amount: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    gross_margin_rate: Mapped[float] = mapped_column(Numeric(8, 4), nullable=False)
    planned_start_date: Mapped[date] = mapped_column(Date, nullable=False)
    planned_end_date: Mapped[date] = mapped_column(Date, nullable=False)
    actual_end_date: Mapped[date | None] = mapped_column(Date)
    current_milestone: Mapped[str | None] = mapped_column(String(200))
    delay_days: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    source_system: Mapped[str] = mapped_column(String(80), nullable=False)
    source_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    dataset_version: Mapped[str | None] = mapped_column(String(80))
    is_current: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class FactFinanceCollection(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "fact_finance_collection"
    __table_args__ = (
        UniqueConstraint("sync_run_id", "source_record_id", name="uq_fact_collection_run_source"),
        Index(
            "ix_fact_collection_scope_current",
            "enterprise_id",
            "organization_unit_id",
            "is_current",
        ),
        Index("ix_fact_collection_due_status", "planned_collection_date", "status"),
    )

    enterprise_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("enterprises.id", ondelete="CASCADE"), nullable=False, index=True
    )
    data_source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("data_sources.id", ondelete="RESTRICT"), nullable=False
    )
    sync_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("data_sync_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    organization_unit_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organization_units.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("dim_customer.id", ondelete="SET NULL")
    )
    source_record_id: Mapped[str] = mapped_column(String(160), nullable=False)
    project_source_record_id: Mapped[str] = mapped_column(String(160), nullable=False)
    invoice_amount: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    receivable_amount: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    collected_amount: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    outstanding_amount: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    planned_collection_date: Mapped[date] = mapped_column(Date, nullable=False)
    actual_collection_date: Mapped[date | None] = mapped_column(Date)
    overdue_days: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    aging_bucket: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    source_system: Mapped[str] = mapped_column(String(80), nullable=False)
    source_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    dataset_version: Mapped[str | None] = mapped_column(String(80))
    is_current: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class FactTarget(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "fact_target"
    __table_args__ = (
        UniqueConstraint("sync_run_id", "source_record_id", name="uq_fact_target_run_source"),
        Index(
            "ix_fact_target_scope_current", "enterprise_id", "organization_unit_id", "is_current"
        ),
        Index("ix_fact_target_metric_period", "metric_code", "period_start", "period_end"),
    )

    enterprise_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("enterprises.id", ondelete="CASCADE"), nullable=False, index=True
    )
    data_source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("data_sources.id", ondelete="RESTRICT"), nullable=False
    )
    sync_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("data_sync_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    organization_unit_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organization_units.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    source_record_id: Mapped[str] = mapped_column(String(160), nullable=False)
    metric_code: Mapped[str] = mapped_column(String(80), nullable=False)
    metric_name: Mapped[str] = mapped_column(String(160), nullable=False)
    period_type: Mapped[str] = mapped_column(String(32), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    target_value: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    source_system: Mapped[str] = mapped_column(String(80), nullable=False)
    source_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    dataset_version: Mapped[str | None] = mapped_column(String(80))
    is_current: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class DailySnapshot(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "daily_snapshot"
    __table_args__ = (
        UniqueConstraint(
            "enterprise_id",
            "organization_unit_id",
            "snapshot_date",
            name="uq_daily_snapshot_scope_date",
        ),
        Index("ix_daily_snapshot_enterprise_date", "enterprise_id", "snapshot_date"),
    )

    enterprise_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("enterprises.id", ondelete="CASCADE"), nullable=False, index=True
    )
    organization_unit_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organization_units.id", ondelete="CASCADE"), index=True
    )
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False)
    source_data_as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    dataset_version: Mapped[str | None] = mapped_column(String(80))
    metrics_json: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    anomalies_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONType, default=list, nullable=False
    )


class MessageRoute(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "message_routes"
    __table_args__ = (UniqueConstraint("message_id", name="uq_message_route_message"),)

    message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    route: Mapped[str] = mapped_column(String(40), nullable=False)
    profile: Mapped[str] = mapped_column(String(80), nullable=False)
    confidence: Mapped[float] = mapped_column(Numeric(6, 5), nullable=False)
    rewritten_query: Mapped[str] = mapped_column(Text, nullable=False)
    scope_status: Mapped[str] = mapped_column(String(40), nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text)
    model_name: Mapped[str | None] = mapped_column(String(160))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Clarification(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "clarifications"
    __table_args__ = (Index("ix_clarification_conversation_status", "conversation_id", "status"),)

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    options_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONType, default=list, nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    selected_value: Mapped[str | None] = mapped_column(String(500))
    resolved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MessageEvidence(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "message_evidence"
    __table_args__ = (
        UniqueConstraint("message_id", "evidence_key", name="uq_message_evidence_key"),
        Index("ix_message_evidence_message", "message_id", "created_at"),
    )

    message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    evidence_key: Mapped[str] = mapped_column(String(80), nullable=False)
    domain: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    metric_code: Mapped[str | None] = mapped_column(String(100))
    metric_value: Mapped[float | None] = mapped_column(Numeric(24, 6))
    metric_unit: Mapped[str | None] = mapped_column(String(40))
    value_json: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    source_data_as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    dataset_version: Mapped[str | None] = mapped_column(String(80))
    scope_json: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    query_json: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    row_references_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONType, default=list, nullable=False
    )


class AuditChainHead(Base):
    """Mutable, HMAC-protected anchor for one enterprise audit chain."""

    __tablename__ = "audit_chain_heads"

    chain_scope: Mapped[str] = mapped_column(String(64), primary_key=True)
    legacy_event_count: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    legacy_root_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    last_sequence: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    last_integrity_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    anchor_key_version: Mapped[str | None] = mapped_column(String(64))
    anchor_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class AuditEvent(UUIDMixin, Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_enterprise_created", "enterprise_id", "created_at"),
        Index("ix_audit_actor_created", "actor_user_id", "created_at"),
        UniqueConstraint("chain_scope", "chain_sequence", name="uq_audit_chain_sequence"),
    )

    enterprise_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("enterprises.id", ondelete="SET NULL")
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user_sessions.id", ondelete="SET NULL")
    )
    environment: Mapped[str] = mapped_column(String(40), nullable=False)
    actor_role: Mapped[str | None] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(80))
    target_id: Mapped[str | None] = mapped_column(String(100))
    outcome: Mapped[str] = mapped_column(String(24), default="success", nullable=False)
    failure_reason_code: Mapped[str | None] = mapped_column(String(100))
    request_id: Mapped[str | None] = mapped_column(String(80))
    ip_address: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(500))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    scope_summary_json: Mapped[dict[str, Any]] = mapped_column(
        JSONType, default=dict, nullable=False
    )
    chain_scope: Mapped[str | None] = mapped_column(String(64))
    chain_sequence: Mapped[int | None] = mapped_column(BigInteger)
    previous_integrity_hash: Mapped[str | None] = mapped_column(String(64))
    audit_key_version: Mapped[str | None] = mapped_column(String(64))
    integrity_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class IdempotencyRecord(UUIDMixin, Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "method", "path", "idempotency_key", name="uq_idempotency_scope"
        ),
        Index("ix_idempotency_expires", "expires_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    method: Mapped[str] = mapped_column(String(12), nullable=False)
    path: Mapped[str] = mapped_column(String(500), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_json: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


@event.listens_for(AuditEvent, "before_insert")
def sign_audit_event(_mapper, connection, target: AuditEvent) -> None:
    # Imported lazily to keep the model module free of settings initialization cycles.
    from .audit_integrity import prepare_audit_event

    prepare_audit_event(connection, target)
