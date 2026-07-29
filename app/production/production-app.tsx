"use client";

import {
  FormEvent,
  useCallback,
  useEffect,
  useState,
} from "react";
import { ApiError, humanizeApiError } from "./api-client";
import {
  loadProductionBootstrap,
  productionServices,
} from "./services";
import { ProductionWorkspace } from "./production-workspace";
import type {
  AuthMe,
  ProductionBootstrap,
} from "./types";

type SessionState =
  | { status: "checking" }
  | { status: "anonymous" }
  | { status: "password-change"; me: AuthMe; currentPassword: string }
  | { status: "ready"; bootstrap: ProductionBootstrap }
  | { status: "error"; message: string };

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
