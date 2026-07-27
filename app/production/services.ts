import { ApiClient, apiClient, humanizeApiError } from "./api-client";
import type {
  AuthMe,
  AuthSession,
  Conversation,
  ConversationMessage,
  CursorPage,
  DataCapabilities,
  Job,
  Memory,
  McpTool,
  McpToolCatalog,
  MessageEvidence,
  ModelProviderConfig,
  ModelProviderTest,
  OrganizationUnit,
  ProductionBootstrap,
  Project,
  Report,
} from "./types";

function queryString(values: Record<string, string | boolean | null | undefined>) {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) {
    if (value != null) query.set(key, String(value));
  }
  const serialized = query.toString();
  return serialized ? `?${serialized}` : "";
}

function idempotencyHeaders() {
  const id = typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  return { "Idempotency-Key": id };
}

export function createProductionServices(client: ApiClient = apiClient) {
  const auth = {
    async login(email: string, password: string) {
      return client.request<AuthSession>("/auth/login", {
        method: "POST",
        skipCsrf: true,
        body: { email, password },
      });
    },
    async me() {
      const result = await client.request<AuthMe>("/auth/me");
      client.setCsrfToken(result.csrf_token);
      return result;
    },
    async changePassword(currentPassword: string, newPassword: string) {
      return client.request<AuthSession>("/auth/change-password", {
        method: "POST",
        body: { current_password: currentPassword, new_password: newPassword },
      });
    },
    async logout() {
      await client.request<void>("/auth/logout", { method: "POST" });
      client.clearSessionState();
    },
    async sessions() {
      return client.request<Array<{ id: string; created_at: string; last_seen_at: string; expires_at: string; ip_address: string | null; user_agent: string | null; is_current: boolean }>>("/auth/sessions");
    },
    async revokeSession(sessionId: string) {
      return client.request<void>(`/auth/sessions/${encodeURIComponent(sessionId)}`, { method: "DELETE" });
    },
    async updatePreferences(memoryEnabled: boolean) {
      return client.request<AuthMe["user"]>("/auth/preferences", {
        method: "PATCH",
        body: { memory_enabled: memoryEnabled },
      });
    },
  };

  const organizations = {
    async listAnalyzable() {
      return client.request<CursorPage<OrganizationUnit>>(
        `/organization-units${queryString({ enabled_for_analysis: true })}`,
      );
    },
  };

  const conversations = {
    async list(
      cursor?: string | null,
      options: { projectId?: string | null; includeArchived?: boolean } = {},
    ) {
      return client.request<CursorPage<Conversation>>(
        `/conversations${queryString({
          cursor,
          project_id: options.projectId,
          include_archived: options.includeArchived,
        })}`,
      );
    },
    async get(id: string) {
      return client.request<Conversation>(`/conversations/${encodeURIComponent(id)}`);
    },
    async create(values: { title?: string; organization_unit_id?: string; project_id?: string }) {
      return client.request<Conversation>("/conversations", { method: "POST", headers: idempotencyHeaders(), body: values });
    },
    async update(id: string, values: { title?: string; organization_unit_id?: string | null; status?: "active" | "archived" }) {
      return client.request<Conversation>(`/conversations/${encodeURIComponent(id)}`, { method: "PATCH", body: values });
    },
    async archive(id: string) {
      return client.request<void>(`/conversations/${encodeURIComponent(id)}`, { method: "DELETE" });
    },
    async setPinned(id: string, pinned: boolean) {
      return client.request<Conversation>(`/conversations/${encodeURIComponent(id)}/pin`, {
        method: pinned ? "POST" : "DELETE",
      });
    },
    async messages(id: string, cursor?: string | null) {
      return client.request<CursorPage<ConversationMessage>>(
        `/conversations/${encodeURIComponent(id)}/messages${queryString({ after_sequence: cursor })}`,
      );
    },
    async sendMessage(id: string, content: string) {
      return client.request<ConversationMessage>(
        `/conversations/${encodeURIComponent(id)}/messages`,
        { method: "POST", headers: idempotencyHeaders(), body: { content, file_ids: [] } },
      );
    },
    async evidence(id: string, messageId: string) {
      return client.request<MessageEvidence[]>(
        `/conversations/${encodeURIComponent(id)}/messages/${encodeURIComponent(messageId)}/evidence`,
      );
    },
    async resolveClarification(id: string, clarificationId: string, value: string) {
      return client.request(
        `/conversations/${encodeURIComponent(id)}/clarifications/${encodeURIComponent(clarificationId)}`,
        { method: "POST", body: { value } },
      );
    },
    streamUrl(id: string, afterSequence: number) {
      return `${client.baseUrl}/conversations/${encodeURIComponent(id)}/stream${queryString({ after_sequence: String(afterSequence) })}`;
    },
  };

  const projects = {
    async list(cursor?: string | null) {
      return client.request<CursorPage<Project>>(`/projects${queryString({ cursor })}`);
    },
    async create(name: string, description?: string, organizationUnitId?: string) {
      return client.request<Project>("/projects", { method: "POST", headers: idempotencyHeaders(), body: { name, description, organization_unit_id: organizationUnitId } });
    },
    async update(id: string, values: { name?: string; description?: string | null; organization_unit_id?: string | null }) {
      return client.request<Project>(`/projects/${encodeURIComponent(id)}`, { method: "PATCH", body: values });
    },
    async archive(id: string) {
      return client.request<void>(`/projects/${encodeURIComponent(id)}`, { method: "DELETE" });
    },
    async setPinned(id: string, pinned: boolean) {
      return client.request<Project>(`/projects/${encodeURIComponent(id)}/pin`, {
        method: pinned ? "POST" : "DELETE",
      });
    },
  };

  const memories = {
    async list(cursor?: string | null) {
      return client.request<CursorPage<Memory>>(`/memories${queryString({ cursor })}`);
    },
    async create(values: { title: string; content: string; kind?: string; organization_unit_id?: string; source_conversation_id?: string }) {
      return client.request<Memory>("/memories", { method: "POST", headers: idempotencyHeaders(), body: values });
    },
    async update(id: string, values: { title?: string; content?: string; status?: "active" | "disabled" | "deleted" }) {
      return client.request<Memory>(`/memories/${encodeURIComponent(id)}`, { method: "PATCH", body: values });
    },
    async remove(id: string) {
      return client.request<void>(`/memories/${encodeURIComponent(id)}`, { method: "DELETE" });
    },
  };

  const reports = {
    async list(cursor?: string | null, reportKind?: string) {
      return client.request<CursorPage<Report>>(
        `/reports${queryString({ cursor, kind: reportKind })}`,
      );
    },
    async get(id: string) {
      return client.request<Report>(`/reports/${encodeURIComponent(id)}`);
    },
  };

  const jobs = {
    async list(cursor?: string | null) {
      return client.request<CursorPage<Job>>(`/jobs${queryString({ cursor })}`);
    },
    async get(id: string) {
      return client.request<Job>(`/jobs/${encodeURIComponent(id)}`);
    },
    async cancel(id: string) {
      return client.request<Job>(`/jobs/${encodeURIComponent(id)}/cancel`, {
        method: "POST",
      });
    },
    async retry(id: string) {
      return client.request<Job>(`/jobs/${encodeURIComponent(id)}/retry`, {
        method: "POST",
      });
    },
  };

  const data = {
    async capabilities() {
      return client.request<DataCapabilities>("/data-capabilities");
    },
  };

  const adminModels = {
    async get() {
      return client.request<ModelProviderConfig>("/admin/model-provider");
    },
    async update(values: { model_id: string; api_key?: string; is_enabled?: boolean }) {
      return client.request<ModelProviderConfig>("/admin/model-provider", {
        method: "PUT",
        body: values,
      });
    },
    async test() {
      return client.request<ModelProviderTest>("/admin/model-provider/test", {
        method: "POST",
      });
    },
  };

  const adminMcp = {
    async list() {
      return client.request<McpToolCatalog>("/admin/mcp-tools");
    },
    async update(toolName: string, values: Partial<Pick<McpTool, "display_name" | "description" | "is_enabled" | "planner_enabled" | "timeout_seconds" | "max_rows" | "operator_note">>) {
      return client.request<McpTool>(`/admin/mcp-tools/${encodeURIComponent(toolName)}`, {
        method: "PATCH",
        body: values,
      });
    },
    async validate(toolName: string) {
      return client.request<{ tool: McpTool; ready: boolean; issues: string[] }>(
        `/admin/mcp-tools/${encodeURIComponent(toolName)}/validate`,
        { method: "POST" },
      );
    },
  };

  return { auth, organizations, conversations, projects, memories, reports, jobs, data, adminModels, adminMcp };
}

export type ProductionServices = ReturnType<typeof createProductionServices>;

export const productionServices = createProductionServices();

export async function loadProductionBootstrap(
  services: ProductionServices = productionServices,
): Promise<ProductionBootstrap> {
  const me = await services.auth.me();
  if (me.user.password_change_required) {
    return {
      me,
      organizationUnits: [],
      conversations: [],
      projects: [],
      memories: [],
      reports: [],
      jobs: [],
      dataCapabilities: null,
      optionalErrors: {},
    };
  }

  // Keep the executive workspace and its resources isolated from management
  // sessions. Enterprise administrators and FDEs use separate APIs/surfaces.
  if (me.user.role !== "executive") {
    return {
      me,
      organizationUnits: [],
      conversations: [],
      projects: [],
      memories: [],
      reports: [],
      jobs: [],
      dataCapabilities: null,
      optionalErrors: {},
    };
  }

  const [organizationsResult, conversationsResult, projectsResult] = await Promise.all([
    services.organizations.listAnalyzable(),
    services.conversations.list(),
    services.projects.list(),
  ]);

  const optional = await Promise.allSettled([
    services.memories.list(),
    services.reports.list(),
    services.jobs.list(),
    services.data.capabilities(),
  ] as const);
  const optionalErrors: ProductionBootstrap["optionalErrors"] = {};
  const authorizedOrganizationIds = new Set(me.scopes.map((scope) => scope.id));
  const optionalKeys = ["memories", "reports", "jobs", "dataCapabilities"] as const;
  optional.forEach((result, index) => {
    if (result.status === "rejected") {
      optionalErrors[optionalKeys[index]] = humanizeApiError(result.reason);
    }
  });

  return {
    me,
    organizationUnits: organizationsResult.items.filter(
      (unit) => authorizedOrganizationIds.has(unit.id) && unit.enabled_for_analysis && unit.data_connected,
    ),
    conversations: conversationsResult.items,
    projects: projectsResult.items,
    memories: optional[0].status === "fulfilled" ? optional[0].value.items : [],
    reports: optional[1].status === "fulfilled" ? optional[1].value.items : [],
    jobs: optional[2].status === "fulfilled" ? optional[2].value.items : [],
    dataCapabilities: optional[3].status === "fulfilled" ? optional[3].value : null,
    optionalErrors,
  };
}
