import { ApiClient, apiClient, humanizeApiError } from "./api-client";
import type {
  AuthMe,
  AuthSession,
  Conversation,
  ConversationMessage,
  CursorPage,
  FileMetadata,
  Job,
  Memory,
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
    async sendMessage(id: string, content: string, fileIds: string[] = []) {
      return client.request<ConversationMessage>(
        `/conversations/${encodeURIComponent(id)}/messages`,
        { method: "POST", headers: idempotencyHeaders(), body: { content, file_ids: fileIds } },
      );
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

  const files = {
    async upload(file: File, conversationId?: string) {
      const form = new FormData();
      form.set("file", file);
      if (conversationId) form.set("conversation_id", conversationId);
      return client.request<FileMetadata>("/files", { method: "POST", headers: idempotencyHeaders(), body: form });
    },
    async get(id: string) {
      return client.request<FileMetadata>(`/files/${encodeURIComponent(id)}`);
    },
    contentUrl(id: string) {
      return `${client.baseUrl}/files/${encodeURIComponent(id)}/content`;
    },
    async remove(id: string) {
      return client.request<void>(`/files/${encodeURIComponent(id)}`, { method: "DELETE" });
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
  };

  return { auth, organizations, conversations, projects, files, memories, reports, jobs };
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
  ] as const);
  const optionalErrors: ProductionBootstrap["optionalErrors"] = {};
  const authorizedOrganizationIds = new Set(me.scopes.map((scope) => scope.id));
  const optionalKeys = ["memories", "reports", "jobs"] as const;
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
    optionalErrors,
  };
}
