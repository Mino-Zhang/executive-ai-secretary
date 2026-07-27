"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import { humanizeApiError } from "./api-client";
import { productionServices } from "./services";
import type {
  AuthMe,
  HarnessBusinessConfig,
  HarnessConfig,
  HarnessFastRule,
  HarnessMetrics,
  HarnessSimulation,
  HarnessTrace,
  HarnessVersion,
  McpTool,
  McpToolCatalog,
  ModelProviderConfig,
} from "./types";

type AdminView = "models" | "harness" | "mcp";

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
          <button className={view === "harness" ? "active" : ""} type="button" onClick={() => setView("harness")}><span aria-hidden="true">编</span><strong>编排策略</strong></button>
          <button className={view === "mcp" ? "active" : ""} type="button" onClick={() => setView("mcp")}><span aria-hidden="true">工</span><strong>MCP 工具</strong></button>
        </nav>
        <div className="production-admin-account"><span aria-hidden="true">{me.user.display_name.slice(0, 1)}</span><div><strong>{me.user.display_name}</strong><small>{me.user.role === "fde" ? "实施与运维" : "企业管理员"}</small></div><button type="button" onClick={onLogout}>退出</button></div>
      </aside>
      {view === "models" ? <ModelProviderPanel /> : view === "harness" ? <HarnessPolicyPanel /> : <McpToolsPanel />}
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

const harnessPromptFields: Array<{ key: keyof HarnessBusinessConfig["prompts"]; label: string; note: string }> = [
  { key: "system", label: "董事长助理基础 Prompt", note: "定义身份、语气与事实边界" },
  { key: "data_answer", label: "经营回答 Prompt", note: "约束结论、数字证据与数据时间" },
  { key: "general_answer", label: "个人泛化回答 Prompt", note: "用于日常分析、写作与思考" },
  { key: "route", label: "意图识别 Prompt", note: "仅在快速规则未命中时使用" },
  { key: "rewrite", label: "查询改写 Prompt", note: "输出固定 QuerySpec" },
  { key: "plan", label: "任务规划 Prompt", note: "仅选择启用的 MCP 工具" },
];

function copyHarnessConfig(config: HarnessBusinessConfig): HarnessBusinessConfig {
  return JSON.parse(JSON.stringify(config)) as HarnessBusinessConfig;
}

function HarnessPolicyPanel() {
  const [current, setCurrent] = useState<HarnessConfig | null>(null);
  const [draft, setDraft] = useState<HarnessBusinessConfig | null>(null);
  const [versions, setVersions] = useState<HarnessVersion[]>([]);
  const [metrics, setMetrics] = useState<HarnessMetrics | null>(null);
  const [traces, setTraces] = useState<HarnessTrace[]>([]);
  const [mcpCatalog, setMcpCatalog] = useState<McpToolCatalog | null>(null);
  const [question, setQuestion] = useState("本月华东与华南的回款差距主要来自哪些客户？");
  const [simulation, setSimulation] = useState<HarnessSimulation | null>(null);
  const [busy, setBusy] = useState<"save" | "simulate" | "restore" | "" | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  async function load() {
    const [configResult, versionResult, metricResult, traceResult, toolResult] = await Promise.all([
      productionServices.adminHarness.get(),
      productionServices.adminHarness.versions(),
      productionServices.adminHarness.metrics(),
      productionServices.adminHarness.traces(),
      productionServices.adminMcp.list(),
    ]);
    setCurrent(configResult);
    setDraft(copyHarnessConfig(configResult.config));
    setVersions(versionResult);
    setMetrics(metricResult);
    setTraces(traceResult);
    setMcpCatalog(toolResult);
  }

  useEffect(() => {
    let active = true;
    Promise.all([
      productionServices.adminHarness.get(),
      productionServices.adminHarness.versions(),
      productionServices.adminHarness.metrics(),
      productionServices.adminHarness.traces(),
      productionServices.adminMcp.list(),
    ]).then(([configResult, versionResult, metricResult, traceResult, toolResult]) => {
      if (!active) return;
      setCurrent(configResult);
      setDraft(copyHarnessConfig(configResult.config));
      setVersions(versionResult);
      setMetrics(metricResult);
      setTraces(traceResult);
      setMcpCatalog(toolResult);
    }).catch((loadError: unknown) => {
      if (active) setError(humanizeApiError(loadError));
    });
    return () => { active = false; };
  }, []);

  function updatePrompt(key: keyof HarnessBusinessConfig["prompts"], value: string) {
    setDraft((existing) => existing ? { ...existing, prompts: { ...existing.prompts, [key]: value } } : existing);
  }

  function updateRule(index: number, values: Partial<HarnessFastRule>) {
    setDraft((existing) => existing ? {
      ...existing,
      fast_rules: existing.fast_rules.map((rule, ruleIndex) => ruleIndex === index ? { ...rule, ...values } : rule),
    } : existing);
  }

  function addRule() {
    setDraft((existing) => existing ? {
      ...existing,
      fast_rules: [...existing.fast_rules, {
        id: `rule-${Date.now()}`,
        name: "新规则",
        enabled: true,
        priority: 50,
        match_mode: "any",
        terms: ["关键词"],
        exclusions: [],
        route: "data",
        candidate_tools: [],
      }],
    } : existing);
  }

  function toggleCandidateTool(ruleIndex: number, toolName: string) {
    if (!draft) return;
    const rule = draft.fast_rules[ruleIndex];
    const selected = rule.candidate_tools.includes(toolName);
    if (!selected && rule.candidate_tools.length >= 4) {
      setError("每条快速规则最多选择 4 个候选 MCP 工具。");
      return;
    }
    updateRule(ruleIndex, {
      candidate_tools: selected
        ? rule.candidate_tools.filter((name) => name !== toolName)
        : [...rule.candidate_tools, toolName],
    });
  }

  async function save() {
    if (!current || !draft || busy) return;
    setBusy("save");
    setError("");
    setNotice("");
    try {
      const result = await productionServices.adminHarness.update(current.version, draft);
      setCurrent(result);
      setDraft(copyHarnessConfig(result.config));
      setVersions(await productionServices.adminHarness.versions());
      setNotice(`版本 v${result.version} 已立即作用于新消息任务；运行中的任务保持原快照。`);
    } catch (saveError) {
      setError(humanizeApiError(saveError));
      await load().catch(() => undefined);
    } finally {
      setBusy("");
    }
  }

  async function simulate() {
    if (!draft || !question.trim() || busy) return;
    setBusy("simulate");
    setError("");
    setNotice("");
    try {
      setSimulation(await productionServices.adminHarness.simulate(question.trim(), draft));
    } catch (simulationError) {
      setError(humanizeApiError(simulationError));
    } finally {
      setBusy("");
    }
  }

  async function restore(version: HarnessVersion) {
    if (busy || version.is_active) return;
    if (!window.confirm(`恢复 v${version.version} 的配置？恢复会生成一个新的当前版本。`)) return;
    setBusy("restore");
    setError("");
    try {
      const result = await productionServices.adminHarness.restore(version.id);
      setCurrent(result);
      setDraft(copyHarnessConfig(result.config));
      setVersions(await productionServices.adminHarness.versions());
      setNotice(`已从 v${version.version} 恢复并生成 v${result.version}。`);
    } catch (restoreError) {
      setError(humanizeApiError(restoreError));
    } finally {
      setBusy("");
    }
  }

  const dirty = Boolean(current && draft && JSON.stringify(current.config) !== JSON.stringify(draft));
  const plannerTools = mcpCatalog?.tools.filter((tool) => tool.is_enabled && tool.planner_enabled) ?? [];

  return (
    <main className="production-admin-main harness-admin-main">
      <header className="production-admin-heading">
        <div><p>编排策略</p><h1>可运营 Harness</h1><span>业务策略可编辑，权限、工具白名单与证据约束由安全内核强制执行。</span></div>
        <span className="production-admin-status positive"><i aria-hidden="true" />{current ? `当前 v${current.version}` : "正在读取"}</span>
      </header>

      {!draft || !current ? <div className="anspire-loading">正在读取编排策略…</div> : <>
        <section className="harness-safety-strip">
          <div><small>不可编辑安全内核</small><strong>服务端权限 · 注册工具白名单 · 数据回答强制证据</strong></div>
          <span>最多 4 次工具调用 · 并发 3 · 修正规划 1 次 · 禁止联网、文件与任意代码</span>
        </section>

        <section className="harness-section">
          <header><div><small>01</small><h2>身份与回答</h2></div><p>决定服务语气和回答组织，不接触个人记忆正文。</p></header>
          <div className="harness-prompt-grid">{harnessPromptFields.slice(0, 3).map((field) => <label key={field.key}><span>{field.label}</span><small>{field.note}</small><textarea rows={5} value={draft.prompts[field.key]} onChange={(event) => updatePrompt(field.key, event.target.value)} /></label>)}</div>
        </section>

        <section className="harness-section">
          <header><div><small>02</small><h2>理解与规划</h2></div><p>意图、改写和规划各自独立，输出结构由服务端固定。</p></header>
          <div className="harness-prompt-grid">{harnessPromptFields.slice(3).map((field) => <label key={field.key}><span>{field.label}</span><small>{field.note}</small><textarea rows={5} value={draft.prompts[field.key]} onChange={(event) => updatePrompt(field.key, event.target.value)} /></label>)}</div>
          <div className="harness-glossary"><header><strong>业务术语表</strong><button type="button" onClick={() => setDraft((existing) => existing ? { ...existing, glossary: [...existing.glossary, { term: "", canonical: "", category: "其他", enabled: true }] } : existing)}>＋ 新增术语</button></header>{draft.glossary.map((entry, index) => <div key={`${entry.term}-${index}`}><input aria-label="术语" value={entry.term} placeholder="术语" onChange={(event) => setDraft((existing) => existing ? { ...existing, glossary: existing.glossary.map((item, itemIndex) => itemIndex === index ? { ...item, term: event.target.value } : item) } : existing)} /><input aria-label="标准名称" value={entry.canonical} placeholder="标准名称" onChange={(event) => setDraft((existing) => existing ? { ...existing, glossary: existing.glossary.map((item, itemIndex) => itemIndex === index ? { ...item, canonical: event.target.value } : item) } : existing)} /><input aria-label="类别" value={entry.category} placeholder="类别" onChange={(event) => setDraft((existing) => existing ? { ...existing, glossary: existing.glossary.map((item, itemIndex) => itemIndex === index ? { ...item, category: event.target.value } : item) } : existing)} /><label className="switch"><input type="checkbox" checked={entry.enabled} onChange={(event) => setDraft((existing) => existing ? { ...existing, glossary: existing.glossary.map((item, itemIndex) => itemIndex === index ? { ...item, enabled: event.target.checked } : item) } : existing)} /><span aria-hidden="true" /></label><button type="button" aria-label="移除术语" onClick={() => setDraft((existing) => existing ? { ...existing, glossary: existing.glossary.filter((_, itemIndex) => itemIndex !== index) } : existing)}>×</button></div>)}</div>
        </section>

        <section className="harness-section">
          <header><div><small>03</small><h2>快速规则</h2></div><p>只跳过模型路由；查询改写、权限与证据校验始终执行。</p><button type="button" className="secondary-button" onClick={addRule}>新增规则</button></header>
          <div className="harness-rule-list">{draft.fast_rules.map((rule, index) => <article key={rule.id}><header><label className="switch"><input type="checkbox" checked={rule.enabled} onChange={(event) => updateRule(index, { enabled: event.target.checked })} /><span aria-hidden="true" /></label><input value={rule.name} aria-label="规则名称" onChange={(event) => updateRule(index, { name: event.target.value })} /><span>优先级 <input type="number" min={0} max={1000} value={rule.priority} onChange={(event) => updateRule(index, { priority: Number(event.target.value) })} /></span><button type="button" aria-label="删除规则" onClick={() => setDraft((existing) => existing ? { ...existing, fast_rules: existing.fast_rules.filter((_, ruleIndex) => ruleIndex !== index) } : existing)}>×</button></header><div className="harness-rule-fields"><label><span>路由</span><select value={rule.route} onChange={(event) => updateRule(index, { route: event.target.value as HarnessFastRule["route"], candidate_tools: event.target.value === "general" ? [] : rule.candidate_tools })}><option value="data">经营问数</option><option value="general">个人泛化</option></select></label><label><span>匹配方式</span><select value={rule.match_mode} onChange={(event) => updateRule(index, { match_mode: event.target.value as HarnessFastRule["match_mode"] })}><option value="any">任一命中</option><option value="all">全部命中</option></select></label><label><span>关键词（逗号分隔）</span><input value={rule.terms.join("，")} onChange={(event) => updateRule(index, { terms: event.target.value.split(/[，,]/).map((item) => item.trim()).filter(Boolean) })} /></label><label><span>排除词（逗号分隔）</span><input value={rule.exclusions.join("，")} onChange={(event) => updateRule(index, { exclusions: event.target.value.split(/[，,]/).map((item) => item.trim()).filter(Boolean) })} /></label></div>{rule.route === "data" && <div className="harness-tool-picks"><span>候选 MCP（最多 4 个）</span>{plannerTools.map((tool) => <label key={tool.tool_name}><input type="checkbox" checked={rule.candidate_tools.includes(tool.tool_name)} onChange={() => toggleCandidateTool(index, tool.tool_name)} /><span>{tool.display_name}</span></label>)}</div>}</article>)}</div>
        </section>

        <section className="harness-section harness-validation-section">
          <header><div><small>04</small><h2>验证与追踪</h2></div><p>模拟不会调用经营工具；正式追踪默认只显示脱敏技术摘要。</p></header>
          <div className="harness-validation-grid"><div className="harness-simulator"><label><span>问题模拟</span><textarea rows={3} value={question} onChange={(event) => setQuestion(event.target.value)} /></label><button type="button" className="secondary-button" disabled={busy === "simulate" || !question.trim()} onClick={() => void simulate()}>{busy === "simulate" ? "正在模拟…" : "运行模拟"}</button>{simulation && <dl><div><dt>路由</dt><dd>{simulation.route}</dd></div><div><dt>来源</dt><dd>{simulation.route_source}{simulation.matched_rule_id ? ` · ${simulation.matched_rule_id}` : ""}</dd></div><div><dt>候选工具</dt><dd>{simulation.candidate_tools.join("、") || "无"}</dd></div><div><dt>歧义</dt><dd>{simulation.validation_issues.join("；") || "无"}</dd></div></dl>}</div><div className="harness-metrics"><strong>近 {metrics?.window_days ?? 30} 天</strong><div><span><b>{metrics?.message_count ?? 0}</b>消息任务</span><span><b>{Math.round((metrics?.structured_output_rate ?? 0) * 100)}%</b>结构有效</span><span><b>{Math.round((metrics?.tool_success_rate ?? 0) * 100)}%</b>工具成功</span></div><small>意图准确率需由基准集人工标注，不用线上自循环分数替代。</small></div></div>
          <div className="harness-traces"><header><strong>最近技术追踪</strong><small>不显示问题、回答、个人记忆和业务正文</small></header>{traces.slice(0, 8).map((trace) => <article key={trace.message_id}><span className={`trace-route ${trace.route}`}>{trace.route ?? "—"}</span><div><strong>{trace.route_source ?? "unknown"} · v{trace.harness_version ?? "—"}</strong><small>{trace.organization_unit_count} 个事业部 · {trace.tools.join("、") || "未调用工具"}</small></div><span>{trace.stages.length} 阶段</span>{trace.diagnostic_shared_until && <i>已授权正文诊断</i>}</article>)}{!traces.length && <p>尚无正式任务追踪。</p>}</div>
        </section>

        <section className="harness-version-rail"><header><div><strong>版本记录</strong><small>每次保存与恢复都会生成不可变版本</small></div><span>{current.config_hash.slice(0, 12)}</span></header><div>{versions.slice(0, 8).map((version) => <article key={version.id}><span>v{version.version}</span><small>{new Date(version.activated_at).toLocaleString("zh-CN")}</small><code>{version.config_hash.slice(0, 10)}</code><button type="button" disabled={version.is_active || busy === "restore"} onClick={() => void restore(version)}>{version.is_active ? "当前" : "恢复"}</button></article>)}</div></section>

        {error && <p className="anspire-error" role="alert">{error}</p>}
        {notice && <p className="anspire-notice" role="status">{notice}</p>}
        <div className="harness-save-bar"><span>{dirty ? "有尚未保存的策略修改" : `已同步 v${current.version}`}</span><button type="button" className="primary-button" disabled={!dirty || Boolean(busy)} onClick={() => void save()}>{busy === "save" ? "正在校验并保存…" : "保存并立即用于新任务"}</button></div>
      </>}
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
