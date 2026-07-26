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

export type FileMetadata = {
  id: string;
  original_name: string;
  media_type: string;
  size_bytes: number;
  sha256: string;
  status: "uploaded" | "processing" | "ready" | "partial" | "failed";
  created_at: string;
  deleted_at: string | null;
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
  optionalErrors: Partial<Record<"memories" | "reports" | "jobs", string>>;
};

export type ApiErrorPayload = {
  error?: {
    code?: string;
    message?: string;
    details?: unknown;
    request_id?: string;
  };
};
