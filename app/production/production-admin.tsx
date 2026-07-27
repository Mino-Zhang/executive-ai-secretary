"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import { humanizeApiError } from "./api-client";
import { productionServices } from "./services";
import type { AuthMe, ModelProviderConfig } from "./types";

export function ProductionAdmin({
  me,
  onLogout,
}: {
  me: AuthMe;
  onLogout: () => void;
}) {
  const [config, setConfig] = useState<ModelProviderConfig | null>(null);
  const [modelId, setModelId] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [busy, setBusy] = useState<"save" | "test" | "toggle" | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  async function load() {
    setError("");
    try {
      const result = await productionServices.adminModels.get();
      setConfig(result);
      setModelId(result.model_id);
    } catch (loadError) {
      setError(humanizeApiError(loadError));
    }
  }

  useEffect(() => {
    void load();
  }, []);

  const selectedModel = useMemo(
    () => config?.models.find((item) => item.id === modelId) ?? null,
    [config, modelId],
  );
  const modelFamilies = useMemo(() => {
    const groups = new Map<string, NonNullable<typeof config>["models"]>();
    for (const model of config?.models.filter((item) => item.selectable) ?? []) {
      const items = groups.get(model.family) ?? [];
      items.push(model);
      groups.set(model.family, items);
    }
    return [...groups.entries()];
  }, [config]);

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!config || busy) return;
    setBusy("save");
    setError("");
    setNotice("");
    try {
      const next = await productionServices.adminModels.update({
        model_id: modelId,
        ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
      });
      setConfig(next);
      setModelId(next.model_id);
      setApiKey("");
      setNotice("配置已加密保存。请完成连接测试后启用。");
    } catch (saveError) {
      setError(humanizeApiError(saveError));
    } finally {
      setBusy(null);
    }
  }

  async function testConnection() {
    if (!config || busy) return;
    setBusy("test");
    setError("");
    setNotice("");
    try {
      const result = await productionServices.adminModels.test();
      await load();
      setNotice(`连接测试通过，${result.latency_ms} ms。现在可以启用模型服务。`);
    } catch (testError) {
      setError(humanizeApiError(testError));
      await load();
    } finally {
      setBusy(null);
    }
  }

  async function toggle() {
    if (!config || busy) return;
    setBusy("toggle");
    setError("");
    setNotice("");
    try {
      const next = await productionServices.adminModels.update({
        model_id: config.model_id,
        is_enabled: !config.is_enabled,
      });
      setConfig(next);
      setNotice(
        next.is_enabled
          ? "Anspire 已成为当前唯一生成模型通道。"
          : "Anspire 生成服务已停用。",
      );
    } catch (toggleError) {
      setError(humanizeApiError(toggleError));
    } finally {
      setBusy(null);
    }
  }

  const status = !config?.is_configured
    ? { label: "未配置", tone: "quiet" }
    : config.last_test_status === "failed"
      ? { label: "测试失败", tone: "risk" }
      : config.is_enabled
        ? { label: "已启用", tone: "positive" }
        : config.last_test_status === "success"
          ? { label: "待启用", tone: "attention" }
          : { label: "等待测试", tone: "quiet" };

  return (
    <div className="production-admin-shell" data-app-mode={me.app_mode} data-app-environment={me.app_env}>
      <aside className="production-admin-rail">
        <div className="production-admin-brand"><span aria-hidden="true">董</span><div><strong>AI 秘书管理端</strong><small>{me.enterprise.name}</small></div></div>
        <nav aria-label="管理功能"><button className="active" type="button"><span aria-hidden="true">模</span><strong>模型服务</strong></button></nav>
        <div className="production-admin-account"><span aria-hidden="true">{me.user.display_name.slice(0, 1)}</span><div><strong>{me.user.display_name}</strong><small>{me.user.role === "fde" ? "实施与运维" : "企业管理员"}</small></div><button type="button" onClick={onLogout}>退出</button></div>
      </aside>
      <main className="production-admin-main">
        <header className="production-admin-heading">
          <div><p>模型服务</p><h1>Anspire 单一模型通道</h1><span>经营问数与文件回答仅通过 Anspire 生成；不接入其他模型供应商。</span></div>
          <span className={`production-admin-status ${status.tone}`}><i aria-hidden="true" />{status.label}</span>
        </header>

        <section className="anspire-provider-summary" aria-label="Anspire 接入边界">
          <div><small>服务商</small><strong>Anspire Open</strong></div>
          <div><small>正式网关</small><strong>open-gateway.anspire.ai</strong></div>
          <div><small>运行边界</small><strong>唯一生成模型通道</strong></div>
          <a href={config?.documentation_url ?? "https://llm.anspire.ai/?tab=models"} target="_blank" rel="noreferrer">查看官方模型列表 <span aria-hidden="true">↗</span></a>
        </section>

        <form className="anspire-settings-card" onSubmit={save}>
          <header><div><p>当前配置</p><h2>经营研究主模型</h2></div><span>{selectedModel?.profile ?? "选择适合经营分析的模型"}</span></header>
          {!config ? (
            <div className="anspire-loading" aria-live="polite">正在读取企业模型配置…</div>
          ) : (
            <>
              <div className="anspire-settings-grid">
                <label><span>模型</span><select value={modelId} onChange={(event) => setModelId(event.target.value)}>{modelFamilies.map(([family, models]) => <optgroup key={family} label={family}>{models.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.id}</option>)}</optgroup>)}</select><small>后台已接入 Anspire 全量模型目录；当前仅允许聊天与推理模型进入经营回答通道。</small></label>
                <label><span>API Key</span><input type="password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder={config.api_key_masked ?? "输入 Anspire API Key"} autoComplete="off" spellCheck={false} /><small>{config.is_configured ? `已保存 ${config.api_key_masked}；留空不会替换。` : "保存后以企业独立密钥加密，页面不会再次返回明文。"}</small></label>
                <label className="wide"><span>API 接口</span><input value={config.endpoint_url} readOnly aria-readonly="true" /><small>地址由系统锁定，管理员不能改成其他兼容网关。</small></label>
              </div>
              <div className="anspire-flow">
                <div className={config.is_configured ? "done" : ""}><span>01</span><strong>保存凭证</strong><small>企业级加密存储</small></div>
                <div className={config.last_test_status === "success" ? "done" : config.last_test_status === "failed" ? "failed" : ""}><span>02</span><strong>连接测试</strong><small>{config.last_test_status === "success" ? `${config.last_test_latency_ms ?? "—"} ms` : config.last_test_status === "failed" ? "需要重新检查" : "尚未测试"}</small></div>
                <div className={config.is_enabled ? "done" : ""}><span>03</span><strong>启用服务</strong><small>{config.is_enabled ? "已作用于真实问答" : "不会提前生效"}</small></div>
              </div>
              {config.last_test_error && <p className="anspire-error" role="alert">{config.last_test_error}</p>}
              {error && <p className="anspire-error" role="alert">{error}</p>}
              {notice && <p className="anspire-notice" role="status">{notice}</p>}
              <footer>
                <p>密钥不会写入浏览器存储、日志或回答证据；模型运行请求由内部签名保护。</p>
                <div><button className="secondary-button" type="submit" disabled={Boolean(busy) || !modelId}>{busy === "save" ? "正在保存…" : "保存配置"}</button><button className="secondary-button" type="button" onClick={() => void testConnection()} disabled={Boolean(busy) || !config.is_configured}>{busy === "test" ? "正在测试…" : "测试连接"}</button><button className="primary-button" type="button" onClick={() => void toggle()} disabled={Boolean(busy) || (!config.is_enabled && config.last_test_status !== "success")}>{busy === "toggle" ? "正在更新…" : config.is_enabled ? "停用" : "启用 Anspire"}</button></div>
              </footer>
            </>
          )}
        </form>
      </main>
    </div>
  );
}
