"use client";

import {
  ChangeEvent,
  FormEvent,
  KeyboardEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { ApiError, humanizeApiError } from "./api-client";
import {
  loadProductionBootstrap,
  productionServices,
} from "./services";
import type {
  AuthMe,
  Conversation,
  ConversationMessage,
  FileMetadata,
  OrganizationUnit,
  ProductionBootstrap,
} from "./types";

type SessionState =
  | { status: "checking" }
  | { status: "anonymous" }
  | { status: "password-change"; me: AuthMe; currentPassword: string }
  | { status: "ready"; bootstrap: ProductionBootstrap }
  | { status: "error"; message: string };

const COMPOSER_MAX_LENGTH = 8000;

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

function greetingForCurrentHour(timezone: string) {
  let hour = new Date().getHours();
  try {
    const part = new Intl.DateTimeFormat("en-US", {
      hour: "2-digit",
      hour12: false,
      timeZone: timezone || "Asia/Shanghai",
    }).formatToParts(new Date()).find((item) => item.type === "hour")?.value;
    if (part) hour = Number(part) % 24;
  } catch {
    // The browser local hour is a safe presentational fallback.
  }
  if (hour < 6) return "夜深了";
  if (hour < 12) return "早上好";
  if (hour < 18) return "下午好";
  return "晚上好";
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

export function ProductionApplication() {
  const [session, setSession] = useState<SessionState>({ status: "checking" });

  const refresh = useCallback(async () => {
    setSession({ status: "checking" });
    try {
      const bootstrap = await loadProductionBootstrap();
      if (bootstrap.me.user.password_change_required) {
        setSession({ status: "password-change", me: bootstrap.me, currentPassword: "" });
      } else {
        setSession({ status: "ready", bootstrap });
      }
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        setSession({ status: "anonymous" });
        return;
      }
      setSession({ status: "error", message: humanizeApiError(error) });
    }
  }, []);

  useEffect(() => {
    let active = true;
    void loadProductionBootstrap()
      .then((bootstrap) => {
        if (!active) return;
        if (bootstrap.me.user.password_change_required) {
          setSession({ status: "password-change", me: bootstrap.me, currentPassword: "" });
        } else {
          setSession({ status: "ready", bootstrap });
        }
      })
      .catch((error: unknown) => {
        if (!active) return;
        if (error instanceof ApiError && error.status === 401) {
          setSession({ status: "anonymous" });
        } else {
          setSession({ status: "error", message: humanizeApiError(error) });
        }
      });
    return () => {
      active = false;
    };
  }, []);

  if (session.status === "checking") {
    return <ProductionStatus title="正在验证安全会话" description="正在连接本机生产服务，请稍候。" />;
  }
  if (session.status === "error") {
    return <ProductionStatus title="暂时无法进入工作台" description={session.message} action="重新连接" onAction={() => void refresh()} />;
  }
  if (session.status === "anonymous") {
    return (
      <ProductionLogin
        onAuthenticated={(me, currentPassword) => {
          if (me.user.password_change_required) {
            setSession({ status: "password-change", me, currentPassword });
          } else {
            void refresh();
          }
        }}
      />
    );
  }
  if (session.status === "password-change") {
    return (
      <ProductionPasswordChange
        me={session.me}
        initialCurrentPassword={session.currentPassword}
        onComplete={() => void refresh()}
        onLogout={async () => {
          try {
            await productionServices.auth.logout();
          } finally {
            setSession({ status: "anonymous" });
          }
        }}
      />
    );
  }
  if (session.bootstrap.me.user.role !== "executive") {
    return (
      <ProductionStatus
        title="管理身份已验证"
        description="当前入口只服务高层本人。管理账号不会加载高层会话、记忆或文件正文；管理功能将通过独立受控入口开放。"
        action="退出登录"
        onAction={() => {
          void productionServices.auth.logout().finally(() => {
            setSession({ status: "anonymous" });
          });
        }}
      />
    );
  }
  return (
    <ProductionWorkspace
      initialBootstrap={session.bootstrap}
      onSessionExpired={() => setSession({ status: "anonymous" })}
      onReload={refresh}
    />
  );
}

function ProductionStatus({
  title,
  description,
  action,
  onAction,
}: {
  title: string;
  description: string;
  action?: string;
  onAction?: () => void;
}) {
  return (
    <main className="login-page" data-app-mode="production">
      <section className="login-context" aria-labelledby="production-status-title">
        <div className="login-brand"><span className="brand-glyph" aria-hidden="true">董</span><span>董事长 AI 秘书</span></div>
        <div className="login-statement">
          <p className="eyebrow">本机生产环境</p>
          <h1 id="production-status-title">可信经营服务正在准备。</h1>
          <p>生产模式只读取已授权的企业数据，不会使用演示样本补位。</p>
        </div>
      </section>
      <section className="login-panel" aria-live="polite">
        <div className="login-form">
          <div className="form-heading"><p className="eyebrow">服务状态</p><h2>{title}</h2><p>{description}</p></div>
          {action && onAction && <button className="primary-button wide" type="button" onClick={onAction}>{action}</button>}
        </div>
      </section>
    </main>
  );
}

function ProductionLogin({
  onAuthenticated,
}: {
  onAuthenticated: (me: AuthMe, currentPassword: string) => void;
}) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (submitting) return;
    setSubmitting(true);
    setError("");
    try {
      const login = await productionServices.auth.login(email.trim(), password);
      const me: AuthMe = {
        user: login.user,
        enterprise: { id: "", name: "企业工作台", slug: "" },
        scopes: [],
        csrf_token: login.csrf_token,
        app_env: login.app_env,
        app_mode: login.app_mode,
      };
      onAuthenticated(me, password);
    } catch (loginError) {
      setError(humanizeApiError(loginError));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="login-page" data-app-mode="production">
      <a className="skip-link" href="#production-login-form">跳到登录表单</a>
      <section className="login-context" aria-labelledby="production-product-title">
        <div className="login-brand"><span className="brand-glyph" aria-hidden="true">董</span><span>董事长 AI 秘书</span></div>
        <div className="login-statement">
          <p className="eyebrow">私有化经营工作入口</p>
          <h1 id="production-product-title">先确认身份，再进入经营现场。</h1>
          <p>会话、文件与经营范围均受企业权限控制，并记录必要的安全审计。</p>
        </div>
        <dl className="login-principles">
          <div><dt>01</dt><dd><strong>独立身份</strong><span>企业预建账号与受控会话</span></dd></div>
          <div><dt>02</dt><dd><strong>最小权限</strong><span>只展示已授权事业部</span></dd></div>
          <div><dt>03</dt><dd><strong>真实数据</strong><span>生产模式不使用演示样本</span></dd></div>
        </dl>
      </section>
      <section className="login-panel" aria-labelledby="production-login-title">
        <form id="production-login-form" className="login-form" onSubmit={submit}>
          <div className="form-heading"><p className="eyebrow">企业用户</p><h2 id="production-login-title">登录</h2><p>使用企业管理员为您开通的账号。</p></div>
          <label className="field"><span>企业邮箱</span><input type="email" value={email} onChange={(event) => setEmail(event.target.value)} autoComplete="username" autoFocus /></label>
          <label className="field password-field">
            <span>密码</span>
            <span className="input-with-action"><input type={showPassword ? "text" : "password"} value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="current-password" /><button type="button" onClick={() => setShowPassword((current) => !current)}>{showPassword ? "隐藏" : "显示"}</button></span>
          </label>
          {error && <p className="form-error" role="alert">{error}</p>}
          <button className="primary-button wide" type="submit" disabled={!email.trim() || !password || submitting}>{submitting ? "正在验证…" : "登录"}</button>
          <p className="contact-note">首版不开放自行注册。无法登录时，请联系企业管理员。</p>
        </form>
      </section>
    </main>
  );
}

function ProductionPasswordChange({
  me,
  initialCurrentPassword,
  onComplete,
  onLogout,
}: {
  me: AuthMe;
  initialCurrentPassword: string;
  onComplete: () => void;
  onLogout: () => void;
}) {
  const [currentPassword, setCurrentPassword] = useState(initialCurrentPassword);
  const [newPassword, setNewPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (newPassword.length < 12 || !/[A-Za-z]/.test(newPassword) || !/\d/.test(newPassword)) {
      setError("新密码至少 12 位，并同时包含字母和数字。");
      return;
    }
    if (newPassword !== confirmation) {
      setError("两次输入的新密码不一致。");
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      await productionServices.auth.changePassword(currentPassword, newPassword);
      onComplete();
    } catch (changeError) {
      setError(humanizeApiError(changeError));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="login-page" data-app-mode="production">
      <section className="login-context" aria-labelledby="password-change-context">
        <div className="login-brand"><span className="brand-glyph" aria-hidden="true">董</span><span>董事长 AI 秘书</span></div>
        <div className="login-statement"><p className="eyebrow">首次登录保护</p><h1 id="password-change-context">临时密码不能进入经营页面。</h1><p>完成密码更新后，系统才会加载您获准查看的企业范围。</p></div>
      </section>
      <section className="login-panel" aria-labelledby="password-change-title">
        <form className="login-form" onSubmit={submit}>
          <div className="form-heading"><p className="eyebrow">{me.user.email}</p><h2 id="password-change-title">设置正式密码</h2><p>至少 12 位，并包含字母和数字。</p></div>
          <label className="field"><span>当前临时密码</span><input type="password" value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} autoComplete="current-password" /></label>
          <label className="field"><span>新密码</span><input type="password" value={newPassword} onChange={(event) => setNewPassword(event.target.value)} autoComplete="new-password" /></label>
          <label className="field"><span>再次确认</span><input type="password" value={confirmation} onChange={(event) => setConfirmation(event.target.value)} autoComplete="new-password" /></label>
          {error && <p className="form-error" role="alert">{error}</p>}
          <button className="primary-button wide" type="submit" disabled={!currentPassword || !newPassword || !confirmation || submitting}>{submitting ? "正在保存…" : "保存并进入工作台"}</button>
          <button className="text-button" type="button" onClick={onLogout}>退出登录</button>
        </form>
      </section>
    </main>
  );
}

function ProductionWorkspace({
  initialBootstrap,
  onSessionExpired,
  onReload,
}: {
  initialBootstrap: ProductionBootstrap;
  onSessionExpired: () => void;
  onReload: () => Promise<void>;
}) {
  const [bootstrap, setBootstrap] = useState(initialBootstrap);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ConversationMessage[]>([]);
  const [messagesLoading, setMessagesLoading] = useState(false);
  const [messagesError, setMessagesError] = useState("");
  const [draft, setDraft] = useState("");
  const [selectedOrganizationId, setSelectedOrganizationId] = useState(
    initialBootstrap.organizationUnits[0]?.id ?? "",
  );
  const [uploadedFiles, setUploadedFiles] = useState<FileMetadata[]>([]);
  const [uploading, setUploading] = useState(false);
  const [sending, setSending] = useState(false);
  const [workspaceError, setWorkspaceError] = useState("");
  const [accountMenuOpen, setAccountMenuOpen] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const organizationUnits = bootstrap.organizationUnits;
  const effectiveSelectedOrganizationId = organizationUnits.some((unit) => unit.id === selectedOrganizationId)
    ? selectedOrganizationId
    : organizationUnits[0]?.id ?? "";
  const selectedOrganization = organizationUnits.find((unit) => unit.id === effectiveSelectedOrganizationId) ?? null;
  const activeConversation = bootstrap.conversations.find((item) => item.id === activeConversationId) ?? null;
  const me = bootstrap.me;
  const latestDailyReport = bootstrap.reports
    .filter((report) => report.kind === "daily" && (report.status === "published" || report.status === "completed"))
    .sort((first, second) => String(second.published_at).localeCompare(String(first.published_at)))[0];

  async function runRequest(action: () => Promise<void>) {
    try {
      setWorkspaceError("");
      await action();
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        onSessionExpired();
        return;
      }
      setWorkspaceError(humanizeApiError(error));
    }
  }

  async function openConversation(conversation: Conversation) {
    setActiveConversationId(conversation.id);
    setMessages([]);
    setMessagesError("");
    setMessagesLoading(true);
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
  }

  function newConversation() {
    setActiveConversationId(null);
    setMessages([]);
    setMessagesError("");
    setDraft("");
    setUploadedFiles([]);
    setSidebarOpen(false);
  }

  async function uploadFiles(event: ChangeEvent<HTMLInputElement>) {
    const incoming = Array.from(event.target.files ?? []);
    event.target.value = "";
    if (!incoming.length) return;
    setUploading(true);
    await runRequest(async () => {
      const results: FileMetadata[] = [];
      for (const file of incoming.slice(0, 10)) {
        if (file.size > 50 * 1024 * 1024) throw new Error(`${file.name} 超过 50 MB 限制。`);
        results.push(await productionServices.files.upload(file, activeConversationId ?? undefined));
      }
      setUploadedFiles((current) => [...current, ...results]);
    });
    setUploading(false);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const content = draft.trim();
    if (!content || sending || !selectedOrganization) return;
    setSending(true);
    await runRequest(async () => {
      let conversationId = activeConversationId;
      let createdConversation: Conversation | null = null;
      if (!conversationId) {
        createdConversation = await productionServices.conversations.create({
          title: content.slice(0, 28),
          organization_unit_id: selectedOrganization.id,
        });
        conversationId = createdConversation.id;
        setActiveConversationId(conversationId);
        setBootstrap((current) => ({
          ...current,
          conversations: [createdConversation as Conversation, ...current.conversations],
        }));
      }
      const message = await productionServices.conversations.sendMessage(
        conversationId,
        content,
        uploadedFiles.filter((file) => file.status === "ready" || file.status === "partial").map((file) => file.id),
      );
      setMessages((current) => [...current, message]);
      setDraft("");
      setUploadedFiles([]);
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

  const businessDataReady = organizationUnits.length > 0;
  const userInitials = preferredDisplayName(me).slice(0, 2).toUpperCase();
  const optionalWarning = Object.values(bootstrap.optionalErrors)[0];

  return (
    <div className={`product-shell workbench-shell production-workbench ${sidebarOpen ? "sidebar-open" : ""}`} data-app-mode={me.app_mode} data-app-environment={me.app_env}>
      <a className="skip-link" href="#main-content">跳到主要内容</a>
      {(workspaceError || optionalWarning) && <div className="network-banner" role="status"><span>{workspaceError || `部分辅助能力暂不可用：${optionalWarning}`}</span></div>}
      <aside className="workspace-sidebar" aria-label="工作台侧栏">
        <header className="sidebar-brand-row">
          <button className="sidebar-brand" type="button" onClick={newConversation}><span className="brand-glyph" aria-hidden="true">董</span><span className="sidebar-label"><strong>董事长 AI 秘书</strong><small>{me.enterprise.name}</small></span></button>
        </header>
        <div className="sidebar-scroll-region">
          <button className="new-conversation-button" type="button" onClick={newConversation}><span aria-hidden="true">＋</span><strong className="sidebar-label">新建会话</strong><kbd className="sidebar-label">⌘ K</kbd></button>
          <nav className="workspace-navigation" aria-label="经营工作台功能">
            <button type="button" onClick={newConversation}><span aria-hidden="true">今</span><strong className="sidebar-label">经营问数</strong></button>
            <button type="button" disabled={!bootstrap.reports.some((report) => report.kind === "daily")}><span aria-hidden="true">日</span><strong className="sidebar-label">今日经营简报</strong></button>
            <button type="button" disabled={!bootstrap.reports.some((report) => report.kind === "weekly")}><span aria-hidden="true">周</span><strong className="sidebar-label">每周高层简报</strong></button>
            <button type="button" disabled={!bootstrap.memories.length}><span aria-hidden="true">记</span><strong className="sidebar-label">长期记忆</strong></button>
          </nav>
          <div className="sidebar-sections">
            <section className="sidebar-section" aria-labelledby="production-projects-title">
              <header className="sidebar-section-header"><span id="production-projects-title">项目</span></header>
              <div className="sidebar-list">
                {bootstrap.projects.length ? bootstrap.projects.map((project) => <div className="sidebar-project" key={project.id}><div className="sidebar-project-row-shell"><button className="sidebar-project-button" type="button"><span className="sidebar-disclosure" aria-hidden="true">›</span><span className="sidebar-project-mark" aria-hidden="true" /><strong>{project.name}</strong></button></div></div>) : <small className="sidebar-label">尚未创建项目</small>}
              </div>
            </section>
            <section className="sidebar-section" aria-labelledby="production-recent-title">
              <header className="sidebar-section-header"><span id="production-recent-title">最近</span><span>{bootstrap.conversations.length}</span></header>
              <div className="sidebar-list">
                {bootstrap.conversations.length ? bootstrap.conversations.map((conversation) => <div className="sidebar-row-shell" key={conversation.id}><button type="button" className={`sidebar-conversation-button ${activeConversationId === conversation.id ? "active" : ""}`} onClick={() => void openConversation(conversation)}><span className="sidebar-unread-dot" aria-hidden="true" /><strong>{conversation.title || "未命名会话"}</strong></button></div>) : <small className="sidebar-label">尚无历史会话</small>}
              </div>
            </section>
          </div>
        </div>
        <footer className="sidebar-footer">
          <button type="button" className="sidebar-data-status"><span className={`status-dot ${businessDataReady ? "positive" : ""}`} aria-hidden="true" /><span className="sidebar-label"><strong>{businessDataReady ? "企业数据可用" : "尚未配置数据范围"}</strong><small>{businessDataReady ? `${organizationUnits.length} 个授权事业部` : "请联系企业管理员"}</small></span></button>
          <div className="profile-control workspace-profile">
            <button className="profile-button" type="button" aria-label="打开个人菜单" aria-expanded={accountMenuOpen} onClick={() => setAccountMenuOpen((current) => !current)}><span className="profile-avatar" aria-hidden="true">{userInitials}</span><span className="sidebar-label"><strong>{preferredDisplayName(me)}</strong><small>{me.user.role} · {me.user.email}</small></span><span className="profile-menu-chevron sidebar-label" aria-hidden="true">{accountMenuOpen ? "⌄" : "›"}</span></button>
            {accountMenuOpen && <div className="profile-menu account-menu" role="menu" aria-label="个人菜单"><button type="button" className="account-menu-identity" role="menuitem"><span className="account-menu-avatar" aria-hidden="true">{userInitials}</span><span><strong>{preferredDisplayName(me)}</strong><small>{me.user.email}</small></span></button><div className="profile-menu-divider" /><button type="button" className="account-menu-item account-menu-logout" role="menuitem" onClick={() => void logout()}><span>退出登录</span></button></div>}
          </div>
        </footer>
      </aside>
      <button className="workspace-sidebar-scrim" type="button" aria-label="关闭侧栏" onClick={() => setSidebarOpen(false)} />

      <section className="workspace-stage" aria-label="AI 对话工作台">
        <header className="workspace-topbar">
          <button className="mobile-sidebar-trigger" type="button" aria-label="打开侧栏" onClick={() => setSidebarOpen(true)}>☰</button>
          <div className="workspace-title-block"><strong>{activeConversation?.title || "新会话"}</strong><small>{me.enterprise.name} · {environmentLabel(me)}</small></div>
          <div className="workspace-topbar-actions"><button className="topbar-scope-button" type="button" onClick={() => void onReload()}>刷新数据</button><button className="topbar-new-button" type="button" aria-label="新建会话" onClick={newConversation}>＋</button></div>
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
              onKeyDown={handleComposerKeyDown}
              onSubmit={submit}
              selectedOrganization={selectedOrganization}
            />
          ) : (
            <ProductionHome
              me={me}
              organizationUnits={organizationUnits}
              selectedOrganizationId={effectiveSelectedOrganizationId}
              setSelectedOrganizationId={setSelectedOrganizationId}
              latestReportTitle={latestDailyReport?.title ?? null}
              latestReportMeta={latestDailyReport?.data_as_of ?? latestDailyReport?.published_at ?? null}
              draft={draft}
              setDraft={setDraft}
              sending={sending}
              uploadedFiles={uploadedFiles}
              uploading={uploading}
              fileRef={fileRef}
              onFiles={uploadFiles}
              onKeyDown={handleComposerKeyDown}
              onSubmit={submit}
            />
          )}
        </main>
      </section>
    </div>
  );
}

function ProductionHome({
  me,
  organizationUnits,
  selectedOrganizationId,
  setSelectedOrganizationId,
  latestReportTitle,
  latestReportMeta,
  draft,
  setDraft,
  sending,
  uploadedFiles,
  uploading,
  fileRef,
  onFiles,
  onKeyDown,
  onSubmit,
}: {
  me: AuthMe;
  organizationUnits: OrganizationUnit[];
  selectedOrganizationId: string;
  setSelectedOrganizationId: (value: string) => void;
  latestReportTitle: string | null;
  latestReportMeta: string | null;
  draft: string;
  setDraft: (value: string) => void;
  sending: boolean;
  uploadedFiles: FileMetadata[];
  uploading: boolean;
  fileRef: React.RefObject<HTMLInputElement | null>;
  onFiles: (event: ChangeEvent<HTMLInputElement>) => void;
  onKeyDown: (event: KeyboardEvent<HTMLTextAreaElement>) => void;
  onSubmit: (event: FormEvent) => void;
}) {
  const name = preferredDisplayName(me);
  const hasScope = organizationUnits.length > 0;
  return (
    <div className="workspace-home">
      <div className="home-empty-stage">
        <div className="home-empty-inner">
          {latestReportTitle && <div className="morning-brief-trigger" role="status"><span className="morning-brief-dot" aria-hidden="true" /><span><strong>{latestReportTitle}</strong><small>{latestReportMeta ? `数据截至 ${latestReportMeta}` : "已生成"}</small></span><span>最新简报</span></div>}
          <section className="workspace-greeting" aria-labelledby="production-greeting-title">
            <p>{localizedDate(me.user.locale, me.user.timezone)}</p>
            <div className="greeting-title-line"><span className="service-mark" aria-hidden="true" /><h1 id="production-greeting-title">{greetingForCurrentHour(me.user.timezone)}，{name}</h1></div>
            <span>{hasScope ? "今天需要我先看什么？" : "企业管理员尚未为您配置可分析的事业部。"}</span>
          </section>
          <ProductionComposer
            id="production-home-question"
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
            onKeyDown={onKeyDown}
            onSubmit={onSubmit}
          />
          {!hasScope && <section className="prompt-suggestions" aria-live="polite"><h2>尚未配置可分析事业部</h2><div><button type="button" disabled><span>请联系企业管理员完成数据接入和授权</span></button></div></section>}
          <p className="home-service-note">生产模式不会使用演示数据。关键经营数字请结合来源与数据时间核对。</p>
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
  onKeyDown,
  onSubmit,
  selectedOrganization,
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
  onKeyDown: (event: KeyboardEvent<HTMLTextAreaElement>) => void;
  onSubmit: (event: FormEvent) => void;
  selectedOrganization: OrganizationUnit | null;
}) {
  return (
    <div className="chat-page">
      <div className="chat-scroll-region"><div className="chat-scroll-inner"><div className="conversation-column">
        {loading && <section className="state-card" aria-live="polite"><p className="eyebrow">正在加载</p><h3>正在读取会话消息</h3><p>只读取当前账号获准访问的内容。</p></section>}
        {error && <section className="state-card" role="alert"><p className="eyebrow">加载失败</p><h3>暂时无法读取这条会话</h3><p>{error}</p></section>}
        {!loading && !error && !messages.length && <section className="chat-empty-state"><p className="eyebrow">空会话</p><h2>{conversation?.title || "新会话"}</h2><p>这条会话还没有消息，可以从下方输入框开始。</p></section>}
        {messages.map((message) => message.role === "user" ? (
          <article className="user-message" key={message.id}><span>您</span><p>{message.content}</p><time>{formatTimestamp(message.created_at)}</time></article>
        ) : (
          <article className="structured-answer" key={message.id}><div className="answer-meta"><span>{message.role === "assistant" ? "AI 秘书" : "系统"}</span><time>{formatTimestamp(message.created_at)}</time></div><section className="answer-conclusion"><p>{message.content || "正在等待真实处理结果…"}</p></section>{message.status && message.status !== "completed" && <small>状态：{messageStatusLabel(message.status)}</small>}</article>
        ))}
        {sending && <section className="processing-card" aria-live="polite"><p className="eyebrow">正在提交</p><h3>问题已进入受控处理流程</h3><p>系统不会在尚未收到真实结果时生成占位结论。</p></section>}
      </div></div></div>
      <div className="workspace-composer-dock chat-dock">
        <ProductionComposer
          id="production-chat-question"
          draft={draft}
          setDraft={setDraft}
          sending={sending}
          disabled={!selectedOrganization}
          organizationUnits={selectedOrganization ? [selectedOrganization] : []}
          selectedOrganizationId={selectedOrganization?.id ?? ""}
          setSelectedOrganizationId={() => undefined}
          uploadedFiles={uploadedFiles}
          uploading={uploading}
          fileRef={fileRef}
          onFiles={onFiles}
          onKeyDown={onKeyDown}
          onSubmit={onSubmit}
        />
        <p>生产模式不会使用演示数据。关键经营数字请结合来源与数据时间核对。</p>
      </div>
    </div>
  );
}

function ProductionComposer({
  id,
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
  onKeyDown,
  onSubmit,
}: {
  id: string;
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
  onKeyDown: (event: KeyboardEvent<HTMLTextAreaElement>) => void;
  onSubmit: (event: FormEvent) => void;
}) {
  return (
    <form className="composer workbench-composer home-primary-composer" onSubmit={onSubmit}>
      <label className="sr-only" htmlFor={id}>输入经营问题</label>
      <textarea id={id} rows={2} maxLength={COMPOSER_MAX_LENGTH} value={draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={onKeyDown} placeholder={disabled ? "尚未配置可分析事业部" : "向 AI 秘书提问经营数据，或上传当前会话文件"} disabled={disabled} />
      {uploadedFiles.length > 0 && <p className="composer-file-note">{uploadedFiles.map((file) => `${file.original_name} · ${file.status}`).join("　")}</p>}
      <div className="composer-footer">
        <div className="composer-tools">
          <input ref={fileRef} className="sr-only" type="file" multiple accept=".pdf,.docx,.xlsx,.pptx" onChange={onFiles} />
          <button type="button" className="composer-tool-button" onClick={() => fileRef.current?.click()} disabled={disabled || uploading}><span aria-hidden="true">＋</span><span>{uploading ? "上传中…" : "文件"}</span></button>
          <label className="sr-only" htmlFor={`${id}-organization`}>选择事业部</label>
          <select id={`${id}-organization`} className="composer-tool-button scope" value={selectedOrganizationId} onChange={(event) => setSelectedOrganizationId(event.target.value)} disabled={disabled || organizationUnits.length <= 1}>
            {!organizationUnits.length && <option value="">尚未配置</option>}
            {organizationUnits.map((unit) => <option key={unit.id} value={unit.id}>{unit.name}</option>)}
          </select>
        </div>
        <div className="composer-send">
          {draft.length >= COMPOSER_MAX_LENGTH * 0.8 && <span className="composer-character-count">还可输入 {(COMPOSER_MAX_LENGTH - draft.length).toLocaleString("zh-CN")} 字</span>}
          <button className="composer-submit-button" type="submit" disabled={disabled || sending || !draft.trim()} aria-label="发送问题">↑</button>
        </div>
      </div>
    </form>
  );
}

function formatTimestamp(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(date);
}
