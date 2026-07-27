from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel):
    items: list[Any]
    next_cursor: str | None = None


class ModelCatalogItem(BaseModel):
    id: str
    name: str
    family: str
    profile: str
    capability: Literal["chat", "image", "video", "embedding", "rerank"]
    selectable: bool


class ModelProviderOut(BaseModel):
    provider: Literal["anspire"] = "anspire"
    endpoint_url: str
    documentation_url: str
    model_id: str
    is_enabled: bool
    is_configured: bool
    api_key_masked: str | None
    last_tested_at: datetime | None
    last_test_status: str | None
    last_test_latency_ms: int | None
    last_test_error: str | None
    models: list[ModelCatalogItem]
    updated_at: datetime | None


class ModelProviderUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_id: str = Field(min_length=1, max_length=100)
    api_key: SecretStr | None = Field(default=None)
    is_enabled: bool | None = None


class ModelProviderTestOut(BaseModel):
    status: Literal["success"] = "success"
    model: str
    latency_ms: int
    tested_at: datetime


class McpToolOut(BaseModel):
    tool_name: str
    display_name: str
    description: str
    category: str
    domains: list[str]
    parameters: dict[str, Any]
    is_enabled: bool
    planner_enabled: bool
    timeout_seconds: int
    max_rows: int
    operator_note: str | None
    configured: bool
    readiness: Literal["ready", "disabled", "data_unavailable"]
    readiness_issues: list[str]
    updated_at: datetime | None


class McpToolCatalogOut(BaseModel):
    tools: list[McpToolOut]
    enabled_count: int
    planner_count: int
    generated_at: datetime


class McpToolUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, min_length=1, max_length=2000)
    is_enabled: bool | None = None
    planner_enabled: bool | None = None
    timeout_seconds: int | None = Field(default=None, ge=3, le=60)
    max_rows: int | None = Field(default=None, ge=1, le=100)
    operator_note: str | None = Field(default=None, max_length=500)


class McpToolValidationOut(BaseModel):
    tool: McpToolOut
    ready: bool
    issues: list[str]


class HarnessConfigOut(BaseModel):
    id: uuid.UUID
    version: int
    schema_version: str
    config_hash: str
    config: dict[str, Any]
    safety_kernel: dict[str, Any]
    activated_at: datetime
    updated_at: datetime


class HarnessConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_version: int = Field(ge=1)
    config: dict[str, Any]


class HarnessVersionOut(BaseModel):
    id: uuid.UUID
    version: int
    config_hash: str
    is_active: bool
    source_version_id: uuid.UUID | None
    created_by_user_id: uuid.UUID | None
    activated_at: datetime
    created_at: datetime


class HarnessSimulationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=12000)
    config: dict[str, Any] | None = None
    organization_scope: dict[str, Any] | None = None


class HarnessSimulationOut(BaseModel):
    route: Literal["data", "general", "clarification"]
    route_source: Literal["fast_rule", "hermes", "validation"]
    matched_rule_id: str | None
    candidate_tools: list[str]
    query_spec: dict[str, Any]
    validation_issues: list[str]
    config_hash: str


class HarnessMetricsOut(BaseModel):
    window_days: int
    message_count: int
    intent_accuracy_sample_size: int
    structured_output_rate: float
    tool_success_rate: float
    route_counts: dict[str, int]
    stage_latency_p95_ms: dict[str, int]


class HarnessTraceOut(BaseModel):
    message_id: uuid.UUID
    conversation_id: uuid.UUID | None = None
    route: str | None
    route_source: str | None
    query_spec_summary: dict[str, Any]
    harness_version: int | None
    organization_unit_count: int
    tools: list[str]
    stages: list[dict[str, Any]]
    diagnostic_shared_until: datetime | None = None
    shared_content: dict[str, Any] | None = None


class UserOut(ORMModel):
    id: uuid.UUID
    email: str
    display_name: str
    preferred_name: str | None
    role: str
    locale: str
    timezone: str
    memory_enabled: bool
    password_change_required: bool


class UserPreferenceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memory_enabled: bool


class ExecutivePersonalProfileOut(BaseModel):
    salutation: str
    amount_unit: Literal["yuan", "wan", "yi"]
    response_style: Literal["concise", "balanced", "detailed"]
    locale: Literal["zh-CN", "zh-TW", "en-US"]
    memory_enabled: bool
    version: int
    updated_at: datetime | None


class ExecutivePersonalProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    salutation: str = Field(min_length=1, max_length=40)
    amount_unit: Literal["yuan", "wan", "yi"] = "wan"
    response_style: Literal["concise", "balanced", "detailed"] = "balanced"
    locale: Literal["zh-CN", "zh-TW", "en-US"] = "zh-CN"
    memory_enabled: bool = True


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
        if normalized.count("@") != 1 or normalized.startswith("@") or normalized.endswith("@"):
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


class OrganizationScopeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["all_authorized", "selected"]
    organization_unit_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_scope(self):
        unique_ids = list(dict.fromkeys(self.organization_unit_ids))
        if len(unique_ids) != len(self.organization_unit_ids):
            raise ValueError("organization_unit_ids must be unique")
        if self.mode == "all_authorized" and self.organization_unit_ids:
            raise ValueError("all_authorized must not include explicit organization units")
        if self.mode == "selected" and not self.organization_unit_ids:
            raise ValueError("selected scope requires at least one organization unit")
        return self


class OrganizationScopeOut(OrganizationScopeInput):
    resolved_organization_unit_ids: list[uuid.UUID] = Field(default_factory=list)


class ConversationCreate(BaseModel):
    title: str = Field(default="新会话", min_length=1, max_length=300)
    organization_unit_id: uuid.UUID | None = None
    organization_scope: OrganizationScopeInput | None = None
    project_id: uuid.UUID | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_mixed_scope_fields(cls, value):
        if (
            isinstance(value, dict)
            and "organization_scope" in value
            and "organization_unit_id" in value
        ):
            raise ValueError("organization_scope and organization_unit_id cannot be used together")
        return value


class ConversationUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    organization_unit_id: uuid.UUID | None = None
    organization_scope: OrganizationScopeInput | None = None
    status: Literal["active", "archived"] | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_mixed_scope_fields(cls, value):
        if (
            isinstance(value, dict)
            and "organization_scope" in value
            and "organization_unit_id" in value
        ):
            raise ValueError("organization_scope and organization_unit_id cannot be used together")
        return value


class ConversationOut(ORMModel):
    id: uuid.UUID
    title: str
    organization_unit_id: uuid.UUID | None
    organization_scope: OrganizationScopeOut
    status: str
    pinned_at: datetime | None
    archived_at: datetime | None
    last_message_at: datetime | None
    created_at: datetime
    updated_at: datetime


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=12000)
    file_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)
    organization_scope: OrganizationScopeInput | None = None


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
    metadata_json: dict[str, Any]
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
        if normalized.count("@") != 1 or normalized.startswith("@") or normalized.endswith("@"):
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


class DataDomainStatusOut(ORMModel):
    domain: str
    status: str
    source_data_as_of: datetime | None
    last_success_at: datetime | None
    record_count: int
    dataset_version: str | None
    source_type: str
    source_display_name: str
    last_error_code: str | None
    last_error_message: str | None


class DataCapabilitiesOut(BaseModel):
    source_kind: str
    source_label: str
    organization_unit_ids: list[uuid.UUID]
    capabilities: dict[str, bool]
    domains: list[DataDomainStatusOut]
    overall_status: str
    generated_at: datetime


class DataSourceOut(ORMModel):
    id: uuid.UUID
    key: str
    display_name: str
    source_type: str
    schema_version: str
    is_enabled: bool
    configuration_json: dict[str, Any]
    last_tested_at: datetime | None
    last_test_status: str | None
    last_test_error: str | None
    created_at: datetime
    updated_at: datetime


class DataSourceUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    is_enabled: bool | None = None
    configuration_json: dict[str, Any] | None = None


class DataSourceTestOut(BaseModel):
    ok: bool
    schema_version: str
    database_version: str
    current_user: str
    read_only: bool
    tls_active: bool
    latest_batch_id: str
    source_data_as_of: datetime
    duration_ms: int


class DataSyncRunOut(ORMModel):
    id: uuid.UUID
    data_source_id: uuid.UUID
    job_id: uuid.UUID | None
    trigger_type: str
    status: str
    dataset_version: str | None
    source_schema_version: str | None
    source_batch_id: str | None
    source_data_as_of: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    records_read: int
    records_written: int
    records_rejected: int
    domain_results_json: dict[str, Any]
    error_code: str | None
    error_message: str | None
    created_at: datetime


class ScheduledTaskOut(ORMModel):
    id: uuid.UUID
    data_source_id: uuid.UUID | None
    key: str
    task_type: str
    cron_expression: str
    timezone: str
    is_enabled: bool
    next_run_at: datetime | None
    last_enqueued_at: datetime | None
    configuration_json: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class ManualRunOut(BaseModel):
    job_id: uuid.UUID
    status: str = "queued"


class FileExtractionOut(ORMModel):
    file_id: uuid.UUID
    status: str
    parser_name: str | None
    parser_version: str | None
    page_count: int | None
    chunk_count: int
    error_code: str | None
    error_message: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ClarificationResolve(BaseModel):
    value: str = Field(min_length=1, max_length=500)


class ClarificationOut(ORMModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    message_id: uuid.UUID
    question: str
    options_json: list[dict[str, Any]]
    status: str
    selected_value: str | None
    resolved_at: datetime | None


class MessageEvidenceOut(ORMModel):
    id: uuid.UUID
    evidence_key: str
    domain: str
    title: str
    value_json: dict[str, Any]
    source_type: str
    source_display_name: str
    source_data_as_of: datetime
    dataset_version: str | None
    scope_json: dict[str, Any]
    query_json: dict[str, Any]
    row_references_json: list[dict[str, Any]]
    created_at: datetime


class DiagnosticShareOut(BaseModel):
    message_id: uuid.UUID
    expires_at: datetime
    revoked_at: datetime | None
