import { ApiClient, apiClient, humanizeApiError } from "./api-client";
import type {
  AdminModelAuthorization,
  AdminModelCatalog,
  AuthorizedModel,
  AuthMe,
  AuthSession,
  Conversation,
  ConversationMessage,
  CursorPage,
  DataCapabilities,
  DailyBrief,
  DataOperationsV3Overview,
  DataSource,
  DataSourceTest,
  DataSyncRun,
  ExecutivePersonalProfile,
  HarnessBusinessConfig,
  HarnessConfig,
  HarnessMetrics,
  HarnessSimulation,
  HarnessTrace,
  HarnessVersion,
  Job,
  Memory,
  McpTool,
  McpToolCatalog,
  McpCompositeToolCreate,
  MessageEvidence,
  ModelProviderConfig,
  ModelProviderTest,
  OpportunityExperienceWeightPolicy,
  OrganizationUnit,
  OrganizationScope,
  ProductionBootstrap,
  Project,
  Report,
  ScheduledTask,
  ManualRun,
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
    async personalProfile() {
      return client.request<ExecutivePersonalProfile>("/auth/personal-profile");
    },
    async updatePersonalProfile(values: Omit<ExecutivePersonalProfile, "version" | "updated_at">) {
      return client.request<ExecutivePersonalProfile>("/auth/personal-profile", {
        method: "PUT",
        body: values,
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
      options: {
        projectId?: string | null;
        includeArchived?: boolean;
        placement?: "unassigned" | "project" | "all";
      } = {},
    ) {
      return client.request<CursorPage<Conversation>>(
        `/conversations${queryString({
          cursor,
          project_id: options.projectId,
          include_archived: options.includeArchived,
          placement: options.placement,
        })}`,
      );
    },
    async get(id: string) {
      return client.request<Conversation>(`/conversations/${encodeURIComponent(id)}`);
    },
    async create(values: { title?: string; organization_scope?: OrganizationScope; project_id?: string; model_id?: string }) {
      return client.request<Conversation>("/conversations", { method: "POST", headers: idempotencyHeaders(), body: values });
    },
    async update(id: string, values: { title?: string; organization_scope?: OrganizationScope; status?: "active" | "archived"; model_id?: string }) {
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
    async setProject(id: string, projectId: string | null) {
      return client.request<Conversation>(
        `/conversations/${encodeURIComponent(id)}/project`,
        { method: "PATCH", body: { project_id: projectId } },
      );
    },
    async sendMessage(
      id: string,
      content: string,
      organizationScope: OrganizationScope,
      modelId: string,
    ) {
      return client.request<ConversationMessage>(
        `/conversations/${encodeURIComponent(id)}/messages`,
        { method: "POST", headers: idempotencyHeaders(), body: { content, file_ids: [], organization_scope: organizationScope, model_id: modelId } },
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
    async shareDiagnostic(id: string, messageId: string) {
      return client.request<{ message_id: string; expires_at: string; revoked_at: string | null }>(
        `/conversations/${encodeURIComponent(id)}/messages/${encodeURIComponent(messageId)}/diagnostic-share`,
        { method: "POST" },
      );
    },
    async revokeDiagnosticShare(id: string, messageId: string) {
      return client.request<void>(
        `/conversations/${encodeURIComponent(id)}/messages/${encodeURIComponent(messageId)}/diagnostic-share`,
        { method: "DELETE" },
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
    async dailyBrief(organizationUnitIds: string[] = []) {
      const query = new URLSearchParams();
      for (const organizationUnitId of organizationUnitIds) {
        query.append("organization_unit_ids", organizationUnitId);
      }
      const serialized = query.toString();
      return client.request<DailyBrief>(`/daily-brief${serialized ? `?${serialized}` : ""}`);
    },
  };

  const models = {
    async list() {
      return client.request<AuthorizedModel[]>("/models");
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
    async catalog() {
      return client.request<AdminModelCatalog>("/admin/models");
    },
    async testModel(modelId: string) {
      return client.request<ModelProviderTest>(
        `/admin/models/${encodeURIComponent(modelId)}/test`,
        { method: "POST" },
      );
    },
    async authorize(
      modelId: string,
      isAuthorized: boolean,
      displayName?: string,
    ) {
      return client.request<AdminModelAuthorization>(
        `/admin/models/${encodeURIComponent(modelId)}/authorization`,
        {
          method: "PATCH",
          body: { is_authorized: isAuthorized, display_name: displayName },
        },
      );
    },
    async setDefault(modelId: string) {
      return client.request<AdminModelAuthorization>(
        `/admin/models/${encodeURIComponent(modelId)}/default`,
        { method: "PATCH", body: { is_default: true } },
      );
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
    async create(values: McpCompositeToolCreate) {
      return client.request<McpTool>("/admin/mcp-tools", {
        method: "POST",
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

  const adminData = {
    async overview() {
      return client.request<DataOperationsV3Overview>("/admin/data-operations/overview");
    },
    async sources() {
      return client.request<CursorPage<DataSource>>("/admin/data-sources");
    },
    async updateSource(sourceId: string, values: { display_name?: string; is_enabled?: boolean; configuration_json?: Record<string, unknown> }) {
      return client.request<DataSource>(`/admin/data-sources/${encodeURIComponent(sourceId)}`, {
        method: "PATCH",
        body: values,
      });
    },
    async testSource(sourceId: string) {
      return client.request<DataSourceTest>(`/admin/data-sources/${encodeURIComponent(sourceId)}/test`, { method: "POST" });
    },
    async syncSource(sourceId: string) {
      return client.request<ManualRun>(`/admin/data-sources/${encodeURIComponent(sourceId)}/sync`, { method: "POST" });
    },
    async validateSource(sourceId: string) {
      return client.request<ManualRun>(`/admin/data-sources/${encodeURIComponent(sourceId)}/validate`, { method: "POST" });
    },
    async runs() {
      return client.request<CursorPage<DataSyncRun>>("/admin/data-sync-runs");
    },
    async scheduledTasks() {
      return client.request<CursorPage<ScheduledTask>>("/admin/scheduled-tasks");
    },
    async runScheduledTask(taskId: string) {
      return client.request<ManualRun>(`/admin/scheduled-tasks/${encodeURIComponent(taskId)}/run`, { method: "POST" });
    },
    async experienceWeightPolicy() {
      return client.request<OpportunityExperienceWeightPolicy>("/admin/metric-policies/opportunity-experience-weight");
    },
    async updateExperienceWeightPolicy(values: {
      base_version: number;
      weights: { high: number; medium: number; low: number };
      label?: string;
      notes?: string;
    }) {
      return client.request<OpportunityExperienceWeightPolicy>("/admin/metric-policies/opportunity-experience-weight", {
        method: "PATCH",
        body: values,
      });
    },
  };

  const adminHarness = {
    async get() {
      return client.request<HarnessConfig>("/admin/harness/config");
    },
    async update(baseVersion: number, config: HarnessBusinessConfig) {
      return client.request<HarnessConfig>("/admin/harness/config", {
        method: "PATCH",
        body: { base_version: baseVersion, config },
      });
    },
    async versions() {
      return client.request<HarnessVersion[]>("/admin/harness/versions");
    },
    async restore(versionId: string) {
      return client.request<HarnessConfig>(`/admin/harness/versions/${encodeURIComponent(versionId)}/restore`, { method: "POST" });
    },
    async simulate(question: string, config: HarnessBusinessConfig, organizationScope?: OrganizationScope) {
      return client.request<HarnessSimulation>("/admin/harness/simulate", {
        method: "POST",
        body: { question, config, organization_scope: organizationScope },
      });
    },
    async metrics(days = 30) {
      return client.request<HarnessMetrics>(`/admin/harness/metrics${queryString({ days: String(days) })}`);
    },
    async traces() {
      return client.request<HarnessTrace[]>("/admin/harness/traces");
    },
    async trace(messageId: string) {
      return client.request<HarnessTrace>(`/admin/harness/traces/${encodeURIComponent(messageId)}`);
    },
  };

  return { auth, organizations, conversations, projects, memories, reports, jobs, data, models, adminModels, adminHarness, adminMcp, adminData };
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
      authorizedModels: [],
      memories: [],
      reports: [],
      jobs: [],
      dataCapabilities: null,
      dailyBrief: null,
      personalProfile: null,
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
      authorizedModels: [],
      memories: [],
      reports: [],
      jobs: [],
      dataCapabilities: null,
      dailyBrief: null,
      personalProfile: null,
      optionalErrors: {},
    };
  }

  const [organizationsResult, conversationsResult, projectsResult] = await Promise.all([
    services.organizations.listAnalyzable(),
    services.conversations.list(undefined, { placement: "all" }),
    services.projects.list(),
  ]);

  const optional = await Promise.allSettled([
    services.memories.list(),
    services.reports.list(),
    services.jobs.list(),
    services.data.capabilities(),
    services.data.dailyBrief(),
    services.auth.personalProfile(),
    services.models.list(),
  ] as const);
  const optionalErrors: ProductionBootstrap["optionalErrors"] = {};
  const authorizedOrganizationIds = new Set(me.scopes.map((scope) => scope.id));
  const optionalKeys = ["memories", "reports", "jobs", "dataCapabilities", "dailyBrief", "personalProfile", "authorizedModels"] as const;
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
    authorizedModels: optional[6].status === "fulfilled" ? optional[6].value : [],
    memories: optional[0].status === "fulfilled" ? optional[0].value.items : [],
    reports: optional[1].status === "fulfilled" ? optional[1].value.items : [],
    jobs: optional[2].status === "fulfilled" ? optional[2].value.items : [],
    dataCapabilities: optional[3].status === "fulfilled" ? optional[3].value : null,
    dailyBrief: optional[4].status === "fulfilled" ? optional[4].value : null,
    personalProfile: optional[5].status === "fulfilled" ? optional[5].value : null,
    optionalErrors,
  };
}
