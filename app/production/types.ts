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

export type OrganizationScope = {
  mode: "all_authorized" | "selected";
  organization_unit_ids: string[];
  resolved_organization_unit_ids?: string[];
};

export type Conversation = {
  id: string;
  title: string;
  organization_unit_id: string | null;
  organization_scope: OrganizationScope;
  project_id: string | null;
  selected_model_id: string | null;
  status: string;
  pinned_at: string | null;
  last_message_at: string | null;
  created_at: string;
  updated_at: string;
  archived_at?: string | null;
};

export type ExecutivePersonalProfile = {
  salutation: string;
  amount_unit: "yuan" | "wan" | "yi";
  response_style: "concise" | "balanced" | "detailed";
  locale: "zh-CN" | "zh-TW" | "en-US";
  memory_enabled: boolean;
  version: number;
  updated_at: string | null;
};

export type HarnessPrompts = {
  system: string;
  route: string;
  rewrite: string;
  plan: string;
  data_answer: string;
  general_answer: string;
};

export type HarnessGlossaryEntry = {
  term: string;
  canonical: string;
  category: string;
  enabled: boolean;
};

export type HarnessFastRule = {
  id: string;
  name: string;
  enabled: boolean;
  priority: number;
  match_mode: "any" | "all";
  terms: string[];
  exclusions: string[];
  route: "data" | "general";
  candidate_tools: string[];
};

export type HarnessBusinessConfig = {
  prompts: HarnessPrompts;
  glossary: HarnessGlossaryEntry[];
  fast_rules: HarnessFastRule[];
};

export type HarnessConfig = {
  id: string;
  version: number;
  schema_version: string;
  config_hash: string;
  config: HarnessBusinessConfig;
  safety_kernel: Record<string, unknown>;
  activated_at: string;
  updated_at: string;
};

export type HarnessVersion = {
  id: string;
  version: number;
  config_hash: string;
  is_active: boolean;
  source_version_id: string | null;
  created_by_user_id: string | null;
  activated_at: string;
  created_at: string;
};

export type HarnessSimulation = {
  route: "data" | "general" | "clarification";
  route_source: "fast_rule" | "hermes" | "validation";
  matched_rule_id: string | null;
  candidate_tools: string[];
  query_spec: Record<string, unknown>;
  validation_issues: string[];
  config_hash: string;
};

export type HarnessMetrics = {
  window_days: number;
  message_count: number;
  intent_accuracy_sample_size: number;
  structured_output_rate: number;
  tool_success_rate: number;
  route_counts: Record<string, number>;
  stage_latency_p95_ms: Record<string, number>;
};

export type HarnessTrace = {
  message_id: string;
  conversation_id: string | null;
  route: string | null;
  route_source: string | null;
  query_spec_summary: Record<string, unknown>;
  harness_version: number | null;
  organization_unit_count: number;
  tools: string[];
  stages: Array<Record<string, unknown>>;
  diagnostic_shared_until: string | null;
  shared_content: Record<string, unknown> | null;
};

export type ConversationMessage = {
  id: string;
  conversation_id: string;
  role: "user" | "assistant" | "system" | "tool";
  content: string;
  content_json: Record<string, unknown>;
  sequence: number;
  requested_model_id: string | null;
  model_name: string | null;
  output_contract_version: string | null;
  output_template_id: string | null;
  source_data_as_of: string | null;
  created_at: string;
  status?: "queued" | "running" | "completed" | "failed";
  request_id?: string | null;
  citations?: Array<{ label: string; source: string; as_of?: string | null }>;
};

export type AuthorizedModel = {
  model_id: string;
  name: string;
  family: string;
  profile: string;
  display_name: string;
  is_default: boolean;
};

export type AdminModelAuthorization = AuthorizedModel & {
  capability: string;
  selectable: boolean;
  test_status: "pending" | "success" | "failed";
  tested_credential_version: number | null;
  current_credential_version: number;
  is_authorized: boolean;
  last_tested_at: string | null;
  last_test_latency_ms: number | null;
  last_test_error: string | null;
  authorized_at: string | null;
};

export type AdminModelCatalog = {
  provider: "anspire";
  credential_version: number;
  is_configured: boolean;
  is_enabled: boolean;
  models: AdminModelAuthorization[];
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

export type DailyBriefItem = {
  rule_id: "delivery_delayed" | "collection_overdue";
  domain: "delivery" | "collection";
  severity: "attention";
  title: string;
  detail: string;
  affected_count: number;
  amount: number | null;
  unit: "元" | null;
};

export type DailyBriefDomainReadiness = {
  domain: string;
  readiness: string;
  data_as_of: string | null;
  record_count: number;
};

export type DailyBrief = {
  brief_date: string | null;
  data_as_of: string | null;
  source_batch_id: string | null;
  readiness: "ready" | "stale" | "partial" | "unavailable";
  attention_count: number;
  items: DailyBriefItem[];
  domains: DailyBriefDomainReadiness[];
  organization_unit_ids: string[];
  uses_enterprise_snapshot: boolean;
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
  credential_version: number;
  last_tested_at: string | null;
  last_test_status: "pending" | "success" | "failed" | null;
  last_test_latency_ms: number | null;
  last_test_error: string | null;
  models: AnspireModelOption[];
  updated_at: string | null;
};

export type ChairmanAnswerMetric = {
  label: string;
  value: string | number;
  unit: string;
  context: string;
  direction: "up" | "down" | "flat" | "unknown";
  evidence_refs: string[];
};

export type ChairmanAnswer = {
  template_id:
    | "executive_pulse"
    | "target_gap"
    | "risk_action"
    | "top_opportunities"
    | "decision_memo";
  schema_version: "1.0";
  decision_readiness: "ready" | "conditional" | "not_ready";
  decision_line: string;
  confidence: { level: "high" | "medium" | "low"; reason: string };
  metrics: ChairmanAnswerMetric[];
  primary_evidence?: {
    kind: "progress" | "bar" | "ranked_bar" | "waterfall" | "timeline" | "table" | "comparison_matrix";
    title: string;
    dataset_ref: string;
    reason: string;
  } | null;
  risks_or_opportunities: Array<{
    type: "risk" | "opportunity";
    title: string;
    impact: string;
    evidence_refs: string[];
  }>;
  actions: Array<{
    owner: string;
    action: string;
    due_at: string;
    success_metric: string;
  }>;
  data_quality: {
    readiness: "ready" | "conditional" | "not_ready";
    as_of: string;
    scope: string;
    issues: Array<{ dimension: string; severity: string; detail: string }>;
    decision_impact: string;
  };
  sources: Array<{
    id: string;
    label: string;
    as_of: string;
    dataset_version?: string | null;
  }>;
  follow_up_questions: string[];
};

export type ExecutiveGeneralAnswer = {
  schema_version: "1.0";
  mode: "direct_answer" | "analysis_memo" | "action_plan" | "writing_draft";
  headline: string;
  direct_answer: string;
  sections: Array<{ title: string; content: string }>;
  action_items: Array<{ action: string; rationale: string }>;
  caveats: string[];
  draft_markdown?: string | null;
  capability_notice?: string | null;
  follow_up_questions: string[];
};

export type AssistantOutputEnvelope =
  | { schema_version: "1.0"; kind: "data"; body: ChairmanAnswer }
  | { schema_version: "1.0"; kind: "general"; body: ExecutiveGeneralAnswer }
  | { schema_version: "1.0"; kind: "clarification"; body: { question: string; options?: Array<Record<string, unknown>> } };

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
  source_type: "built_in" | "composite";
  component_tools: string[];
  definition_version: number;
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

export type McpCompositeToolCreate = {
  tool_name: string;
  display_name: string;
  description: string;
  category: string;
  component_tools: string[];
  operator_note?: string;
};

export type McpToolCatalog = {
  tools: McpTool[];
  enabled_count: number;
  planner_count: number;
  generated_at: string;
};

export type DataSource = {
  id: string;
  key: string;
  display_name: string;
  source_type: string;
  schema_version: string;
  is_enabled: boolean;
  configuration_json: Record<string, unknown>;
  last_tested_at: string | null;
  last_test_status: "success" | "failed" | null;
  last_test_error: string | null;
  created_at: string;
  updated_at: string;
};

export type DataSourceTest = {
  ok: boolean;
  schema_version: string;
  database_version: string;
  current_user: string;
  read_only: boolean;
  tls_active: boolean;
  latest_batch_id: string;
  source_data_as_of: string;
  duration_ms: number;
};

export type DataSyncRun = {
  id: string;
  data_source_id: string;
  job_id: string | null;
  trigger_type: string;
  status: string;
  dataset_version: string | null;
  source_schema_version: string | null;
  source_batch_id: string | null;
  source_data_as_of: string | null;
  started_at: string | null;
  completed_at: string | null;
  records_read: number;
  records_written: number;
  records_rejected: number;
  source_schema_hashes_json: Record<string, string>;
  source_record_counts_json: Record<string, number>;
  source_content_hashes_json: Record<string, string>;
  cross_table_validation_json: Record<string, unknown>;
  activation_mode: string;
  atomic_activation_status: string;
  experience_weight_policy_id: string | null;
  activation_started_at: string | null;
  activated_at: string | null;
  domain_results_json: Record<string, unknown>;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
};

export type FeishuFieldBindingStatus = {
  field_id: string;
  field_name: string;
  field_type: number;
  required: boolean;
};

export type FeishuTableBindingStatus = {
  domain: "opportunity" | "delivery" | "collection";
  display_name: string;
  configured: boolean;
  app_token_masked: string | null;
  table_id: string | null;
  fields: FeishuFieldBindingStatus[];
  schema_hash: string | null;
  content_hash: string | null;
  record_count: number | null;
  validation_status: "not_configured" | "configured" | "validated" | "rejected";
  last_validated_at: string | null;
  warnings: string[];
};

export type DataSourceOperationsStatus = {
  source_id: string;
  display_name: string;
  source_type: string;
  schema_version: string;
  is_enabled: boolean;
  activation_policy: string;
  bindings: FeishuTableBindingStatus[];
  latest_successful_run: DataSyncRun | null;
  latest_rejected_run: DataSyncRun | null;
};

export type OpportunityExperienceWeightPolicy = {
  id: string;
  version: number;
  label: string;
  weights_json: { high: number; medium: number; low: number };
  observation_windows_json: number[];
  observation_window_days: number;
  is_active: boolean;
  activated_at: string;
  notes: string | null;
  created_at: string;
  updated_at: string;
};

export type DataOperationsV3Overview = {
  sources: DataSourceOperationsStatus[];
  experience_weight_policy: OpportunityExperienceWeightPolicy;
  generated_at: string;
};

export type ScheduledTask = {
  id: string;
  data_source_id: string | null;
  key: string;
  task_type: string;
  cron_expression: string;
  timezone: string;
  is_enabled: boolean;
  next_run_at: string | null;
  last_enqueued_at: string | null;
  configuration_json: Record<string, unknown>;
  created_at: string;
  updated_at: string;
};

export type ManualRun = {
  job_id: string;
  status: "queued";
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
  authorizedModels: AuthorizedModel[];
  memories: Memory[];
  reports: Report[];
  jobs: Job[];
  dataCapabilities: DataCapabilities | null;
  dailyBrief: DailyBrief | null;
  personalProfile: ExecutivePersonalProfile | null;
  optionalErrors: Partial<Record<"memories" | "reports" | "jobs" | "dataCapabilities" | "dailyBrief" | "personalProfile" | "authorizedModels", string>>;
};

export type ApiErrorPayload = {
  error?: {
    code?: string;
    message?: string;
    details?: unknown;
    request_id?: string;
  };
};
