from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
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
