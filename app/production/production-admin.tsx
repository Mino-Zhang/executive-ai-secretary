"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import { humanizeApiError } from "./api-client";
import { productionServices } from "./services";
import type {
  AuthMe,
  McpTool,
  McpToolCatalog,
  ModelProviderConfig,
} from "./types";

type AdminView = "models" | "mcp";

export function ProductionAdmin({
  me,
  onLogout,
}: {
  me: AuthMe;
  onLogout: () => void;
}) {
  const [view, setView] = useState<AdminView>("models");

  return (
    <div className="production-admin-shell" data-app-mode={me.app_mode} data-app-environment={me.app_env}>
      <aside className="production-admin-rail">
        <div className="production-admin-brand"><span aria-hidden="true">董</span><div><strong>AI 秘书管理端</strong><small>{me.enterprise.name}</small></div></div>
        <nav aria-label="管理功能">
          <button className={view === "models" ? "active" : ""} type="button" onClick={() => setView("models")}><span aria-hidden="true">模</span><strong>模型服务</strong></button>
          <button className={view === "mcp" ? "active" : ""} type="button" onClick={() => setView("mcp")}><span aria-hidden="true">工</span><strong>MCP 工具</strong></button>
        </nav>
        <div className="production-admin-account"><span aria-hidden="true">{me.user.display_name.slice(0, 1)}</span><div><strong>{me.user.display_name}</strong><small>{me.user.role === "fde" ? "实施与运维" : "企业管理员"}</small></div><button type="button" onClick={onLogout}>退出</button></div>
      </aside>
      {view === "models" ? <ModelProviderPanel /> : <McpToolsPanel />}
    </div>
  );
}

function ModelProviderPanel() {
  const [config, setConfig] = useState<ModelProviderConfig | null>(null);
  const [modelId, setModelId] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [busy, setBusy] = useState<"save" | "test" | "toggle" | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    let active = true;
    productionServices.adminModels.get()
      .then((result) => {
        if (!active) return;
        setConfig(result);
        setModelId(result.model_id);
      })
      .catch((loadError: unknown) => {
        if (active) setError(humanizeApiError(loadError));
      });
    return () => { active = false; };
  }, []);

  async function reload() {
    const result = await productionServices.adminModels.get();
    setConfig(result);
    setModelId(result.model_id);
  }

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
      await reload();
      setNotice(`连接测试通过，${result.latency_ms} ms。现在可以启用模型服务。`);
    } catch (testError) {
      setError(humanizeApiError(testError));
      await reload();
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
      setNotice(next.is_enabled ? "Anspire 已成为当前唯一生成模型通道。" : "Anspire 生成服务已停用。");
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
    <main className="production-admin-main">
      <header className="production-admin-heading">
        <div><p>模型服务</p><h1>Anspire 单一模型通道</h1><span>路由、规划与回答统一通过 Anspire；不接入其他模型供应商。</span></div>
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
        {!config ? <div className="anspire-loading" aria-live="polite">正在读取企业模型配置…</div> : <>
          <div className="anspire-settings-grid">
            <label><span>模型</span><select value={modelId} onChange={(event) => setModelId(event.target.value)}>{modelFamilies.map(([family, models]) => <optgroup key={family} label={family}>{models.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.id}</option>)}</optgroup>)}</select><small>后台已接入 Anspire 全量模型目录；只允许聊天与推理模型进入问答 Harness。</small></label>
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
          <footer><p>密钥不会写入浏览器存储、日志或回答证据；模型运行请求由内部签名保护。</p><div><button className="secondary-button" type="submit" disabled={Boolean(busy) || !modelId}>{busy === "save" ? "正在保存…" : "保存配置"}</button><button className="secondary-button" type="button" onClick={() => void testConnection()} disabled={Boolean(busy) || !config.is_configured}>{busy === "test" ? "正在测试…" : "测试连接"}</button><button className="primary-button" type="button" onClick={() => void toggle()} disabled={Boolean(busy) || (!config.is_enabled && config.last_test_status !== "success")}>{busy === "toggle" ? "正在更新…" : config.is_enabled ? "停用" : "启用 Anspire"}</button></div></footer>
        </>}
      </form>
    </main>
  );
}

function McpToolsPanel() {
  const [catalog, setCatalog] = useState<McpToolCatalog | null>(null);
  const [selectedName, setSelectedName] = useState("");
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [draft, setDraft] = useState({ display_name: "", description: "", timeout_seconds: 20, max_rows: 50, operator_note: "" });

  useEffect(() => {
    let active = true;
    productionServices.adminMcp.list()
      .then((result) => {
        if (!active) return;
        setCatalog(result);
        const first = result.tools[0];
        setSelectedName(first?.tool_name ?? "");
        if (first) {
          setDraft({
            display_name: first.display_name,
            description: first.description,
            timeout_seconds: first.timeout_seconds,
            max_rows: first.max_rows,
            operator_note: first.operator_note ?? "",
          });
        }
      })
      .catch((loadError: unknown) => {
        if (active) setError(humanizeApiError(loadError));
      });
    return () => { active = false; };
  }, []);

  const selected = catalog?.tools.find((item) => item.tool_name === selectedName) ?? null;
  const filtered = useMemo(() => {
    const keyword = query.trim().toLowerCase();
    if (!keyword) return catalog?.tools ?? [];
    return (catalog?.tools ?? []).filter((item) => `${item.display_name} ${item.tool_name} ${item.category}`.toLowerCase().includes(keyword));
  }, [catalog, query]);

  function mergeTool(tool: McpTool) {
    setCatalog((current) => current ? {
      ...current,
      tools: current.tools.map((item) => item.tool_name === tool.tool_name ? tool : item),
      enabled_count: current.tools.reduce((count, item) => count + (item.tool_name === tool.tool_name ? Number(tool.is_enabled) : Number(item.is_enabled)), 0),
      planner_count: current.tools.reduce((count, item) => count + (item.tool_name === tool.tool_name ? Number(tool.is_enabled && tool.planner_enabled) : Number(item.is_enabled && item.planner_enabled)), 0),
    } : current);
    if (tool.tool_name === selectedName) {
      setDraft({
        display_name: tool.display_name,
        description: tool.description,
        timeout_seconds: tool.timeout_seconds,
        max_rows: tool.max_rows,
        operator_note: tool.operator_note ?? "",
      });
    }
  }

  function selectTool(tool: McpTool) {
    setSelectedName(tool.tool_name);
    setDraft({
      display_name: tool.display_name,
      description: tool.description,
      timeout_seconds: tool.timeout_seconds,
      max_rows: tool.max_rows,
      operator_note: tool.operator_note ?? "",
    });
  }

  async function updateTool(toolName: string, values: Parameters<typeof productionServices.adminMcp.update>[1], action: string) {
    if (busy) return;
    setBusy(action);
    setError("");
    setNotice("");
    try {
      mergeTool(await productionServices.adminMcp.update(toolName, values));
      setNotice("MCP 工具配置已生效。后续规划会立即遵循这项边界。");
    } catch (updateError) {
      setError(humanizeApiError(updateError));
    } finally {
      setBusy("");
    }
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!selected) return;
    await updateTool(selected.tool_name, {
      display_name: draft.display_name.trim(),
      description: draft.description.trim(),
      timeout_seconds: draft.timeout_seconds,
      max_rows: draft.max_rows,
      operator_note: draft.operator_note.trim() || null,
    }, "save");
  }

  async function validate() {
    if (!selected || busy) return;
    setBusy("validate");
    setError("");
    setNotice("");
    try {
      const result = await productionServices.adminMcp.validate(selected.tool_name);
      mergeTool(result.tool);
      setNotice(result.ready ? "校验通过：工具配置与所需数据域均已就绪。" : result.issues.join("；"));
    } catch (validationError) {
      setError(humanizeApiError(validationError));
    } finally {
      setBusy("");
    }
  }

  return (
    <main className="production-admin-main mcp-admin-main">
      <header className="production-admin-heading">
        <div><p>执行能力</p><h1>MCP 工具注册表</h1><span>只开放经过审计的经营工具；查询规划、意图路由和后续 Skill 共用同一配置。</span></div>
        <span className="production-admin-status positive"><i aria-hidden="true" />{catalog ? `${catalog.enabled_count} / ${catalog.tools.length} 已启用` : "正在读取"}</span>
      </header>
      <section className="mcp-boundary-note"><strong>受控边界</strong><span>管理端可以调整启停、规划权限、超时和返回规模，不能添加任意 SQL、脚本或外部地址。</span></section>
      <div className="mcp-registry-layout">
        <section className="mcp-tool-index" aria-label="MCP 工具列表">
          <header><div><strong>工具</strong><small>{catalog ? `${catalog.planner_count} 个可被规划器选择` : "加载中"}</small></div><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索工具" aria-label="搜索 MCP 工具" /></header>
          <div>{filtered.map((tool) => <article className={selectedName === tool.tool_name ? "selected" : ""} key={tool.tool_name}><button type="button" onClick={() => selectTool(tool)}><span><strong>{tool.display_name}</strong><small>{tool.tool_name}</small></span><i className={`mcp-readiness ${tool.readiness}`} title={tool.readiness_issues.join("；")} aria-label={tool.readiness} /></button><label className="switch mcp-inline-switch" title="启用工具"><input type="checkbox" checked={tool.is_enabled} disabled={Boolean(busy)} onChange={(event) => void updateTool(tool.tool_name, { is_enabled: event.target.checked }, `enable:${tool.tool_name}`)} /><span aria-hidden="true" /></label></article>)}</div>
          {!filtered.length && <p className="mcp-empty">没有匹配的工具。</p>}
        </section>
        <section className="mcp-tool-detail" aria-live="polite">
          {!selected ? <div className="anspire-loading">请选择一个 MCP 工具。</div> : <form onSubmit={save}>
            <header><div><small>{selected.category}</small><h2>{selected.display_name}</h2><code>{selected.tool_name}</code></div><span className={`mcp-detail-status ${selected.readiness}`}>{selected.readiness === "ready" ? "可运行" : selected.readiness === "disabled" ? "已停用" : "数据未就绪"}</span></header>
            <div className="mcp-tool-controls"><label><span>允许执行</span><span className="switch"><input type="checkbox" checked={selected.is_enabled} disabled={Boolean(busy)} onChange={(event) => void updateTool(selected.tool_name, { is_enabled: event.target.checked }, "enable")} /><span aria-hidden="true" /></span><small>关闭后，MCP Hub 会直接拒绝调用。</small></label><label><span>允许自动规划</span><span className="switch"><input type="checkbox" checked={selected.planner_enabled} disabled={Boolean(busy) || !selected.is_enabled} onChange={(event) => void updateTool(selected.tool_name, { planner_enabled: event.target.checked }, "planner")} /><span aria-hidden="true" /></span><small>关闭后仍可保留工具，但 Harness 不会自动选择。</small></label></div>
            <div className="mcp-tool-form"><label><span>显示名称</span><input value={draft.display_name} maxLength={160} onChange={(event) => setDraft((current) => ({ ...current, display_name: event.target.value }))} /></label><label className="wide"><span>用途说明</span><textarea rows={3} value={draft.description} maxLength={2000} onChange={(event) => setDraft((current) => ({ ...current, description: event.target.value }))} /></label><label><span>超时（秒）</span><input type="number" min={3} max={60} value={draft.timeout_seconds} onChange={(event) => setDraft((current) => ({ ...current, timeout_seconds: Number(event.target.value) }))} /></label><label><span>最大返回行数</span><input type="number" min={1} max={100} value={draft.max_rows} onChange={(event) => setDraft((current) => ({ ...current, max_rows: Number(event.target.value) }))} /></label><label className="wide"><span>运维备注</span><textarea rows={2} value={draft.operator_note} maxLength={500} onChange={(event) => setDraft((current) => ({ ...current, operator_note: event.target.value }))} placeholder="仅管理端可见" /></label></div>
            <section className="mcp-schema"><header><strong>规划器可用参数</strong><small>{selected.domains.length ? `依赖数据域：${selected.domains.join("、")}` : "不依赖经营事实"}</small></header><div>{Object.entries(selected.parameters).map(([name, schema]) => <span key={name}><code>{name}</code><small>{String(schema.description ?? schema.type ?? "参数")}</small></span>)}{!Object.keys(selected.parameters).length && <p>该工具不接受可变业务参数，查询范围由权限令牌注入。</p>}</div></section>
            {selected.readiness_issues.length > 0 && <p className="anspire-error" role="alert">{selected.readiness_issues.join("；")}</p>}
            {error && <p className="anspire-error" role="alert">{error}</p>}
            {notice && <p className="anspire-notice" role="status">{notice}</p>}
            <footer><span>配置变更会写入审计日志，并由规划器和 MCP Hub 同时执行。</span><div><button className="secondary-button" type="button" disabled={Boolean(busy)} onClick={() => void validate()}>{busy === "validate" ? "正在校验…" : "校验就绪度"}</button><button className="primary-button" type="submit" disabled={Boolean(busy) || !draft.display_name.trim() || !draft.description.trim()}>{busy === "save" ? "正在保存…" : "保存配置"}</button></div></footer>
          </form>}
        </section>
      </div>
    </main>
  );
}
