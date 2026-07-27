"use client";

import {
  ChangeEvent,
  FormEvent,
  KeyboardEvent,
  ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { ApiError, humanizeApiError } from "./api-client";
import { loadProductionBootstrap, productionServices } from "./services";
import type {
  AuthMe,
  Conversation,
  ConversationMessage,
  FileMetadata,
  Memory,
  OrganizationUnit,
  ProductionBootstrap,
  Project,
  Report,
} from "./types";

type ThemePreference = "system" | "light" | "dark";
type UiLanguage = "zh-CN" | "zh-TW" | "en";
type WorkspacePanel = "daily" | "weekly" | "history" | "memory" | "scope";
type PreferencesView = "profile" | "appearance" | "memory";
type ProjectDialogState = { mode: "create" } | { mode: "edit"; projectId: string };
type SidebarMenuState =
  | { kind: "conversation"; conversationId: string; top: number }
  | { kind: "project"; projectId: string; top: number };
type ConfirmState = {
  title: string;
  description: string;
  confirmLabel: string;
  tone?: "normal" | "danger";
  action: () => void | Promise<void>;
};
type ProfilePreferences = { salutation: string; amountUnit: string };

const ALL_SCOPE_ID = "all";
const COMPOSER_MAX_LENGTH = 8000;
const COMPOSER_HINT_THRESHOLD = COMPOSER_MAX_LENGTH * 0.8;

const languageOptions: Array<{ id: UiLanguage; label: string }> = [
  { id: "zh-CN", label: "简体中文" },
  { id: "zh-TW", label: "繁體中文" },
  { id: "en", label: "English" },
];

const copy = {
  "zh-CN": {
    brand: "董事长 AI 秘书",
    newConversation: "新建会话",
    daily: "今日经营简报",
    weekly: "每周高层简报",
    history: "历史会话",
    memory: "长期记忆",
    pinned: "置顶",
    projects: "项目",
    recent: "最近",
    all: "全部",
    settings: "设置",
    language: "语言",
    logout: "退出登录",
    profile: "个人资料",
    appearance: "外观",
    scope: "全部事业部",
    file: "文件",
    greetingQuestion: "今天需要我先看什么？",
    placeholder: "向 AI 秘书提问经营数据，或上传当前会话文件",
    disclaimer: "AI 可能出错。关键经营数字请结合来源与数据时间核对。",
    noProject: "尚未创建项目",
    noConversation: "尚无历史会话",
    dataReady: "企业数据可用",
    dataMissing: "尚未配置数据范围",
  },
  "zh-TW": {
    brand: "董事長 AI 秘書",
    newConversation: "新建會話",
    daily: "今日經營簡報",
    weekly: "每週高層簡報",
    history: "歷史會話",
    memory: "長期記憶",
    pinned: "置頂",
    projects: "項目",
    recent: "最近",
    all: "全部",
    settings: "設定",
    language: "語言",
    logout: "登出",
    profile: "個人資料",
    appearance: "外觀",
    scope: "全部事業部",
    file: "檔案",
    greetingQuestion: "今天需要我先看什麼？",
    placeholder: "向 AI 秘書提問經營資料，或上傳目前會話檔案",
    disclaimer: "AI 可能出錯。關鍵經營數字請結合來源與資料時間核對。",
    noProject: "尚未建立項目",
    noConversation: "尚無歷史會話",
    dataReady: "企業資料可用",
    dataMissing: "尚未設定資料範圍",
  },
  en: {
    brand: "Chairman's AI Secretary",
    newConversation: "New conversation",
    daily: "Daily brief",
    weekly: "Weekly executive brief",
    history: "Conversation history",
    memory: "Long-term memory",
    pinned: "Pinned",
    projects: "Projects",
    recent: "Recent",
    all: "All",
    settings: "Settings",
    language: "Language",
    logout: "Sign out",
    profile: "Profile",
    appearance: "Appearance",
    scope: "All business units",
    file: "File",
    greetingQuestion: "What should I look into first?",
    placeholder: "Ask about the business or upload a file for this conversation",
    disclaimer: "AI can make mistakes. Verify critical figures against sources and data timestamps.",
    noProject: "No projects yet",
    noConversation: "No conversations yet",
    dataReady: "Enterprise data available",
    dataMissing: "No data scope configured",
  },
} as const;

function preferredDisplayName(me: AuthMe) {
  return me.user.preferred_name || me.user.display_name || me.user.email;
}

function environmentLabel(me: AuthMe) {
  return me.app_env === "local-demo" || me.app_mode === "demo"
    ? "脱敏演示环境"
    : "生产环境";
}

function localizedDate(locale: string, timezone: string) {
  try {
    return new Intl.DateTimeFormat(locale || "zh-CN", {
      dateStyle: "full",
      timeZone: timezone || "Asia/Shanghai",
    }).format(new Date());
  } catch {
    return new Intl.DateTimeFormat("zh-CN", { dateStyle: "full" }).format(new Date());
  }
}

function greetingForCurrentHour(timezone: string, language: UiLanguage) {
  let hour = new Date().getHours();
  try {
    const part = new Intl.DateTimeFormat("en-US", {
      hour: "2-digit",
      hour12: false,
      timeZone: timezone || "Asia/Shanghai",
    }).formatToParts(new Date()).find((item) => item.type === "hour")?.value;
    if (part) hour = Number(part) % 24;
  } catch {
    // Browser time is a safe display-only fallback.
  }
  if (language === "en") {
    if (hour < 6) return "Good evening";
    if (hour < 12) return "Good morning";
    if (hour < 18) return "Good afternoon";
    return "Good evening";
  }
  if (hour < 6) return language === "zh-TW" ? "夜深了" : "夜深了";
  if (hour < 12) return "早上好";
  if (hour < 18) return "下午好";
  return "晚上好";
}

function formatTimestamp(value: string | null | undefined, locale: string = "zh-CN") {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(locale, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function formatDate(value: string, locale: string = "zh-CN") {
  const date = new Date(`${value}T00:00:00`);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(locale, { year: "numeric", month: "short", day: "numeric" }).format(date);
}

function messageStatusLabel(status: ConversationMessage["status"]) {
  if (status === "queued") return "等待受控处理";
  if (status === "running") return "正在处理";
  if (status === "failed") return "未完成";
  return status ?? "";
}

function wait(milliseconds: number) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

function makeInitials(value: string) {
  const normalized = value.trim();
  if (!normalized) return "董";
  const latin = normalized.split(/[\s._-]+/).filter(Boolean).map((part) => part[0]).join("").slice(0, 2);
  return latin || normalized.slice(0, 2);
}

function sortByPinnedAndRecent<T extends { pinned_at: string | null; updated_at: string }>(items: T[]) {
  return [...items].sort((first, second) => {
    if (Boolean(first.pinned_at) !== Boolean(second.pinned_at)) return first.pinned_at ? -1 : 1;
    return second.updated_at.localeCompare(first.updated_at);
  });
}

export function ProductionWorkspace({
  initialBootstrap,
  onSessionExpired,
}: {
  initialBootstrap: ProductionBootstrap;
  onSessionExpired: () => void;
  onReload: () => Promise<void>;
}) {
  const [bootstrap, setBootstrap] = useState(initialBootstrap);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const [activeProjectId, setActiveProjectId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ConversationMessage[]>([]);
  const [messagesLoading, setMessagesLoading] = useState(false);
  const [messagesError, setMessagesError] = useState("");
  const [draft, setDraft] = useState("");
  const [selectedOrganizationId, setSelectedOrganizationId] = useState(
    initialBootstrap.organizationUnits.length > 1
      ? ALL_SCOPE_ID
      : initialBootstrap.organizationUnits[0]?.id ?? ALL_SCOPE_ID,
  );
  const [uploadedFiles, setUploadedFiles] = useState<FileMetadata[]>([]);
  const [uploading, setUploading] = useState(false);
  const [sending, setSending] = useState(false);
  const [workspaceError, setWorkspaceError] = useState("");
  const [toast, setToast] = useState("");
  const [accountMenuOpen, setAccountMenuOpen] = useState(false);
  const [languageMenuOpen, setLanguageMenuOpen] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [activePanel, setActivePanel] = useState<WorkspacePanel | null>(null);
  const [selectedReport, setSelectedReport] = useState<Report | null>(null);
  const [reportLoading, setReportLoading] = useState(false);
  const [preferencesView, setPreferencesView] = useState<PreferencesView | null>(null);
  const [sidebarMenu, setSidebarMenu] = useState<SidebarMenuState | null>(null);
  const [projectDialog, setProjectDialog] = useState<ProjectDialogState | null>(null);
  const [confirmState, setConfirmState] = useState<ConfirmState | null>(null);
  const [renameConversationId, setRenameConversationId] = useState<string | null>(null);
  const [renameDraft, setRenameDraft] = useState("");
  const [unreadConversationIds, setUnreadConversationIds] = useState<string[]>(() => {
    if (typeof window === "undefined") return [];
    try {
      const parsed = JSON.parse(window.localStorage.getItem("executive-workbench-unread-conversations") || "[]");
      return Array.isArray(parsed) ? parsed.filter((item): item is string => typeof item === "string") : [];
    } catch {
      return [];
    }
  });
  const [expandedProjectIds, setExpandedProjectIds] = useState<string[]>([]);
  const [projectConversations, setProjectConversations] = useState<Record<string, Conversation[]>>({});
  const [projectLoadingId, setProjectLoadingId] = useState<string | null>(null);
  const [themePreference, setThemePreference] = useState<ThemePreference>(() => {
    if (typeof window === "undefined") return "system";
    const saved = window.localStorage.getItem("executive-workbench-theme");
    return saved === "light" || saved === "dark" || saved === "system" ? saved : "system";
  });
  const [languagePreference, setLanguagePreference] = useState<UiLanguage>(() => {
    if (typeof window === "undefined") return "zh-CN";
    const saved = window.localStorage.getItem("executive-workbench-language");
    if (saved === "zh-CN" || saved === "zh-TW" || saved === "en") return saved;
    return initialBootstrap.me.user.locale === "zh-TW" || initialBootstrap.me.user.locale === "en"
      ? initialBootstrap.me.user.locale
      : "zh-CN";
  });
  const [profilePreferences, setProfilePreferences] = useState<ProfilePreferences>(() => {
    if (typeof window === "undefined") return { salutation: "董事长", amountUnit: "万元" };
    try {
      const saved = JSON.parse(window.localStorage.getItem("executive-workbench-profile-preferences") || "null") as Partial<ProfilePreferences> | null;
      return {
        salutation: saved?.salutation?.trim() || "董事长",
        amountUnit: saved?.amountUnit?.trim() || "万元",
      };
    } catch {
      return { salutation: "董事长", amountUnit: "万元" };
    }
  });
  const [memoryEnabled, setMemoryEnabled] = useState(() => {
    if (typeof window === "undefined") return true;
    return window.localStorage.getItem("executive-workbench-memory-enabled") !== "false";
  });
  const fileRef = useRef<HTMLInputElement>(null);
  const accountRef = useRef<HTMLDivElement>(null);
  const sidebarMenuRef = useRef<HTMLDivElement>(null);
  const deepLinkHandled = useRef(false);

  const me = bootstrap.me;
  const c = copy[languagePreference];
  const organizationUnits = bootstrap.organizationUnits;
  const businessDataReady = organizationUnits.length > 0;
  const activeConversation = bootstrap.conversations.find((item) => item.id === activeConversationId) ?? null;
  const selectedOrganization = selectedOrganizationId === ALL_SCOPE_ID
    ? null
    : organizationUnits.find((unit) => unit.id === selectedOrganizationId) ?? null;
  const selectedScopeLabel = selectedOrganization?.name ?? c.scope;
  const sortedProjects = useMemo(() => sortByPinnedAndRecent(bootstrap.projects), [bootstrap.projects]);
  const pinnedConversations = useMemo(
    () => sortByPinnedAndRecent(bootstrap.conversations.filter((item) => item.pinned_at && !item.archived_at)),
    [bootstrap.conversations],
  );
  const recentConversations = useMemo(
    () => [...bootstrap.conversations]
      .filter((item) => !item.pinned_at && !item.archived_at)
      .sort((first, second) => (second.last_message_at || second.updated_at).localeCompare(first.last_message_at || first.updated_at))
      .slice(0, 14),
    [bootstrap.conversations],
  );
  const latestDailyReport = useMemo(
    () => bootstrap.reports
      .filter((report) => report.kind === "daily" && (report.status === "published" || report.status === "completed"))
      .sort((first, second) => String(second.published_at || second.created_at).localeCompare(String(first.published_at || first.created_at)))[0] ?? null,
    [bootstrap.reports],
  );
  const optionalWarning = Object.values(bootstrap.optionalErrors)[0];
  const userInitials = makeInitials(preferredDisplayName(me)).toUpperCase();

  useEffect(() => {
    document.documentElement.dataset.theme = themePreference;
    document.documentElement.style.colorScheme = themePreference === "system" ? "light dark" : themePreference;
    window.localStorage.setItem("executive-workbench-theme", themePreference);
  }, [themePreference]);

  useEffect(() => {
    document.documentElement.lang = languagePreference;
    window.localStorage.setItem("executive-workbench-language", languagePreference);
  }, [languagePreference]);

  useEffect(() => {
    window.localStorage.setItem("executive-workbench-profile-preferences", JSON.stringify(profilePreferences));
  }, [profilePreferences]);

  useEffect(() => {
    window.localStorage.setItem("executive-workbench-memory-enabled", String(memoryEnabled));
  }, [memoryEnabled]);

  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(""), 2600);
    return () => window.clearTimeout(timer);
  }, [toast]);

  useEffect(() => {
    window.localStorage.setItem("executive-workbench-unread-conversations", JSON.stringify(unreadConversationIds));
  }, [unreadConversationIds]);

  useEffect(() => {
    const closeFloating = (event: PointerEvent) => {
      const target = event.target as HTMLElement;
      if (!accountRef.current?.contains(target)) {
        setAccountMenuOpen(false);
        setLanguageMenuOpen(false);
      }
      if (!target.closest("[data-sidebar-menu]")) setSidebarMenu(null);
    };
    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        newConversation();
      }
      if (event.key === "Escape") {
        setAccountMenuOpen(false);
        setLanguageMenuOpen(false);
        setSidebarMenu(null);
      }
    };
    window.addEventListener("pointerdown", closeFloating);
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("pointerdown", closeFloating);
      window.removeEventListener("keydown", onKeyDown);
    };
  });

  const runRequest = useCallback(async <T,>(action: () => Promise<T>): Promise<T | undefined> => {
    try {
      setWorkspaceError("");
      return await action();
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        onSessionExpired();
        return undefined;
      }
      setWorkspaceError(humanizeApiError(error));
      return undefined;
    }
  }, [onSessionExpired]);

  const refreshWorkspace = useCallback(async () => {
    const refreshed = await runRequest(() => loadProductionBootstrap());
    if (refreshed) setBootstrap(refreshed);
  }, [runRequest]);

  const openConversation = useCallback(async (conversation: Conversation) => {
    setActiveConversationId(conversation.id);
    setActiveProjectId(null);
    setMessages([]);
    setMessagesError("");
    setMessagesLoading(true);
    setSidebarOpen(false);
    setActivePanel(null);
    setUnreadConversationIds((current) => current.filter((id) => id !== conversation.id));
    try {
      const result = await productionServices.conversations.messages(conversation.id);
      setMessages(result.items);
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        onSessionExpired();
        return;
      }
      setMessagesError(humanizeApiError(error));
    } finally {
      setMessagesLoading(false);
    }
  }, [onSessionExpired]);

  useEffect(() => {
    if (deepLinkHandled.current) return;
    deepLinkHandled.current = true;
    const conversationId = new URLSearchParams(window.location.search).get("conversation");
    if (!conversationId) return;
    const conversation = initialBootstrap.conversations.find((item) => item.id === conversationId);
    if (!conversation) return;
    const timer = window.setTimeout(() => void openConversation(conversation), 0);
    return () => window.clearTimeout(timer);
  }, [initialBootstrap.conversations, openConversation]);

  function newConversation(projectId: string | null = null) {
    setActiveConversationId(null);
    setActiveProjectId(projectId);
    setMessages([]);
    setMessagesError("");
    setDraft("");
    setUploadedFiles([]);
    setSidebarOpen(false);
    setActivePanel(null);
    const project = bootstrap.projects.find((item) => item.id === projectId);
    if (project?.organization_unit_id) setSelectedOrganizationId(project.organization_unit_id);
    window.history.replaceState(null, "", window.location.pathname);
  }

  async function uploadFiles(event: ChangeEvent<HTMLInputElement>) {
    const incoming = Array.from(event.target.files ?? []);
    event.target.value = "";
    if (!incoming.length) return;
    setUploading(true);
    const results = await runRequest(async () => {
      const uploaded: FileMetadata[] = [];
      for (const file of incoming.slice(0, 10)) {
        if (file.size > 50 * 1024 * 1024) throw new Error(`${file.name} 超过 50 MB 限制。`);
        uploaded.push(await productionServices.files.upload(file, activeConversationId ?? undefined));
      }
      return uploaded;
    });
    if (results) setUploadedFiles((current) => [...current, ...results]);
    setUploading(false);
  }

  async function removeUploadedFile(file: FileMetadata) {
    const removed = await runRequest(async () => {
      await productionServices.files.remove(file.id);
      return true;
    });
    if (removed) setUploadedFiles((current) => current.filter((item) => item.id !== file.id));
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const content = draft.trim();
    if (!content || sending || !businessDataReady) return;
    setSending(true);
    await runRequest(async () => {
      let conversationId = activeConversationId;
      if (!conversationId) {
        const createdConversation = await productionServices.conversations.create({
          title: content.slice(0, 42),
          organization_unit_id: selectedOrganization?.id,
          project_id: activeProjectId ?? undefined,
        });
        conversationId = createdConversation.id;
        setActiveConversationId(conversationId);
        setBootstrap((current) => ({
          ...current,
          conversations: [createdConversation, ...current.conversations],
        }));
        if (activeProjectId) {
          setProjectConversations((current) => ({
            ...current,
            [activeProjectId]: [createdConversation, ...(current[activeProjectId] ?? [])],
          }));
        }
      }
      const message = await productionServices.conversations.sendMessage(
        conversationId,
        content,
        uploadedFiles.filter((file) => file.status === "ready" || file.status === "partial").map((file) => file.id),
      );
      setMessages((current) => [...current, message]);
      setDraft("");
      setUploadedFiles([]);
      window.history.replaceState(null, "", `${window.location.pathname}?conversation=${encodeURIComponent(conversationId)}`);
      let refreshed = await productionServices.conversations.messages(conversationId);
      setMessages(refreshed.items);
      for (let attempt = 0; attempt < 8; attempt += 1) {
        const hasPendingAssistant = refreshed.items.some(
          (item) => item.role === "assistant" && (item.status === "queued" || item.status === "running"),
        );
        if (!hasPendingAssistant) break;
        await wait(750);
        refreshed = await productionServices.conversations.messages(conversationId);
        setMessages(refreshed.items);
      }
      await refreshWorkspace();
    });
    setSending(false);
  }

  function handleComposerKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) return;
    event.preventDefault();
    event.currentTarget.form?.requestSubmit();
  }

  async function logout() {
    await runRequest(async () => {
      await productionServices.auth.logout();
      onSessionExpired();
    });
  }

  async function openReport(kind: "daily" | "weekly", reportId?: string) {
    setActivePanel(kind);
    setSelectedReport(null);
    setReportLoading(false);
    const summary = reportId
      ? bootstrap.reports.find((item) => item.id === reportId)
      : bootstrap.reports
        .filter((item) => item.kind === kind)
        .sort((first, second) => second.period_end.localeCompare(first.period_end))[0];
    if (!summary) return;
    setReportLoading(true);
    const detail = await runRequest(() => productionServices.reports.get(summary.id));
    if (detail) setSelectedReport(detail);
    setReportLoading(false);
  }

  async function toggleConversationPinned(conversation: Conversation) {
    const updated = await runRequest(() => productionServices.conversations.setPinned(conversation.id, !conversation.pinned_at));
    if (!updated) return;
    setBootstrap((current) => ({
      ...current,
      conversations: current.conversations.map((item) => item.id === updated.id ? updated : item),
    }));
    setSidebarMenu(null);
    setToast(updated.pinned_at ? "会话已置顶" : "已取消置顶");
  }

  async function renameConversation(conversationId: string, title: string) {
    const normalized = title.trim();
    if (!normalized) return;
    const updated = await runRequest(() => productionServices.conversations.update(conversationId, { title: normalized }));
    if (!updated) return;
    setBootstrap((current) => ({
      ...current,
      conversations: current.conversations.map((item) => item.id === updated.id ? updated : item),
    }));
    setProjectConversations((current) => Object.fromEntries(
      Object.entries(current).map(([projectId, items]) => [
        projectId,
        items.map((item) => item.id === updated.id ? updated : item),
      ]),
    ));
    setRenameConversationId(null);
    setToast("会话名称已更新");
  }

  function requestArchiveConversation(conversation: Conversation) {
    setSidebarMenu(null);
    setConfirmState({
      title: "归档这条会话？",
      description: `“${conversation.title}”将从工作台列表移除，数据仍保留在受控存储中。`,
      confirmLabel: "归档会话",
      tone: "danger",
      action: async () => {
        const archived = await runRequest(async () => {
          await productionServices.conversations.archive(conversation.id);
          return true;
        });
        if (!archived) return;
        setBootstrap((current) => ({
          ...current,
          conversations: current.conversations.filter((item) => item.id !== conversation.id),
        }));
        setProjectConversations((current) => Object.fromEntries(
          Object.entries(current).map(([projectId, items]) => [projectId, items.filter((item) => item.id !== conversation.id)]),
        ));
        if (activeConversationId === conversation.id) newConversation();
        setToast("会话已归档");
      },
    });
  }

  function toggleUnread(conversationId: string) {
    setUnreadConversationIds((current) => current.includes(conversationId)
      ? current.filter((id) => id !== conversationId)
      : [...current, conversationId]);
    setSidebarMenu(null);
  }

  async function copyText(value: string, confirmation: string) {
    await navigator.clipboard.writeText(value);
    setSidebarMenu(null);
    setToast(confirmation);
  }

  async function toggleProject(projectId: string) {
    if (expandedProjectIds.includes(projectId)) {
      setExpandedProjectIds((current) => current.filter((id) => id !== projectId));
      return;
    }
    setExpandedProjectIds((current) => [...current, projectId]);
    if (projectConversations[projectId]) return;
    setProjectLoadingId(projectId);
    const result = await runRequest(() => productionServices.conversations.list(undefined, { projectId }));
    if (result) setProjectConversations((current) => ({ ...current, [projectId]: result.items }));
    setProjectLoadingId(null);
  }

  async function saveProject(state: ProjectDialogState, name: string, description: string, organizationUnitId: string) {
    const normalizedOrganization = organizationUnitId === ALL_SCOPE_ID ? undefined : organizationUnitId;
    const result = state.mode === "create"
      ? await runRequest(() => productionServices.projects.create(name, description || undefined, normalizedOrganization))
      : await runRequest(() => productionServices.projects.update(state.projectId, {
        name,
        description: description || null,
        organization_unit_id: normalizedOrganization || null,
      }));
    if (!result) return false;
    setBootstrap((current) => ({
      ...current,
      projects: state.mode === "create"
        ? [result, ...current.projects]
        : current.projects.map((item) => item.id === result.id ? result : item),
    }));
    setProjectDialog(null);
    if (state.mode === "create") setExpandedProjectIds((current) => [...current, result.id]);
    setToast(state.mode === "create" ? "项目已创建" : "项目已更新");
    return true;
  }

  async function toggleProjectPinned(project: Project) {
    const updated = await runRequest(() => productionServices.projects.setPinned(project.id, !project.pinned_at));
    if (!updated) return;
    setBootstrap((current) => ({
      ...current,
      projects: current.projects.map((item) => item.id === updated.id ? updated : item),
    }));
    setSidebarMenu(null);
    setToast(updated.pinned_at ? "项目已置顶" : "已取消项目置顶");
  }

  function requestArchiveProjectTasks(project: Project) {
    setSidebarMenu(null);
    setConfirmState({
      title: "归档项目内的全部会话？",
      description: `“${project.name}”项目仍会保留，但其中的现有会话将全部归档。`,
      confirmLabel: "归档全部会话",
      tone: "danger",
      action: async () => {
        let items = projectConversations[project.id];
        if (!items) {
          const result = await runRequest(() => productionServices.conversations.list(undefined, { projectId: project.id }));
          items = result?.items ?? [];
        }
        const archived = await runRequest(async () => {
          await Promise.all(items.map((item) => productionServices.conversations.archive(item.id)));
          return true;
        });
        if (!archived) return;
        const ids = new Set(items.map((item) => item.id));
        setBootstrap((current) => ({
          ...current,
          conversations: current.conversations.filter((item) => !ids.has(item.id)),
        }));
        setProjectConversations((current) => ({ ...current, [project.id]: [] }));
        if (activeConversationId && ids.has(activeConversationId)) newConversation();
        setToast("项目会话已归档");
      },
    });
  }

  function requestRemoveProject(project: Project) {
    setSidebarMenu(null);
    setConfirmState({
      title: "移除这个项目？",
      description: `“${project.name}”将从项目列表移除。项目内会话不会被删除，仍可在历史会话中找到。`,
      confirmLabel: "移除项目",
      tone: "danger",
      action: async () => {
        const removed = await runRequest(async () => {
          await productionServices.projects.archive(project.id);
          return true;
        });
        if (!removed) return;
        setBootstrap((current) => ({
          ...current,
          projects: current.projects.filter((item) => item.id !== project.id),
        }));
        setExpandedProjectIds((current) => current.filter((id) => id !== project.id));
        setActiveProjectId((current) => current === project.id ? null : current);
        setToast("项目已移除");
      },
    });
  }

  function openSidebarMenu(
    event: React.MouseEvent<HTMLButtonElement>,
    state: { kind: "conversation"; conversationId: string } | { kind: "project"; projectId: string },
  ) {
    event.stopPropagation();
    const top = Math.min(event.currentTarget.getBoundingClientRect().top, window.innerHeight - 310);
    setSidebarMenu({ ...state, top } as SidebarMenuState);
  }

  const sidebarMenuConversation = sidebarMenu?.kind === "conversation"
    ? bootstrap.conversations.find((item) => item.id === sidebarMenu.conversationId) ?? null
    : null;
  const sidebarMenuProject = sidebarMenu?.kind === "project"
    ? bootstrap.projects.find((item) => item.id === sidebarMenu.projectId) ?? null
    : null;

  return (
    <div className={`product-shell workbench-shell production-workbench production-workbench-v2 ${sidebarOpen ? "sidebar-open" : ""} ${sidebarCollapsed ? "sidebar-collapsed" : ""}`} data-app-mode={me.app_mode} data-app-environment={me.app_env}>
      <a className="skip-link" href="#main-content">跳到主要内容</a>
      {(workspaceError || optionalWarning) && <div className="network-banner" role="status"><span>{workspaceError || `部分辅助能力暂不可用：${optionalWarning}`}</span><button type="button" onClick={() => setWorkspaceError("")} aria-label="关闭提示">×</button></div>}

      <aside className="workspace-sidebar" aria-label="工作台侧栏">
        <header className="sidebar-brand-row">
          <button className="sidebar-brand" type="button" onClick={() => newConversation()}>
            <span className="brand-glyph" aria-hidden="true">董</span>
            <span className="sidebar-label"><strong>{c.brand}</strong><small>{me.enterprise.name}</small></span>
          </button>
          <button className="sidebar-collapse" type="button" aria-label={sidebarCollapsed ? "展开侧栏" : "收起侧栏"} onClick={() => setSidebarCollapsed((current) => !current)}>{sidebarCollapsed ? "›" : "‹"}</button>
        </header>

        <div className="sidebar-scroll-region">
          <button className="new-conversation-button" type="button" onClick={() => newConversation()}><span aria-hidden="true">＋</span><strong className="sidebar-label">{c.newConversation}</strong><kbd className="sidebar-label">⌘ K</kbd></button>
          <nav className="workspace-navigation" aria-label="经营工作台功能">
            <button type="button" className={!activeConversationId && !activePanel ? "active" : ""} onClick={() => newConversation()}><span aria-hidden="true">问</span><strong className="sidebar-label">经营问数</strong></button>
            <button type="button" className={activePanel === "daily" ? "active" : ""} onClick={() => void openReport("daily")}><span aria-hidden="true">今</span><strong className="sidebar-label">{c.daily}</strong></button>
            <button type="button" className={activePanel === "weekly" ? "active" : ""} onClick={() => void openReport("weekly")}><span aria-hidden="true">周</span><strong className="sidebar-label">{c.weekly}</strong></button>
            <button type="button" className={activePanel === "history" ? "active" : ""} onClick={() => setActivePanel("history")}><span aria-hidden="true">历</span><strong className="sidebar-label">{c.history}</strong></button>
            <button type="button" className={activePanel === "memory" ? "active" : ""} onClick={() => setActivePanel("memory")}><span aria-hidden="true">记</span><strong className="sidebar-label">{c.memory}</strong></button>
          </nav>

          <div className="sidebar-sections">
            {pinnedConversations.length > 0 && (
              <section className="sidebar-section" aria-labelledby="production-pinned-title">
                <header className="sidebar-section-header"><span id="production-pinned-title">{c.pinned}</span></header>
                <div className="sidebar-list">
                  {pinnedConversations.map((conversation) => (
                    <SidebarConversationRow
                      key={conversation.id}
                      conversation={conversation}
                      active={activeConversationId === conversation.id}
                      unread={unreadConversationIds.includes(conversation.id)}
                      renaming={renameConversationId === conversation.id}
                      renameDraft={renameDraft}
                      setRenameDraft={setRenameDraft}
                      onRename={() => void renameConversation(conversation.id, renameDraft)}
                      onCancelRename={() => setRenameConversationId(null)}
                      onOpen={() => void openConversation(conversation)}
                      onMenu={(event) => openSidebarMenu(event, { kind: "conversation", conversationId: conversation.id })}
                    />
                  ))}
                </div>
              </section>
            )}

            <section className="sidebar-section" aria-labelledby="production-projects-title">
              <header className="sidebar-section-header"><span id="production-projects-title">{c.projects}</span><button className="sidebar-add-project" type="button" aria-label="新建项目" onClick={() => setProjectDialog({ mode: "create" })}>＋</button></header>
              <div className="sidebar-list">
                {sortedProjects.length ? sortedProjects.map((project) => {
                  const expanded = expandedProjectIds.includes(project.id);
                  const items = projectConversations[project.id] ?? [];
                  return (
                    <div className={`sidebar-project ${project.pinned_at ? "pinned" : ""}`} key={project.id}>
                      <div className="sidebar-project-row-shell">
                        <button className="sidebar-project-button" type="button" aria-expanded={expanded} title={project.description || project.name} onClick={() => void toggleProject(project.id)}>
                          <span className="sidebar-disclosure" aria-hidden="true">{expanded ? "⌄" : "›"}</span><span className="sidebar-project-mark" aria-hidden="true" /><strong>{project.name}</strong>
                        </button>
                        <button type="button" className="sidebar-row-menu-button sidebar-project-menu-button" data-sidebar-menu aria-label={`打开“${project.name}”项目菜单`} onClick={(event) => openSidebarMenu(event, { kind: "project", projectId: project.id })}>•••</button>
                      </div>
                      {expanded && <div className="sidebar-project-conversations">
                        {projectLoadingId === project.id && <small className="sidebar-project-loading">正在读取项目会话…</small>}
                        {projectLoadingId !== project.id && items.map((conversation) => <button type="button" key={conversation.id} className={activeConversationId === conversation.id ? "active" : ""} onClick={() => void openConversation(conversation)}><span aria-hidden="true" /><strong>{conversation.title}</strong></button>)}
                        <button type="button" className={`sidebar-project-start-button ${activeProjectId === project.id && !activeConversationId ? "active" : ""}`} onClick={() => newConversation(project.id)}><span aria-hidden="true">＋</span><strong>在此项目新建会话</strong></button>
                      </div>}
                    </div>
                  );
                }) : <small className="sidebar-label sidebar-empty-copy">{c.noProject}</small>}
              </div>
            </section>

            <section className="sidebar-section" aria-labelledby="production-recent-title">
              <header className="sidebar-section-header"><span id="production-recent-title">{c.recent}</span><button type="button" onClick={() => setActivePanel("history")}>{c.all}</button></header>
              <div className="sidebar-list">
                {recentConversations.length ? recentConversations.map((conversation) => (
                  <SidebarConversationRow
                    key={conversation.id}
                    conversation={conversation}
                    active={activeConversationId === conversation.id}
                    unread={unreadConversationIds.includes(conversation.id)}
                    renaming={renameConversationId === conversation.id}
                    renameDraft={renameDraft}
                    setRenameDraft={setRenameDraft}
                    onRename={() => void renameConversation(conversation.id, renameDraft)}
                    onCancelRename={() => setRenameConversationId(null)}
                    onOpen={() => void openConversation(conversation)}
                    onMenu={(event) => openSidebarMenu(event, { kind: "conversation", conversationId: conversation.id })}
                  />
                )) : <small className="sidebar-label sidebar-empty-copy">{c.noConversation}</small>}
              </div>
            </section>
          </div>
        </div>

        <footer className="sidebar-footer">
          <button type="button" className="sidebar-data-status" onClick={() => setActivePanel("scope")}><span className={`status-dot ${businessDataReady ? "positive" : ""}`} aria-hidden="true" /><span className="sidebar-label"><strong>{businessDataReady ? c.dataReady : c.dataMissing}</strong><small>{businessDataReady ? `${organizationUnits.length} 个授权事业部` : "请联系企业管理员"}</small></span></button>
          <div ref={accountRef} className="profile-control workspace-profile">
            <button className="profile-button" type="button" aria-label="打开个人菜单" aria-expanded={accountMenuOpen} onClick={() => { setAccountMenuOpen((current) => !current); setLanguageMenuOpen(false); }}><span className="profile-avatar" aria-hidden="true">{userInitials}</span><span className="sidebar-label"><strong>{preferredDisplayName(me)}</strong><small>{selectedScopeLabel}</small></span><span className="profile-menu-chevron sidebar-label" aria-hidden="true">{accountMenuOpen ? "⌄" : "›"}</span></button>
            {accountMenuOpen && <div className="profile-menu account-menu" role="menu" aria-label="个人菜单">
              <button type="button" className="account-menu-identity" role="menuitem" onClick={() => { setPreferencesView("profile"); setAccountMenuOpen(false); }}><span className="account-menu-avatar" aria-hidden="true">{userInitials}</span><span><strong>{preferredDisplayName(me)}</strong><small>{me.user.email}</small></span><UiIcon name="chevron" /></button>
              <div className="profile-menu-divider" />
              <button type="button" className="account-menu-item" role="menuitem" onClick={() => { setPreferencesView("appearance"); setAccountMenuOpen(false); }}><UiIcon name="settings" /><span>{c.settings}</span></button>
              <div className="account-language-control">
                <button type="button" className="account-menu-item" role="menuitem" aria-haspopup="menu" aria-expanded={languageMenuOpen} onClick={() => setLanguageMenuOpen((current) => !current)}><UiIcon name="language" /><span>{c.language}</span><small>{languageOptions.find((option) => option.id === languagePreference)?.label}</small><UiIcon name="chevron" /></button>
                {languageMenuOpen && <div className="language-submenu" role="menu" aria-label="选择界面语言">{languageOptions.map((option) => <button type="button" key={option.id} className={languagePreference === option.id ? "selected" : ""} role="menuitemradio" aria-checked={languagePreference === option.id} onClick={() => { setLanguagePreference(option.id); setLanguageMenuOpen(false); setAccountMenuOpen(false); }}><span>{option.label}</span><span aria-hidden="true">{languagePreference === option.id ? "✓" : ""}</span></button>)}</div>}
              </div>
              <div className="profile-menu-divider" />
              <button type="button" className="account-menu-item account-menu-logout" role="menuitem" onClick={() => void logout()}><UiIcon name="logout" /><span>{c.logout}</span></button>
            </div>}
          </div>
        </footer>

        {sidebarMenuConversation && <div ref={sidebarMenuRef} className="sidebar-context-menu" data-sidebar-menu role="menu" style={{ top: sidebarMenu?.top }}>
          <button type="button" role="menuitem" onClick={() => void toggleConversationPinned(sidebarMenuConversation)}>{sidebarMenuConversation.pinned_at ? "取消置顶" : "置顶"}</button>
          <button type="button" role="menuitem" onClick={() => toggleUnread(sidebarMenuConversation.id)}>{unreadConversationIds.includes(sidebarMenuConversation.id) ? "标记为已读" : "标记未读"}</button>
          <button type="button" role="menuitem" onClick={() => { setRenameConversationId(sidebarMenuConversation.id); setRenameDraft(sidebarMenuConversation.title); setSidebarMenu(null); }}>重命名</button>
          <button type="button" role="menuitem" onClick={() => requestArchiveConversation(sidebarMenuConversation)}>归档</button>
          <span className="sidebar-menu-divider" role="separator" />
          <button type="button" role="menuitem" onClick={() => void copyText(sidebarMenuConversation.id, "会话 ID 已复制")}>复制会话 ID</button>
          <button type="button" role="menuitem" onClick={() => void copyText(`${window.location.origin}${window.location.pathname}?conversation=${encodeURIComponent(sidebarMenuConversation.id)}`, "深度链接已复制")}>复制深度链接</button>
          <span className="sidebar-menu-divider" role="separator" />
          <button type="button" role="menuitem" onClick={() => { const title = sidebarMenuConversation.title; newConversation(); setDraft(`继续“${title}”中的工作：`); setSidebarMenu(null); }}>在新会话中继续</button>
        </div>}
        {sidebarMenuProject && <div ref={sidebarMenuRef} className="sidebar-context-menu sidebar-project-context-menu" data-sidebar-menu role="menu" style={{ top: sidebarMenu?.top }}>
          <button type="button" role="menuitem" onClick={() => void toggleProjectPinned(sidebarMenuProject)}><UiIcon name="pin" /><span>{sidebarMenuProject.pinned_at ? "取消置顶项目" : "置顶项目"}</span></button>
          <button type="button" role="menuitem" onClick={() => { setProjectDialog({ mode: "edit", projectId: sidebarMenuProject.id }); setSidebarMenu(null); }}><UiIcon name="edit" /><span>编辑项目</span></button>
          <button type="button" role="menuitem" onClick={() => requestArchiveProjectTasks(sidebarMenuProject)}><UiIcon name="archive" /><span>归档任务</span></button>
          <span className="sidebar-menu-divider" role="separator" />
          <button type="button" className="danger" role="menuitem" onClick={() => requestRemoveProject(sidebarMenuProject)}><UiIcon name="remove" /><span>移除</span></button>
        </div>}
      </aside>

      <button className="workspace-sidebar-scrim" type="button" aria-label="关闭侧栏" onClick={() => setSidebarOpen(false)} />

      <section className="workspace-stage" aria-label="AI 对话工作台">
        <header className="workspace-topbar">
          <button className="mobile-sidebar-trigger" type="button" aria-label="打开侧栏" onClick={() => setSidebarOpen(true)}>☰</button>
          <div className="workspace-title-block"><strong>{activeConversation?.title || (activeProjectId ? bootstrap.projects.find((item) => item.id === activeProjectId)?.name : null) || c.newConversation}</strong><small>{environmentLabel(me)} · {selectedScopeLabel}</small></div>
          <div className="workspace-topbar-actions"><span className="production-environment-badge">{environmentLabel(me)}</span><button className="topbar-scope-button" type="button" onClick={() => void refreshWorkspace()}>刷新数据</button><button className="topbar-new-button" type="button" aria-label="新建会话" onClick={() => newConversation()}>＋</button></div>
        </header>
        <main id="main-content" className="workspace-main">
          {activeConversationId ? (
            <ProductionConversation
              conversation={activeConversation}
              messages={messages}
              loading={messagesLoading}
              error={messagesError}
              draft={draft}
              setDraft={setDraft}
              sending={sending}
              uploadedFiles={uploadedFiles}
              uploading={uploading}
              fileRef={fileRef}
              onFiles={uploadFiles}
              onRemoveFile={removeUploadedFile}
              onKeyDown={handleComposerKeyDown}
              onSubmit={submit}
              organizationUnits={organizationUnits}
              selectedOrganizationId={selectedOrganizationId}
              setSelectedOrganizationId={setSelectedOrganizationId}
              language={languagePreference}
              disclaimer={c.disclaimer}
            />
          ) : (
            <ProductionHome
              me={me}
              language={languagePreference}
              salutation={profilePreferences.salutation}
              organizationUnits={organizationUnits}
              selectedOrganizationId={selectedOrganizationId}
              setSelectedOrganizationId={setSelectedOrganizationId}
              latestReport={latestDailyReport}
              onOpenReport={() => void openReport("daily", latestDailyReport?.id)}
              draft={draft}
              setDraft={setDraft}
              sending={sending}
              uploadedFiles={uploadedFiles}
              uploading={uploading}
              fileRef={fileRef}
              onFiles={uploadFiles}
              onRemoveFile={removeUploadedFile}
              onKeyDown={handleComposerKeyDown}
              onSubmit={submit}
              activeProjectName={activeProjectId ? bootstrap.projects.find((item) => item.id === activeProjectId)?.name ?? null : null}
            />
          )}
        </main>
      </section>

      {activePanel && <WorkspaceDetailPanel
        panel={activePanel}
        onClose={() => setActivePanel(null)}
        report={selectedReport}
        reportLoading={reportLoading}
        reports={bootstrap.reports}
        conversations={bootstrap.conversations}
        memories={bootstrap.memories}
        organizationUnits={organizationUnits}
        language={languagePreference}
        memoryEnabled={memoryEnabled}
        setMemoryEnabled={setMemoryEnabled}
        onSelectReport={(report) => void openReport(report.kind === "weekly" ? "weekly" : "daily", report.id)}
        onOpenConversation={(conversation) => void openConversation(conversation)}
        onNewConversation={() => newConversation()}
        onRenameConversation={renameConversation}
        onArchiveConversation={requestArchiveConversation}
        onCreateMemory={async (title, content, kind, organizationUnitId) => {
          const created = await runRequest(() => productionServices.memories.create({ title, content, kind, organization_unit_id: organizationUnitId || undefined }));
          if (created) { setBootstrap((current) => ({ ...current, memories: [created, ...current.memories] })); setToast("记忆已保存"); return true; }
          return false;
        }}
        onUpdateMemory={async (memory, values) => {
          const updated = await runRequest(() => productionServices.memories.update(memory.id, values));
          if (updated) { setBootstrap((current) => ({ ...current, memories: current.memories.map((item) => item.id === updated.id ? updated : item) })); setToast("记忆已更新"); return true; }
          return false;
        }}
        onDeleteMemory={(memory) => setConfirmState({ title: "删除这条长期记忆？", description: `“${memory.title}”将不再用于后续会话。`, confirmLabel: "删除记忆", tone: "danger", action: async () => { const removed = await runRequest(async () => { await productionServices.memories.remove(memory.id); return true; }); if (removed) { setBootstrap((current) => ({ ...current, memories: current.memories.filter((item) => item.id !== memory.id) })); setToast("记忆已删除"); } } })}
      />}

      {preferencesView && <PreferencesWindow
        view={preferencesView}
        setView={setPreferencesView}
        onClose={() => setPreferencesView(null)}
        me={me}
        initials={userInitials}
        selectedScopeLabel={selectedScopeLabel}
        organizationUnits={organizationUnits}
        theme={themePreference}
        setTheme={setThemePreference}
        language={languagePreference}
        profilePreferences={profilePreferences}
        setProfilePreferences={setProfilePreferences}
        memoryEnabled={memoryEnabled}
        setMemoryEnabled={setMemoryEnabled}
        memories={bootstrap.memories}
        onCreateMemory={async (title, content, kind, organizationUnitId) => {
          const created = await runRequest(() => productionServices.memories.create({ title, content, kind, organization_unit_id: organizationUnitId || undefined }));
          if (created) { setBootstrap((current) => ({ ...current, memories: [created, ...current.memories] })); setToast("记忆已保存"); return true; }
          return false;
        }}
        onUpdateMemory={async (memory, values) => {
          const updated = await runRequest(() => productionServices.memories.update(memory.id, values));
          if (updated) { setBootstrap((current) => ({ ...current, memories: current.memories.map((item) => item.id === updated.id ? updated : item) })); setToast("记忆已更新"); return true; }
          return false;
        }}
        onDeleteMemory={(memory) => setConfirmState({ title: "删除这条长期记忆？", description: `“${memory.title}”将不再用于后续会话。`, confirmLabel: "删除记忆", tone: "danger", action: async () => { const removed = await runRequest(async () => { await productionServices.memories.remove(memory.id); return true; }); if (removed) { setBootstrap((current) => ({ ...current, memories: current.memories.filter((item) => item.id !== memory.id) })); setToast("记忆已删除"); } } })}
      />}

      {projectDialog && <ProjectDialog
        state={projectDialog}
        project={projectDialog.mode === "edit" ? bootstrap.projects.find((item) => item.id === projectDialog.projectId) ?? null : null}
        organizationUnits={organizationUnits}
        onClose={() => setProjectDialog(null)}
        onSave={(name, description, organizationUnitId) => saveProject(projectDialog, name, description, organizationUnitId)}
      />}
      {confirmState && <ConfirmDialog state={confirmState} onCancel={() => setConfirmState(null)} onConfirm={() => { const action = confirmState.action; setConfirmState(null); void action(); }} />}
      {toast && <Toast message={toast} />}
    </div>
  );
}

function SidebarConversationRow({
  conversation,
  active,
  unread,
  renaming,
  renameDraft,
  setRenameDraft,
  onRename,
  onCancelRename,
  onOpen,
  onMenu,
}: {
  conversation: Conversation;
  active: boolean;
  unread: boolean;
  renaming: boolean;
  renameDraft: string;
  setRenameDraft: (value: string) => void;
  onRename: () => void;
  onCancelRename: () => void;
  onOpen: () => void;
  onMenu: (event: React.MouseEvent<HTMLButtonElement>) => void;
}) {
  return (
    <div className="sidebar-row-shell">
      {renaming ? (
        <form className="sidebar-rename-form" onSubmit={(event) => { event.preventDefault(); onRename(); }}>
          <input value={renameDraft} maxLength={60} onChange={(event) => setRenameDraft(event.target.value)} aria-label="新的会话名称" autoFocus />
          <button type="submit" aria-label="保存名称">✓</button>
          <button type="button" aria-label="取消重命名" onClick={onCancelRename}>×</button>
        </form>
      ) : (
        <>
          <button type="button" className={`sidebar-conversation-button ${active ? "active" : ""}`} onClick={onOpen}><span className={`sidebar-unread-dot ${unread ? "visible" : ""}`} aria-hidden="true" /><strong>{conversation.title || "未命名会话"}</strong></button>
          <button type="button" className="sidebar-row-menu-button" data-sidebar-menu aria-label={`打开“${conversation.title}”操作菜单`} onClick={onMenu}>•••</button>
        </>
      )}
    </div>
  );
}

function ProductionHome({
  me,
  language,
  salutation,
  organizationUnits,
  selectedOrganizationId,
  setSelectedOrganizationId,
  latestReport,
  onOpenReport,
  draft,
  setDraft,
  sending,
  uploadedFiles,
  uploading,
  fileRef,
  onFiles,
  onRemoveFile,
  onKeyDown,
  onSubmit,
  activeProjectName,
}: {
  me: AuthMe;
  language: UiLanguage;
  salutation: string;
  organizationUnits: OrganizationUnit[];
  selectedOrganizationId: string;
  setSelectedOrganizationId: (value: string) => void;
  latestReport: Report | null;
  onOpenReport: () => void;
  draft: string;
  setDraft: (value: string) => void;
  sending: boolean;
  uploadedFiles: FileMetadata[];
  uploading: boolean;
  fileRef: React.RefObject<HTMLInputElement | null>;
  onFiles: (event: ChangeEvent<HTMLInputElement>) => void;
  onRemoveFile: (file: FileMetadata) => void;
  onKeyDown: (event: KeyboardEvent<HTMLTextAreaElement>) => void;
  onSubmit: (event: FormEvent) => void;
  activeProjectName: string | null;
}) {
  const c = copy[language];
  const hasScope = organizationUnits.length > 0;
  const suggestions = language === "en"
    ? ["Summarize this month's operating changes", "Show items that need my confirmation", "Extract executive points from an uploaded file"]
    : language === "zh-TW"
      ? ["整理本月經營變化", "查看需要我確認的事項", "從上傳檔案提取管理層要點"]
      : ["整理本月经营变化", "查看需要我确认的事项", "从上传文件提取管理层要点"];

  return (
    <div className="workspace-home">
      <div className="home-empty-stage">
        <div className="home-empty-inner">
          {latestReport ? (
            <button className="morning-brief-trigger production-brief-trigger" type="button" onClick={onOpenReport}>
              <span className="morning-brief-dot" aria-hidden="true" />
              <span><strong>{latestReport.title}</strong><small>{latestReport.data_as_of ? `数据截至 ${formatTimestamp(latestReport.data_as_of, language)}` : "最新简报已生成"}</small></span>
              <span>查看晨间摘要 <b aria-hidden="true">›</b></span>
            </button>
          ) : (
            <div className="morning-brief-trigger production-brief-trigger empty" role="status"><span className="morning-brief-dot" aria-hidden="true" /><span><strong>今日简报尚未生成</strong><small>连接经营数据与简报任务后将在这里出现</small></span><span>尚未配置</span></div>
          )}

          <section className="workspace-greeting" aria-labelledby="production-greeting-title">
            <p>{localizedDate(language, me.user.timezone)}</p>
            <div className="greeting-title-line"><span className="service-mark" aria-hidden="true" /><h1 id="production-greeting-title">{greetingForCurrentHour(me.user.timezone, language)}，{salutation}</h1></div>
            <span>{hasScope ? c.greetingQuestion : "企业管理员尚未为您配置可分析的事业部。"}</span>
            {activeProjectName && <small className="active-project-context">当前会话将归入项目：{activeProjectName}</small>}
          </section>

          <ProductionComposer
            id="production-home-question"
            language={language}
            draft={draft}
            setDraft={setDraft}
            sending={sending}
            disabled={!hasScope}
            organizationUnits={organizationUnits}
            selectedOrganizationId={selectedOrganizationId}
            setSelectedOrganizationId={setSelectedOrganizationId}
            uploadedFiles={uploadedFiles}
            uploading={uploading}
            fileRef={fileRef}
            onFiles={onFiles}
            onRemoveFile={onRemoveFile}
            onKeyDown={onKeyDown}
            onSubmit={onSubmit}
          />

          {hasScope && <section className="prompt-suggestions production-prompt-suggestions" aria-label="从一个问题开始"><h2>{language === "en" ? "Start with a question" : "从一个问题开始"}</h2><div>{suggestions.map((suggestion) => <button type="button" key={suggestion} onClick={() => setDraft(suggestion)}><span>{suggestion}</span><i aria-hidden="true">›</i></button>)}</div></section>}
          <p className="home-service-note">生产模式不会使用演示数据。{c.disclaimer}</p>
        </div>
      </div>
    </div>
  );
}

function ProductionConversation({
  conversation,
  messages,
  loading,
  error,
  draft,
  setDraft,
  sending,
  uploadedFiles,
  uploading,
  fileRef,
  onFiles,
  onRemoveFile,
  onKeyDown,
  onSubmit,
  organizationUnits,
  selectedOrganizationId,
  setSelectedOrganizationId,
  language,
  disclaimer,
}: {
  conversation: Conversation | null;
  messages: ConversationMessage[];
  loading: boolean;
  error: string;
  draft: string;
  setDraft: (value: string) => void;
  sending: boolean;
  uploadedFiles: FileMetadata[];
  uploading: boolean;
  fileRef: React.RefObject<HTMLInputElement | null>;
  onFiles: (event: ChangeEvent<HTMLInputElement>) => void;
  onRemoveFile: (file: FileMetadata) => void;
  onKeyDown: (event: KeyboardEvent<HTMLTextAreaElement>) => void;
  onSubmit: (event: FormEvent) => void;
  organizationUnits: OrganizationUnit[];
  selectedOrganizationId: string;
  setSelectedOrganizationId: (value: string) => void;
  language: UiLanguage;
  disclaimer: string;
}) {
  return (
    <div className="chat-page production-chat-page">
      <div className="chat-scroll-region"><div className="chat-scroll-inner"><div className="conversation-column">
        {loading && <MessageSkeleton />}
        {error && <section className="state-card" role="alert"><p className="eyebrow">加载失败</p><h3>暂时无法读取这条会话</h3><p>{error}</p></section>}
        {!loading && !error && !messages.length && <section className="chat-empty-state"><p className="eyebrow">空会话</p><h2>{conversation?.title || "新会话"}</h2><p>这条会话还没有消息，可以从下方输入框开始。</p></section>}
        {messages.map((message) => message.role === "user" ? (
          <article className="user-message" key={message.id}><span>您</span><p>{message.content}</p><time>{formatTimestamp(message.created_at, language)}</time></article>
        ) : (
          <article className={`structured-answer production-answer ${message.status === "failed" ? "failed" : ""}`} key={message.id}>
            <div className="answer-meta"><span>{message.role === "assistant" ? "AI 秘书" : message.role === "tool" ? "数据工具" : "系统"}</span><time>{formatTimestamp(message.created_at, language)}</time></div>
            <section className="answer-conclusion"><p>{message.content || "正在等待真实处理结果…"}</p></section>
            <MessageDetails message={message} />
            {message.status && message.status !== "completed" && <small className={`message-status ${message.status}`}>状态：{messageStatusLabel(message.status)}</small>}
          </article>
        ))}
        {sending && <section className="processing-card" aria-live="polite"><p className="eyebrow">正在提交</p><h3>问题已进入受控处理流程</h3><p>系统不会在尚未收到真实结果时生成占位结论。</p></section>}
      </div></div></div>
      <div className="workspace-composer-dock chat-dock">
        <ProductionComposer
          id="production-chat-question"
          language={language}
          draft={draft}
          setDraft={setDraft}
          sending={sending}
          disabled={!organizationUnits.length}
          organizationUnits={organizationUnits}
          selectedOrganizationId={selectedOrganizationId}
          setSelectedOrganizationId={setSelectedOrganizationId}
          uploadedFiles={uploadedFiles}
          uploading={uploading}
          fileRef={fileRef}
          onFiles={onFiles}
          onRemoveFile={onRemoveFile}
          onKeyDown={onKeyDown}
          onSubmit={onSubmit}
        />
        <p>{disclaimer}</p>
      </div>
    </div>
  );
}

function MessageSkeleton() {
  return <section className="message-skeleton" aria-live="polite" aria-label="正在读取会话消息"><span /><span /><span /><span /></section>;
}

function MessageDetails({ message }: { message: ConversationMessage }) {
  const content = message.content_json && typeof message.content_json === "object" ? message.content_json : {};
  const metrics = Array.isArray(content.metrics) ? content.metrics.filter((item): item is Record<string, unknown> => Boolean(item && typeof item === "object")) : [];
  const sections = Array.isArray(content.sections) ? content.sections.filter((item): item is Record<string, unknown> => Boolean(item && typeof item === "object")) : [];
  const citations = message.citations ?? (Array.isArray(content.citations) ? content.citations.filter((item): item is { label: string; source: string; as_of?: string | null } => Boolean(item && typeof item === "object" && "label" in item && "source" in item)) : []);
  if (!metrics.length && !sections.length && !citations.length && !message.source_data_as_of && !message.model_name) return null;
  return (
    <div className="production-message-details">
      {metrics.length > 0 && <dl className="answer-metric-grid">{metrics.slice(0, 6).map((metric, index) => <div key={`${String(metric.label)}-${index}`}><dt>{String(metric.label ?? "指标")}</dt><dd>{String(metric.value ?? "—")}</dd>{metric.note ? <small>{String(metric.note)}</small> : null}</div>)}</dl>}
      {sections.length > 0 && <div className="answer-section-list">{sections.slice(0, 8).map((section, index) => <section key={`${String(section.title)}-${index}`}><span>{String(index + 1).padStart(2, "0")}</span><div><strong>{String(section.title ?? "分析")}</strong>{section.content || section.detail ? <p>{String(section.content ?? section.detail)}</p> : null}</div></section>)}</div>}
      {(citations.length > 0 || message.source_data_as_of || message.model_name) && <details className="answer-evidence"><summary>来源与处理信息</summary><dl>{message.source_data_as_of && <div><dt>数据截至</dt><dd>{formatTimestamp(message.source_data_as_of)}</dd></div>}{message.model_name && <div><dt>处理模型</dt><dd>{message.model_name}</dd></div>}{citations.map((citation, index) => <div key={`${citation.source}-${index}`}><dt>{citation.label}</dt><dd>{citation.source}{citation.as_of ? ` · ${citation.as_of}` : ""}</dd></div>)}</dl></details>}
    </div>
  );
}

function ProductionComposer({
  id,
  language,
  draft,
  setDraft,
  sending,
  disabled,
  organizationUnits,
  selectedOrganizationId,
  setSelectedOrganizationId,
  uploadedFiles,
  uploading,
  fileRef,
  onFiles,
  onRemoveFile,
  onKeyDown,
  onSubmit,
}: {
  id: string;
  language: UiLanguage;
  draft: string;
  setDraft: (value: string) => void;
  sending: boolean;
  disabled: boolean;
  organizationUnits: OrganizationUnit[];
  selectedOrganizationId: string;
  setSelectedOrganizationId: (value: string) => void;
  uploadedFiles: FileMetadata[];
  uploading: boolean;
  fileRef: React.RefObject<HTMLInputElement | null>;
  onFiles: (event: ChangeEvent<HTMLInputElement>) => void;
  onRemoveFile: (file: FileMetadata) => void;
  onKeyDown: (event: KeyboardEvent<HTMLTextAreaElement>) => void;
  onSubmit: (event: FormEvent) => void;
}) {
  const c = copy[language];
  return (
    <form className="composer workbench-composer home-primary-composer production-composer" onSubmit={onSubmit}>
      <label className="sr-only" htmlFor={id}>输入经营问题</label>
      <textarea id={id} rows={2} maxLength={COMPOSER_MAX_LENGTH} value={draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={onKeyDown} placeholder={disabled ? "尚未配置可分析事业部" : c.placeholder} disabled={disabled} />
      {uploadedFiles.length > 0 && <div className="composer-file-list" aria-label="待发送文件">{uploadedFiles.map((file) => <span key={file.id}><UiIcon name="file" /><strong>{file.original_name}</strong><small>{file.status}</small><button type="button" aria-label={`移除 ${file.original_name}`} onClick={() => onRemoveFile(file)}>×</button></span>)}</div>}
      <div className="composer-footer">
        <div className="composer-tools">
          <input ref={fileRef} className="sr-only" type="file" multiple accept=".pdf,.docx,.xlsx,.pptx" onChange={onFiles} />
          <button type="button" className="composer-tool-button" onClick={() => fileRef.current?.click()} disabled={disabled || uploading}><span aria-hidden="true">＋</span><span>{uploading ? "上传中…" : c.file}</span></button>
          <OrganizationPicker language={language} units={organizationUnits} value={selectedOrganizationId} onChange={setSelectedOrganizationId} disabled={disabled} />
        </div>
        <div className="composer-send">
          {draft.length >= COMPOSER_HINT_THRESHOLD && <span className="composer-character-count">{language === "en" ? `${(COMPOSER_MAX_LENGTH - draft.length).toLocaleString("en")} characters remaining` : `还可输入 ${(COMPOSER_MAX_LENGTH - draft.length).toLocaleString(language)} 字`}</span>}
          <button className="composer-submit-button" type="submit" disabled={disabled || sending || !draft.trim()} aria-label="发送问题">↑</button>
        </div>
      </div>
    </form>
  );
}

function OrganizationPicker({
  language,
  units,
  value,
  onChange,
  disabled,
}: {
  language: UiLanguage;
  units: OrganizationUnit[];
  value: string;
  onChange: (value: string) => void;
  disabled: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const rootRef = useRef<HTMLDivElement>(null);
  const c = copy[language];
  const label = value === ALL_SCOPE_ID ? c.scope : units.find((unit) => unit.id === value)?.name ?? c.scope;
  const filtered = units.filter((unit) => unit.name.toLowerCase().includes(query.trim().toLowerCase()));

  useEffect(() => {
    if (!open) return;
    const close = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const escape = (event: globalThis.KeyboardEvent) => { if (event.key === "Escape") setOpen(false); };
    window.addEventListener("pointerdown", close);
    window.addEventListener("keydown", escape);
    return () => { window.removeEventListener("pointerdown", close); window.removeEventListener("keydown", escape); };
  }, [open]);

  return (
    <div ref={rootRef} className="organization-picker">
      <button type="button" className="composer-tool-button scope" disabled={disabled} aria-haspopup="listbox" aria-expanded={open} onClick={() => setOpen((current) => !current)}><UiIcon name="organization" /><span>{label}</span><span className="organization-picker-chevron" aria-hidden="true">⌄</span></button>
      {open && <div className="organization-popover">
        <header><strong>{language === "en" ? "Business unit scope" : "选择事业部"}</strong></header>
        {units.length > 5 && <label className="organization-search"><UiIcon name="search" /><input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder={language === "en" ? "Search business units" : "搜索事业部"} autoFocus /></label>}
        <div className="organization-options" role="listbox" aria-label="可分析事业部">
          <button type="button" role="option" aria-selected={value === ALL_SCOPE_ID} className={value === ALL_SCOPE_ID ? "selected" : ""} onClick={() => { onChange(ALL_SCOPE_ID); setOpen(false); }}><span className="organization-check">{value === ALL_SCOPE_ID ? "✓" : ""}</span><span>{c.scope}</span><UiIcon name="organization" /></button>
          {filtered.map((unit) => <button type="button" role="option" aria-selected={value === unit.id} className={value === unit.id ? "selected" : ""} key={unit.id} onClick={() => { onChange(unit.id); setOpen(false); }}><span className="organization-check">{value === unit.id ? "✓" : ""}</span><span>{unit.name}</span><UiIcon name="organization" /></button>)}
          {!filtered.length && <p className="organization-empty">没有匹配的事业部</p>}
        </div>
        <footer><small>可选范围由企业管理员配置</small><span>{units.length} 个可分析事业部</span></footer>
      </div>}
    </div>
  );
}

type MemoryUpdateValues = { title?: string; content?: string; status?: "active" | "disabled" | "deleted" };
type MemoryCreateHandler = (title: string, content: string, kind: string, organizationUnitId: string | null) => Promise<boolean>;
type MemoryUpdateHandler = (memory: Memory, values: MemoryUpdateValues) => Promise<boolean>;

function WorkspaceDetailPanel({
  panel,
  onClose,
  report,
  reportLoading,
  reports,
  conversations,
  memories,
  organizationUnits,
  language,
  memoryEnabled,
  setMemoryEnabled,
  onSelectReport,
  onOpenConversation,
  onNewConversation,
  onRenameConversation,
  onArchiveConversation,
  onCreateMemory,
  onUpdateMemory,
  onDeleteMemory,
}: {
  panel: WorkspacePanel;
  onClose: () => void;
  report: Report | null;
  reportLoading: boolean;
  reports: Report[];
  conversations: Conversation[];
  memories: Memory[];
  organizationUnits: OrganizationUnit[];
  language: UiLanguage;
  memoryEnabled: boolean;
  setMemoryEnabled: (value: boolean) => void;
  onSelectReport: (report: Report) => void;
  onOpenConversation: (conversation: Conversation) => void;
  onNewConversation: () => void;
  onRenameConversation: (conversationId: string, title: string) => Promise<void>;
  onArchiveConversation: (conversation: Conversation) => void;
  onCreateMemory: MemoryCreateHandler;
  onUpdateMemory: MemoryUpdateHandler;
  onDeleteMemory: (memory: Memory) => void;
}) {
  const titles: Record<WorkspacePanel, string> = {
    daily: "今日经营简报",
    weekly: "每周高层简报",
    history: "历史会话",
    memory: "长期记忆",
    scope: "可查询范围",
  };
  const reportPanel = panel === "daily" || panel === "weekly";
  return (
    <div className="workspace-panel-layer" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <aside className={`workspace-detail-panel ${reportPanel ? "report-detail-panel" : ""}`} role="dialog" aria-modal="true" aria-labelledby="production-panel-title">
        <header><div><h2 id="production-panel-title">{titles[panel]}</h2><small>工作台下钻</small></div><div className="panel-header-actions"><button type="button" className="panel-close-button" onClick={onClose} aria-label="关闭面板">×</button></div></header>
        <div className="workspace-detail-scroll">
          {reportPanel && <ProductionReportPanel kind={panel} report={report} loading={reportLoading} reports={reports} language={language} onSelectReport={onSelectReport} />}
          {panel === "history" && <ProductionHistoryPanel conversations={conversations} language={language} onOpen={onOpenConversation} onNew={onNewConversation} onRename={onRenameConversation} onArchive={onArchiveConversation} />}
          {panel === "memory" && <ProductionMemoryPanel memories={memories} organizationUnits={organizationUnits} enabled={memoryEnabled} setEnabled={setMemoryEnabled} onCreate={onCreateMemory} onUpdate={onUpdateMemory} onDelete={onDeleteMemory} />}
          {panel === "scope" && <ProductionScopePanel organizationUnits={organizationUnits} />}
        </div>
      </aside>
    </div>
  );
}

function ProductionHistoryPanel({
  conversations,
  language,
  onOpen,
  onNew,
  onRename,
  onArchive,
}: {
  conversations: Conversation[];
  language: UiLanguage;
  onOpen: (conversation: Conversation) => void;
  onNew: () => void;
  onRename: (conversationId: string, title: string) => Promise<void>;
  onArchive: (conversation: Conversation) => void;
}) {
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<"all" | "pinned">("all");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [titleDraft, setTitleDraft] = useState("");
  const filtered = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return conversations
      .filter((item) => !item.archived_at)
      .filter((item) => filter === "all" || Boolean(item.pinned_at))
      .filter((item) => !normalized || item.title.toLowerCase().includes(normalized))
      .sort((first, second) => (second.last_message_at || second.updated_at).localeCompare(first.last_message_at || first.updated_at));
  }, [conversations, filter, query]);

  return (
    <div className="page subpage production-history-page">
      <section className="page-heading split"><div><p className="eyebrow">真实持久化</p><h1>历史会话</h1><p>恢复会话原有的数据范围与消息。重新提问时，系统仍按当前授权范围执行。</p></div><button type="button" className="primary-button" onClick={onNew}>新建会话</button></section>
      <section className="history-controls"><label><span className="sr-only">搜索历史会话</span><input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索会话标题" /></label><div className="filter-tabs"><button type="button" className={filter === "all" ? "active" : ""} onClick={() => setFilter("all")}>全部</button><button type="button" className={filter === "pinned" ? "active" : ""} onClick={() => setFilter("pinned")}>已置顶</button></div><span>共 {filtered.length} 条</span></section>
      {filtered.length ? <div className="history-list">{filtered.map((conversation) => <article key={conversation.id}>
        {editingId === conversation.id ? <form onSubmit={(event) => { event.preventDefault(); void onRename(conversation.id, titleDraft).then(() => setEditingId(null)); }}><input value={titleDraft} maxLength={60} onChange={(event) => setTitleDraft(event.target.value)} autoFocus /><button type="submit">保存</button><button type="button" onClick={() => setEditingId(null)}>取消</button></form> : <button type="button" className="history-main" onClick={() => onOpen(conversation)}><span className="type-badge">{conversation.pinned_at ? "置顶" : "会话"}</span><span><strong>{conversation.title}</strong><small>{conversation.organization_unit_id ? "限定事业部范围" : "全部授权事业部"}</small></span><time>{formatTimestamp(conversation.last_message_at || conversation.updated_at, language)}</time></button>}
        <div className="history-actions"><button type="button" onClick={() => { setEditingId(conversation.id); setTitleDraft(conversation.title); }}>改名</button><button type="button" className="danger" onClick={() => onArchive(conversation)}>归档</button></div>
      </article>)}</div> : <EmptyState title="没有找到相关会话" description="换一个关键词或清除筛选条件。" action="清除筛选" onAction={() => { setQuery(""); setFilter("all"); }} />}
    </div>
  );
}

function ProductionMemoryPanel({
  memories,
  organizationUnits,
  enabled,
  setEnabled,
  onCreate,
  onUpdate,
  onDelete,
}: {
  memories: Memory[];
  organizationUnits: OrganizationUnit[];
  enabled: boolean;
  setEnabled: (value: boolean) => void;
  onCreate: MemoryCreateHandler;
  onUpdate: MemoryUpdateHandler;
  onDelete: (memory: Memory) => void;
}) {
  const [adding, setAdding] = useState(false);
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [kind, setKind] = useState("preference");
  const [organizationUnitId, setOrganizationUnitId] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingTitle, setEditingTitle] = useState("");
  const [editingContent, setEditingContent] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const kindLabels: Record<string, string> = { preference: "表达偏好", metric: "数字偏好", scope: "默认范围", focus: "长期关注", comparison: "比较口径" };

  async function submitNew(event: FormEvent) {
    event.preventDefault();
    if (!title.trim() || !content.trim() || submitting) return;
    setSubmitting(true);
    const saved = await onCreate(title.trim(), content.trim(), kind, organizationUnitId || null);
    setSubmitting(false);
    if (!saved) return;
    setTitle("");
    setContent("");
    setKind("preference");
    setOrganizationUnitId("");
    setAdding(false);
  }

  return (
    <div className="page subpage production-memory-page">
      <section className="page-heading split"><div><p className="eyebrow">由您控制</p><h1>个人长期记忆</h1><p>只保存经确认的稳定偏好。记忆内容对企业管理员和实施人员保持正文隔离。</p></div><button type="button" className="secondary-button" onClick={() => setAdding(true)} disabled={!enabled}>手动新增</button></section>
      <section className="memory-master-setting"><div><strong>长期记忆</strong><p>{enabled ? "后续新消息可使用已确认的偏好。" : "已停止在界面中使用和新增，现有记忆仍保留。"}</p></div><label className="switch"><input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} /><span aria-hidden="true" /><small>{enabled ? "已开启" : "已关闭"}</small></label></section>
      {adding && <form className="inline-form memory-add-form production-memory-form" onSubmit={submitNew}>
        <label className="field"><span>分类</span><select value={kind} onChange={(event) => setKind(event.target.value)}><option value="preference">表达偏好</option><option value="metric">数字偏好</option><option value="scope">默认范围</option><option value="focus">长期关注</option><option value="comparison">比较口径</option></select></label>
        <label className="field"><span>适用范围</span><select value={organizationUnitId} onChange={(event) => setOrganizationUnitId(event.target.value)}><option value="">全部授权范围</option>{organizationUnits.map((unit) => <option key={unit.id} value={unit.id}>{unit.name}</option>)}</select></label>
        <label className="field grow"><span>标题</span><input value={title} maxLength={240} onChange={(event) => setTitle(event.target.value)} placeholder="例如：经营会汇报偏好" /></label>
        <label className="field grow memory-content-field"><span>记忆内容</span><input value={content} maxLength={20000} onChange={(event) => setContent(event.target.value)} placeholder="例如：先给结论，再展开依据" /></label>
        <button type="submit" className="primary-button compact" disabled={!title.trim() || !content.trim() || submitting}>{submitting ? "保存中…" : "保存"}</button><button type="button" className="text-button" onClick={() => setAdding(false)}>取消</button>
      </form>}
      <section className="memory-list-section"><header className="section-header"><div><p className="eyebrow">{memories.length} 条</p><h2>已保存记忆</h2></div></header>
        {memories.length ? <div className="memory-list">{memories.map((memory) => {
          const unit = organizationUnits.find((item) => item.id === memory.organization_unit_id);
          return <article key={memory.id}><span className="type-badge">{kindLabels[memory.kind] || memory.kind}</span>
            {editingId === memory.id ? <form onSubmit={(event) => { event.preventDefault(); setSubmitting(true); void onUpdate(memory, { title: editingTitle.trim(), content: editingContent.trim() }).then((saved) => { setSubmitting(false); if (saved) setEditingId(null); }); }}><input value={editingTitle} maxLength={240} onChange={(event) => setEditingTitle(event.target.value)} /><textarea rows={3} value={editingContent} maxLength={20000} onChange={(event) => setEditingContent(event.target.value)} /><div><button type="submit" className="primary-button compact" disabled={!editingTitle.trim() || !editingContent.trim() || submitting}>保存</button><button type="button" className="text-button" onClick={() => setEditingId(null)}>取消</button></div></form> : <div className="memory-copy"><strong>{memory.title}</strong><p>{memory.content}</p><dl><div><dt>范围</dt><dd>{unit?.name || "全部授权范围"}</dd></div><div><dt>更新</dt><dd>{formatTimestamp(memory.updated_at)}</dd></div><div><dt>版本</dt><dd>v{memory.version}</dd></div></dl></div>}
            <div className="memory-actions"><button type="button" onClick={() => { setEditingId(memory.id); setEditingTitle(memory.title); setEditingContent(memory.content); }}>修改</button><button type="button" className="danger" onClick={() => onDelete(memory)}>删除</button></div>
          </article>;
        })}</div> : <EmptyState title="暂无长期记忆" description="明确表达并确认的稳定偏好会显示在这里。" />}
      </section>
    </div>
  );
}

function ProductionScopePanel({ organizationUnits }: { organizationUnits: OrganizationUnit[] }) {
  return (
    <div className="page subpage production-scope-page">
      <section className="page-heading"><p className="eyebrow">服务端授权结果</p><h1>可查询范围</h1><p>这里仅展示已经接入数据、已启用分析并且当前账号获准访问的事业部。前端不能自行添加。</p></section>
      {organizationUnits.length ? <div className="scope-unit-list">{organizationUnits.map((unit) => <article key={unit.id}><span className="scope-unit-mark" aria-hidden="true" /><div><strong>{unit.name}</strong><small>{unit.code} · {unit.unit_type}</small></div><span className="scope-unit-status">数据可用</span></article>)}</div> : <EmptyState title="尚未配置可分析事业部" description="请由企业管理员完成数据连接、启用分析并授予当前账号访问范围。" />}
      <aside className="scope-security-note"><UiIcon name="shield" /><div><strong>范围由服务端控制</strong><p>创建会话、生成任务和读取资源时都会再次校验权限，不依赖前端选择结果。</p></div></aside>
    </div>
  );
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

function firstText(record: Record<string, unknown>, keys: string[]) {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return "";
}

function recordItems(record: Record<string, unknown>, keys: string[]) {
  for (const key of keys) {
    const value = record[key];
    if (Array.isArray(value)) return value.map(asRecord).filter((item): item is Record<string, unknown> => Boolean(item));
  }
  return [];
}

function ProductionReportPanel({
  kind,
  report,
  loading,
  reports,
  language,
  onSelectReport,
}: {
  kind: "daily" | "weekly";
  report: Report | null;
  loading: boolean;
  reports: Report[];
  language: UiLanguage;
  onSelectReport: (report: Report) => void;
}) {
  const available = reports.filter((item) => item.kind === kind).sort((first, second) => second.period_end.localeCompare(first.period_end));
  if (loading) return <ReportSkeleton />;
  if (!report) return <div className="production-report-empty"><EmptyState title={kind === "daily" ? "尚无今日经营简报" : "尚无每周高层简报"} description="生产环境不会使用固定样本补位。配置经营数据与简报任务后，真实结果会显示在这里。" /></div>;

  const content = report.content ?? {};
  const summary = firstText(content, ["summary", "conclusion", "headline", "overview"]);
  const metrics = recordItems(content, ["metrics", "key_metrics", "indicators"]);
  const changes = recordItems(content, ["changes", "items", "sections", "attention_items", "findings"]);
  const actions = recordItems(content, ["actions", "action_items", "priorities", "recommendations"]);
  const attentionCount = typeof content.attention_items === "number" ? content.attention_items : changes.length;
  const sourceSummary = firstText(content, ["source_summary", "sources", "data_sources"]);
  const definition = firstText(content, ["definition", "methodology", "metric_definition"]);
  const reportLabel = kind === "daily" ? "每日经营变化" : "每周高层经营简报";

  return (
    <article className={`executive-report production-executive-report ${kind}`}>
      {available.length > 1 && <label className="report-version-select"><span>选择简报</span><select value={report.id} onChange={(event) => { const selected = available.find((item) => item.id === event.target.value); if (selected) onSelectReport(selected); }}>{available.map((item) => <option key={item.id} value={item.id}>{item.title} · {formatDate(item.period_end, language)}</option>)}</select></label>}
      <header className="executive-report-lead">
        <div className="executive-report-meta"><div><span>{reportLabel}</span><time>{kind === "daily" ? formatDate(report.period_end, language) : `${formatDate(report.period_start, language)}—${formatDate(report.period_end, language)}`}</time></div><p>{report.data_as_of ? `数据截至 ${formatTimestamp(report.data_as_of, language)}` : "尚未记录数据时间"}{report.published_at ? ` · ${formatTimestamp(report.published_at, language)} 发布` : ""}</p></div>
        <h1>{summary || report.title}</h1>
        <p>{summary ? report.title : "该版本已由正式简报服务创建；未写入的内容不会由前端自行补全。"}</p>
      </header>

      {metrics.length > 0 && <section className={`executive-metric-rail ${kind === "weekly" ? "weekly" : ""}`} aria-label="关键指标">{metrics.slice(0, kind === "weekly" ? 4 : 3).map((metric, index) => <div className="executive-report-metric" key={`${String(metric.label)}-${index}`}><span>{String(metric.label ?? metric.name ?? "指标")}</span><strong>{String(metric.value ?? "—")}</strong><small><i aria-hidden="true" />{String(metric.note ?? metric.change ?? "以简报版本为准")}</small></div>)}</section>}

      {changes.length > 0 && <section className="executive-report-section"><header><span>01—{String(changes.length).padStart(2, "0")}</span><h2>{kind === "daily" ? "关键变化" : "本周经营判断"}</h2></header><div className={`executive-change-list ${kind === "weekly" ? "weekly" : ""}`}>{changes.slice(0, 12).map((item, index) => <div className="executive-change-row" key={`${String(item.title)}-${index}`}><span className="executive-change-index">{String(index + 1).padStart(2, "0")}</span>{kind === "daily" && <span className="executive-change-status">{String(item.status ?? item.tone ?? "关注")}</span>}<span className="executive-change-copy"><strong>{String(item.title ?? item.label ?? `事项 ${index + 1}`)}</strong>{item.detail || item.content || item.description ? <small>{String(item.detail ?? item.content ?? item.description)}</small> : null}</span><span className="executive-change-arrow" aria-hidden="true">·</span></div>)}</div></section>}

      {(actions.length > 0 || attentionCount > 0) && <section className="executive-action-strip"><header><span>需要关注</span><h2>{kind === "daily" ? "需要确认" : "下一阶段优先事项"}</h2></header>{actions.length ? <ol>{actions.slice(0, 8).map((item, index) => <li key={`${String(item.title)}-${index}`}><span>{index + 1}</span><strong>{String(item.title ?? item.content ?? item.description ?? `事项 ${index + 1}`)}</strong></li>)}</ol> : <p className="report-attention-count">简报记录了 {attentionCount} 项待确认事项，但当前版本尚未写入可展示的明细。</p>}</section>}

      {!metrics.length && !changes.length && !actions.length && !summary && <section className="production-report-content-empty"><span aria-hidden="true">—</span><div><strong>简报正文尚未写入</strong><p>当前只有正式简报元数据。生产前端不会调用 Demo 样本补齐指标或结论。</p></div></section>}

      <details className="executive-report-provenance"><summary>数据范围、来源与生成信息</summary><dl><div><dt>范围</dt><dd>{report.organization_unit_id ? "限定事业部" : "全部授权事业部"}</dd></div><div><dt>周期</dt><dd>{formatDate(report.period_start, language)} 至 {formatDate(report.period_end, language)}</dd></div><div><dt>版本</dt><dd>{report.latest_version ? `v${report.latest_version}` : "尚无正文版本"}</dd></div>{sourceSummary && <div><dt>来源</dt><dd>{sourceSummary}</dd></div>}{definition && <div><dt>口径</dt><dd>{definition}</dd></div>}</dl></details>
    </article>
  );
}

function ReportSkeleton() {
  return <div className="report-skeleton" aria-live="polite" aria-label="正在读取简报"><span /><span /><span /><div><i /><i /><i /></div><span /><span /></div>;
}

function PreferencesWindow({
  view,
  setView,
  onClose,
  me,
  initials,
  selectedScopeLabel,
  organizationUnits,
  theme,
  setTheme,
  language,
  profilePreferences,
  setProfilePreferences,
  memoryEnabled,
  setMemoryEnabled,
  memories,
  onCreateMemory,
  onUpdateMemory,
  onDeleteMemory,
}: {
  view: PreferencesView;
  setView: (view: PreferencesView) => void;
  onClose: () => void;
  me: AuthMe;
  initials: string;
  selectedScopeLabel: string;
  organizationUnits: OrganizationUnit[];
  theme: ThemePreference;
  setTheme: (theme: ThemePreference) => void;
  language: UiLanguage;
  profilePreferences: ProfilePreferences;
  setProfilePreferences: (value: ProfilePreferences) => void;
  memoryEnabled: boolean;
  setMemoryEnabled: (value: boolean) => void;
  memories: Memory[];
  onCreateMemory: MemoryCreateHandler;
  onUpdateMemory: MemoryUpdateHandler;
  onDeleteMemory: (memory: Memory) => void;
}) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const [editing, setEditing] = useState(false);
  const [salutation, setSalutation] = useState(profilePreferences.salutation);
  const [amountUnit, setAmountUnit] = useState(profilePreferences.amountUnit);
  const labels = language === "en"
    ? { title: "Personal settings", back: "Back to workspace", profile: "Profile", appearance: "Appearance", memory: "Long-term memory", close: "Close" }
    : language === "zh-TW"
      ? { title: "個人設定", back: "返回工作台", profile: "個人資料", appearance: "外觀", memory: "長期記憶", close: "關閉" }
      : { title: "个人设置", back: "返回工作台", profile: "个人资料", appearance: "外观", memory: "长期记忆", close: "关闭" };

  useEffect(() => {
    const previouslyFocused = document.activeElement as HTMLElement | null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    window.requestAnimationFrame(() => dialogRef.current?.querySelector<HTMLElement>("button, input, select")?.focus());
    const handleKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key !== "Tab" || !dialogRef.current) return;
      const focusable = Array.from(dialogRef.current.querySelectorAll<HTMLElement>("button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [href]"));
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", handleKeyDown);
      previouslyFocused?.focus();
    };
  }, [onClose]);

  function savePreferences(event: FormEvent) {
    event.preventDefault();
    setProfilePreferences({ salutation: salutation.trim() || "董事长", amountUnit });
    setEditing(false);
  }

  return (
    <div className="preferences-layer" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <div ref={dialogRef} className="preferences-window" role="dialog" aria-modal="true" aria-labelledby="production-preferences-title">
        <aside className="preferences-sidebar">
          <div className="window-dots" aria-hidden="true"><i /><i /><i /></div>
          <button type="button" className="preferences-back" onClick={onClose}><span aria-hidden="true">←</span>{labels.back}</button>
          <div className="preferences-heading"><small>{labels.title}</small><strong id="production-preferences-title">{preferredDisplayName(me)}</strong></div>
          <nav aria-label={labels.title}>
            <button type="button" className={view === "profile" ? "active" : ""} onClick={() => setView("profile")}><UiIcon name="profile" /><span>{labels.profile}</span></button>
            <button type="button" className={view === "appearance" ? "active" : ""} onClick={() => setView("appearance")}><UiIcon name="appearance" /><span>{labels.appearance}</span></button>
            <button type="button" className={view === "memory" ? "active" : ""} onClick={() => setView("memory")}><UiIcon name="memory" /><span>{labels.memory}</span></button>
          </nav>
          <div className="preferences-privacy"><UiIcon name="shield" /><span><strong>仅您可见</strong><small>长期记忆正文不会向企业管理员展示</small></span></div>
        </aside>

        <main className="preferences-main">
          <header className="preferences-main-header"><div><small>{labels.title}</small><strong>{view === "profile" ? labels.profile : view === "appearance" ? labels.appearance : labels.memory}</strong></div><button type="button" onClick={onClose} aria-label={labels.close}>×</button></header>

          {view === "profile" && <div className="profile-settings-pane production-profile-pane">
            <section className="profile-hero"><span className="profile-hero-avatar" aria-hidden="true">{initials}</span><div><h1>{preferredDisplayName(me)}</h1><p>{profilePreferences.salutation} · {selectedScopeLabel}</p><small>{me.user.email}</small></div>{!editing && <button type="button" className="profile-edit-button" onClick={() => { setSalutation(profilePreferences.salutation); setAmountUnit(profilePreferences.amountUnit); setEditing(true); }}><UiIcon name="edit" />编辑服务偏好</button>}</section>
            <section className="profile-summary-rail" aria-label="账号摘要"><div><small>专属称呼</small><strong>{profilePreferences.salutation}</strong><span>用于首页问候</span></div><div><small>可分析事业部</small><strong>{organizationUnits.length} 个</strong><span>由服务端授权决定</span></div><div><small>默认金额单位</small><strong>{profilePreferences.amountUnit}</strong><span>用于界面表达偏好</span></div></section>
            {editing ? <form className="profile-edit-form" onSubmit={savePreferences}><div className="profile-section-title"><span>编辑服务偏好</span><small>不会改变账号和数据权限</small></div><div className="profile-form-grid"><label><span>专属称呼</span><input value={salutation} maxLength={24} onChange={(event) => setSalutation(event.target.value)} placeholder="例如：张总、Ryan" autoFocus /></label><label><span>默认金额单位</span><select value={amountUnit} onChange={(event) => setAmountUnit(event.target.value)}><option>万元</option><option>亿元</option><option>元</option></select></label></div><div className="profile-form-actions"><button type="button" onClick={() => setEditing(false)}>取消</button><button type="submit">保存偏好</button></div></form> : <div className="profile-detail-grid"><section><div className="profile-section-title"><span>服务偏好</span><small>只影响界面表达</small></div><dl><div><dt>问候预览</dt><dd>早上好，{profilePreferences.salutation}</dd></div><div><dt>回答原则</dt><dd>先给结论，再展开依据</dd></div><div><dt>金额表达</dt><dd>{profilePreferences.amountUnit}</dd></div></dl></section><section><div className="profile-section-title"><span>账号与安全</span><small>正式身份信息</small></div><dl><div><dt>登录邮箱</dt><dd>{me.user.email}</dd></div><div><dt>角色</dt><dd>{me.user.role}</dd></div><div><dt>账号状态</dt><dd><span className="profile-status-dot" />正常</dd></div></dl></section></div>}
            <p className="production-profile-note">显示名称与登录邮箱属于正式身份信息，由企业管理员维护；这里不会用浏览器数据覆盖。</p>
          </div>}

          {view === "appearance" && <div className="appearance-settings-pane"><header><p className="eyebrow">界面显示</p><h1>选择适合您的外观</h1><p>外观偏好只保存在当前设备，不影响会话、数据或长期记忆。</p></header><div className="appearance-options" role="radiogroup" aria-label="外观模式">{([
            ["system", "跟随系统", "随电脑的深浅色自动切换", "system"],
            ["light", "白天", "温暖克制的浅色工作台", "light"],
            ["dark", "夜间", "低眩光的深色工作台", "dark"],
          ] as const).map(([id, title, description, preview]) => <button type="button" role="radio" aria-checked={theme === id} className={theme === id ? "selected" : ""} key={id} onClick={() => setTheme(id)}><span className={`appearance-preview ${preview}`} aria-hidden="true"><span /><span /><span /></span><span className="appearance-option-copy"><i><UiIcon name={id} /></i><span><strong>{title}</strong><small>{description}</small></span></span><i className="appearance-radio" aria-hidden="true" /></button>)}</div><section className="appearance-composer-preview"><small>输入框预览</small><div><span>向 AI 秘书提问经营数据</span><i aria-hidden="true">↑</i></div><p>使用极轻的暖色阴影和清晰边界，保持克制的立体感。</p></section></div>}

          {view === "memory" && <div className="preferences-memory-pane"><ProductionMemoryPanel memories={memories} organizationUnits={organizationUnits} enabled={memoryEnabled} setEnabled={setMemoryEnabled} onCreate={onCreateMemory} onUpdate={onUpdateMemory} onDelete={onDeleteMemory} /></div>}
        </main>
      </div>
    </div>
  );
}

function ProjectDialog({
  state,
  project,
  organizationUnits,
  onClose,
  onSave,
}: {
  state: ProjectDialogState;
  project: Project | null;
  organizationUnits: OrganizationUnit[];
  onClose: () => void;
  onSave: (name: string, description: string, organizationUnitId: string) => Promise<boolean>;
}) {
  const [name, setName] = useState(project?.name ?? "");
  const [description, setDescription] = useState(project?.description ?? "");
  const [organizationUnitId, setOrganizationUnitId] = useState(project?.organization_unit_id ?? ALL_SCOPE_ID);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const dialogRef = useRef<HTMLElement>(null);
  const editing = state.mode === "edit";

  useEffect(() => {
    const previouslyFocused = document.activeElement as HTMLElement | null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    window.requestAnimationFrame(() => dialogRef.current?.querySelector<HTMLInputElement>("input")?.focus());
    const handleKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); onClose(); return; }
      if (event.key !== "Tab" || !dialogRef.current) return;
      const focusable = Array.from(dialogRef.current.querySelectorAll<HTMLElement>("button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled])"));
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => { document.body.style.overflow = previousOverflow; window.removeEventListener("keydown", handleKeyDown); previouslyFocused?.focus(); };
  }, [onClose]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!name.trim()) { setError("请输入项目名称。"); return; }
    setSubmitting(true);
    const saved = await onSave(name.trim(), description.trim(), organizationUnitId);
    setSubmitting(false);
    if (!saved) setError("项目暂时未能保存，请检查页面提示后重试。");
  }

  return (
    <div className="project-dialog-layer" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section ref={dialogRef} className="project-dialog" role="dialog" aria-modal="true" aria-labelledby="production-project-dialog-title">
        <header><div><small>{editing ? "项目设置" : "工作项目"}</small><h2 id="production-project-dialog-title">{editing ? "编辑项目" : "创建项目"}</h2></div><button type="button" aria-label="关闭项目窗口" onClick={onClose}>×</button></header>
        <form onSubmit={submit}>
          <label className="project-name-field"><span>项目名称</span><span className="project-name-input"><UiIcon name="folder" /><input value={name} maxLength={200} onChange={(event) => { setName(event.target.value); setError(""); }} placeholder="例如：年度经营计划" autoComplete="off" /></span></label>
          <label className="project-description-field"><span>项目说明 <small>可选</small></span><textarea value={description} maxLength={4000} rows={3} onChange={(event) => setDescription(event.target.value)} placeholder="说明该项目持续关注的经营主题或范围" /><small>创建后，可直接从项目中开始一条新会话。</small></label>
          <label className="project-scope-field"><span>默认事业部范围</span><select value={organizationUnitId} onChange={(event) => setOrganizationUnitId(event.target.value)}><option value={ALL_SCOPE_ID}>全部授权事业部</option>{organizationUnits.map((unit) => <option key={unit.id} value={unit.id}>{unit.name}</option>)}</select><small>可选项来自企业管理员配置，不会扩大账号权限。</small></label>
          {error && <p className="project-dialog-error" role="alert">{error}</p>}
          <footer><button type="button" className="secondary-button" onClick={onClose}>取消</button><button type="submit" className="primary-button" disabled={!name.trim() || submitting}>{submitting ? "保存中…" : editing ? "保存修改" : "创建项目"}</button></footer>
        </form>
      </section>
    </div>
  );
}

function ConfirmDialog({ state, onCancel, onConfirm }: { state: ConfirmState; onCancel: () => void; onConfirm: () => void }) {
  const dialogRef = useRef<HTMLElement>(null);
  useEffect(() => {
    const previouslyFocused = document.activeElement as HTMLElement | null;
    window.requestAnimationFrame(() => dialogRef.current?.querySelector<HTMLButtonElement>("button")?.focus());
    const handleKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); onCancel(); return; }
      if (event.key !== "Tab" || !dialogRef.current) return;
      const focusable = Array.from(dialogRef.current.querySelectorAll<HTMLButtonElement>("button:not([disabled])"));
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => { window.removeEventListener("keydown", handleKeyDown); previouslyFocused?.focus(); };
  }, [onCancel]);
  return <div className="overlay dialog-overlay" role="presentation"><section ref={dialogRef} className="confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="production-confirm-title"><span className={`confirm-mark ${state.tone === "danger" ? "danger" : ""}`} aria-hidden="true">!</span><h2 id="production-confirm-title">{state.title}</h2><p>{state.description}</p><div><button type="button" className="secondary-button" onClick={onCancel}>取消</button><button type="button" className={state.tone === "danger" ? "danger-button" : "primary-button"} onClick={onConfirm}>{state.confirmLabel}</button></div></section></div>;
}

function EmptyState({ title, description, action, onAction }: { title: string; description: string; action?: string; onAction?: () => void }) {
  return <section className="empty-state"><span aria-hidden="true">∅</span><h2>{title}</h2><p>{description}</p>{action && onAction && <button type="button" className="secondary-button" onClick={onAction}>{action}</button>}</section>;
}

function Toast({ message }: { message: string }) {
  return <div className="toast" role="status" aria-live="polite"><span className="status-dot positive" aria-hidden="true" />{message}</div>;
}

type UiIconName = "settings" | "language" | "logout" | "chevron" | "search" | "profile" | "appearance" | "memory" | "system" | "light" | "dark" | "edit" | "shield" | "pin" | "archive" | "remove" | "folder" | "organization" | "file";

function UiIcon({ name }: { name: UiIconName }) {
  const paths: Record<UiIconName, ReactNode> = {
    settings: <><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .34 1.88l.06.06-1.9 1.9-.06-.06a1.7 1.7 0 0 0-1.88-.34 1.7 1.7 0 0 0-1 1.55V20h-2.7v-.09a1.7 1.7 0 0 0-1.07-1.55 1.7 1.7 0 0 0-1.88.34l-.06.06-1.9-1.9.06-.06A1.7 1.7 0 0 0 7.75 15a1.7 1.7 0 0 0-1.55-1H6v-2.7h.09a1.7 1.7 0 0 0 1.55-1.07 1.7 1.7 0 0 0-.34-1.88l-.06-.06 1.9-1.9.06.06a1.7 1.7 0 0 0 1.88.34A1.7 1.7 0 0 0 12.1 5.2V5h2.7v.09a1.7 1.7 0 0 0 1.07 1.55 1.7 1.7 0 0 0 1.88-.34l.06-.06 1.9 1.9-.06.06a1.7 1.7 0 0 0-.34 1.88A1.7 1.7 0 0 0 20.8 11v2.7h-.09A1.7 1.7 0 0 0 19.4 15Z" /></>,
    language: <><circle cx="12" cy="12" r="8.5" /><path d="M3.8 12h16.4M12 3.5c2.3 2.4 3.4 5.2 3.4 8.5S14.3 18.1 12 20.5M12 3.5C9.7 5.9 8.6 8.7 8.6 12s1.1 6.1 3.4 8.5" /></>,
    logout: <><path d="M10 5H6.5A2.5 2.5 0 0 0 4 7.5v9A2.5 2.5 0 0 0 6.5 19H10" /><path d="m14 8 4 4-4 4M18 12H9" /></>,
    chevron: <path d="m9 6 6 6-6 6" />,
    search: <><circle cx="10.5" cy="10.5" r="6" /><path d="m15 15 4.5 4.5" /></>,
    profile: <><circle cx="12" cy="8" r="3.5" /><path d="M5.5 19c.8-3.2 3-5 6.5-5s5.7 1.8 6.5 5" /></>,
    appearance: <><circle cx="12" cy="12" r="8.5" /><path d="M12 3.5v17M3.5 12h17M6 6l12 12M18 6 6 18" /></>,
    memory: <><path d="M7 5.5h8.5A2.5 2.5 0 0 1 18 8v10l-6-3-6 3V6.5A1 1 0 0 1 7 5.5Z" /><path d="M9 9h6" /></>,
    system: <><rect x="3.5" y="4.5" width="17" height="11" rx="2" /><path d="M9 19.5h6M12 15.5v4" /></>,
    light: <><circle cx="12" cy="12" r="3.5" /><path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M18.7 5.3l-1.4 1.4M6.7 17.3l-1.4 1.4" /></>,
    dark: <path d="M19.5 15.5A8 8 0 0 1 8.5 4.5a8.2 8.2 0 1 0 11 11Z" />,
    edit: <><path d="m5 16-.7 3.7L8 19l9.8-9.8-3-3L5 16Z" /><path d="m13.8 7.2 3 3" /></>,
    shield: <><path d="M12 3.5 19 6v5.4c0 4.2-2.3 7.1-7 9.1-4.7-2-7-4.9-7-9.1V6l7-2.5Z" /><path d="m9 12 2 2 4-4" /></>,
    pin: <><path d="m14 4 6 6-3 1-3.5 3.5 1 3-1.5 1.5-4-4-4.5 4.5" /><path d="m7 8 3 1L13.5 5l.5-1Z" /></>,
    archive: <><rect x="4" y="5" width="16" height="4" rx="1" /><path d="M6 9v9.5h12V9M10 13h4" /></>,
    remove: <><path d="M5 5l14 14M19 5 5 19" /></>,
    folder: <><path d="M3.5 7.5h6l2-2h8a1.5 1.5 0 0 1 1.5 1.5v10.5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V9a1.5 1.5 0 0 1 .5-1.5Z" /><path d="M3.5 9h17.5" /></>,
    organization: <><path d="M5 20V9l4-3v14M9 20h10V4l-6 3v13M3 20h18" /><path d="M12 10h2M12 14h2M16 8h1M16 12h1" /></>,
    file: <><path d="M7 3.5h7l4 4V20H7Z" /><path d="M14 3.5V8h4M10 12h5M10 15h5" /></>,
  };
  return <svg className="ui-icon" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round">{paths[name]}</svg>;
}
