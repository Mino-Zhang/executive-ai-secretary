export type AppRole = "executive" | "enterprise_admin" | "fde";
export type AppEnvironment =
  | "development"
  | "test"
  | "local-demo"
  | "customer-template"
  | "production";
export type BackendAppMode = "demo" | "production";

export type ApiUser = {
  id: string;
  email: string;
  display_name: string;
  preferred_name: string | null;
  role: AppRole;
  locale: string;
  timezone: string;
  memory_enabled: boolean;
  password_change_required: boolean;
};

export type Enterprise = {
  id: string;
  name: string;
  slug: string;
};

export type AuthSession = {
  user: ApiUser;
  csrf_token: string;
  expires_at: string;
  app_env: AppEnvironment;
  app_mode: BackendAppMode;
};

export type AuthMe = Required<Pick<AuthSession, "user" | "csrf_token">> & {
  enterprise: Enterprise;
  scopes: OrganizationUnit[];
  app_env: AppEnvironment;
  app_mode: BackendAppMode;
};

export type OrganizationUnit = {
  id: string;
  name: string;
  code: string;
  parent_id: string | null;
  unit_type: string;
  data_connected: boolean;
  enabled_for_analysis: boolean;
  sort_order: number;
};

export type Conversation = {
  id: string;
  title: string;
  organization_unit_id: string | null;
  status: string;
  pinned_at: string | null;
  last_message_at: string | null;
  created_at: string;
  updated_at: string;
  archived_at?: string | null;
};

export type ConversationMessage = {
  id: string;
  conversation_id: string;
  role: "user" | "assistant" | "system" | "tool";
  content: string;
  content_json: Record<string, unknown>;
  sequence: number;
  model_name: string | null;
  source_data_as_of: string | null;
  created_at: string;
  status?: "queued" | "running" | "completed" | "failed";
  request_id?: string | null;
  citations?: Array<{ label: string; source: string; as_of?: string | null }>;
};

export type Project = {
  id: string;
  name: string;
  description: string | null;
  organization_unit_id: string | null;
  pinned_at: string | null;
  created_at: string;
  updated_at: string;
  archived_at?: string | null;
};

export type DataDomainStatus = {
  domain: "opportunity" | "delivery" | "collection" | "target" | string;
  status: "fresh" | "stale" | "partial" | "failed" | "unavailable" | string;
  source_data_as_of: string | null;
  last_success_at: string | null;
  record_count: number;
  dataset_version: string | null;
  source_type: string;
  source_display_name: string;
  last_error_code: string | null;
  last_error_message: string | null;
};

export type DataCapabilities = {
  source_kind: string;
  source_label: string;
  organization_unit_ids: string[];
  capabilities: Record<string, boolean>;
  domains: DataDomainStatus[];
  overall_status: "fresh" | "stale" | "partial" | "failed" | "unavailable" | string;
  generated_at: string;
};

export type AnspireModelOption = {
  id: string;
  name: string;
  family: string;
  profile: string;
  capability: "chat" | "image" | "video" | "embedding" | "rerank";
  selectable: boolean;
};

export type ModelProviderConfig = {
  provider: "anspire";
  endpoint_url: string;
  documentation_url: string;
  model_id: string;
  is_enabled: boolean;
  is_configured: boolean;
  api_key_masked: string | null;
  last_tested_at: string | null;
  last_test_status: "pending" | "success" | "failed" | null;
  last_test_latency_ms: number | null;
  last_test_error: string | null;
  models: AnspireModelOption[];
  updated_at: string | null;
};

export type ModelProviderTest = {
  status: "success";
  model: string;
  latency_ms: number;
  tested_at: string;
};

export type McpTool = {
  tool_name: string;
  display_name: string;
  description: string;
  category: string;
  domains: string[];
  parameters: Record<string, Record<string, unknown>>;
  is_enabled: boolean;
  planner_enabled: boolean;
  timeout_seconds: number;
  max_rows: number;
  operator_note: string | null;
  configured: boolean;
  readiness: "ready" | "disabled" | "data_unavailable";
  readiness_issues: string[];
  updated_at: string | null;
};

export type McpToolCatalog = {
  tools: McpTool[];
  enabled_count: number;
  planner_count: number;
  generated_at: string;
};

export type MessageEvidence = {
  id: string;
  evidence_key: string;
  domain: string;
  title: string;
  value_json: Record<string, unknown>;
  source_type: string;
  source_display_name: string;
  source_data_as_of: string;
  dataset_version: string | null;
  scope_json: Record<string, unknown>;
  query_json: Record<string, unknown>;
  row_references_json: Array<Record<string, unknown>>;
  created_at: string;
};

export type Memory = {
  id: string;
  title: string;
  content: string;
  kind: string;
  organization_unit_id: string | null;
  source_conversation_id: string | null;
  status: "active" | "disabled" | "deleted" | string;
  version: number;
  created_at: string;
  updated_at: string;
};

export type Report = {
  id: string;
  kind: "daily" | "weekly" | "custom" | string;
  title: string;
  status: "draft" | "published" | "queued" | "running" | "completed" | "failed" | string;
  organization_unit_id: string | null;
  period_start: string;
  period_end: string;
  data_as_of: string | null;
  published_at: string | null;
  created_at: string;
  latest_version: number | null;
  content: Record<string, unknown> | null;
};

export type Job = {
  id: string;
  job_type: string;
  status: "queued" | "running" | "completed" | "succeeded" | "failed" | "canceled";
  payload_json: Record<string, unknown>;
  result_json: Record<string, unknown>;
  error_code: string | null;
  scheduled_at: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  error_message?: string | null;
};

export type CursorPage<T> = {
  items: T[];
  next_cursor: string | null;
};

export type ProductionBootstrap = {
  me: AuthMe;
  organizationUnits: OrganizationUnit[];
  conversations: Conversation[];
  projects: Project[];
  memories: Memory[];
  reports: Report[];
  jobs: Job[];
  dataCapabilities: DataCapabilities | null;
  optionalErrors: Partial<Record<"memories" | "reports" | "jobs" | "dataCapabilities", string>>;
};

export type ApiErrorPayload = {
  error?: {
    code?: string;
    message?: string;
    details?: unknown;
    request_id?: string;
  };
};
