"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import { humanizeApiError } from "./api-client";
import { productionServices } from "./services";
import type {
  AdminModelAuthorization,
  AdminModelCatalog,
  AuthMe,
  DataOperationsV3Overview,
  DataSource,
  DataSyncRun,
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
  ScheduledTask,
} from "./types";

type AdminView = "models" | "harness" | "mcp" | "data";

export function ProductionAdmin({
  me,
  onLogout,
}: {
  me: AuthMe;
  onLogout: () => void;
}) {
  const [view, setView] = useState<AdminView>("models");
  const [guideCollapsed, setGuideCollapsed] = useState(() => {
    if (typeof window === "undefined") return false;
    return window.localStorage.getItem("executive-admin-guide-collapsed") === "true";
  });

  useEffect(() => {
    const saved = window.localStorage.getItem("executive-workbench-theme");
    const theme = saved === "light" || saved === "dark" || saved === "system" ? saved : "system";
    document.documentElement.dataset.theme = theme;
    document.documentElement.style.colorScheme = theme === "system" ? "light dark" : theme;
    if (!saved) window.localStorage.setItem("executive-workbench-theme", "system");
  }, []);

  function toggleGuide() {
    setGuideCollapsed((current) => {
      const next = !current;
      window.localStorage.setItem("executive-admin-guide-collapsed", String(next));
      return next;
    });
  }

  const panel = view === "models"
    ? <ModelProviderPanel />
    : view === "harness"
      ? <HarnessPolicyPanel />
      : view === "mcp"
        ? <McpToolsPanel />
        : <DataOperationsPanel />;

  return (
    <div className="production-admin-shell" data-app-mode={me.app_mode} data-app-environment={me.app_env}>
      <aside className="production-admin-rail">
        <div className="production-admin-brand"><span aria-hidden="true">董</span><div><strong>AI 秘书管理端</strong><small>{me.enterprise.name}</small></div></div>
        <nav aria-label="管理功能">
          <button className={view === "models" ? "active" : ""} type="button" onClick={() => setView("models")}><span aria-hidden="true">模</span><strong>模型服务</strong></button>
          <button className={view === "harness" ? "active" : ""} type="button" onClick={() => setView("harness")}><span aria-hidden="true">编</span><strong>编排策略</strong></button>
          <button className={view === "mcp" ? "active" : ""} type="button" onClick={() => setView("mcp")}><span aria-hidden="true">工</span><strong>MCP 工具</strong></button>
          <button className={view === "data" ? "active" : ""} type="button" onClick={() => setView("data")}><span aria-hidden="true">数</span><strong>经营数据</strong></button>
        </nav>
        <div className="production-admin-account"><span aria-hidden="true">{me.user.display_name.slice(0, 1)}</span><div><strong>{me.user.display_name}</strong><small>{me.user.role === "fde" ? "实施与运维" : "企业管理员"}</small></div><button type="button" onClick={onLogout}>退出</button></div>
      </aside>
      <div className={`production-admin-stage${guideCollapsed ? " guide-collapsed" : ""}`}>
        {panel}
        <AdminGuide view={view} collapsed={guideCollapsed} onToggle={toggleGuide} />
      </div>
    </div>
  );
}

const guideContent: Record<AdminView, { eyebrow: string; title: string; summary: string; principles: string[] }> = {
  models: {
    eyebrow: "配置说明",
    title: "模型只负责推理",
    summary: "Anspire 是唯一模型通道。业务数据、权限和工具边界仍由产品服务端控制。",
    principles: ["保存密钥后先测试连接", "只有测试通过的模型才能启用", "停用不会删除已有配置"],
  },
  harness: {
    eyebrow: "编排边界",
    title: "策略可编辑，安全内核不可覆盖",
    summary: "这里决定问题如何理解、改写、规划和回答；每次保存生成独立版本。",
    principles: ["修改只影响新创建的消息任务", "工具白名单和事业部权限不可编辑", "先用问题模拟检查策略变化"],
  },
  mcp: {
    eyebrow: "工具说明",
    title: "工具是经营数据的唯一执行入口",
    summary: "系统内置工具执行受审查询；企业组合工具只复用这些能力，不接收任意代码。",
    principles: ["新工具默认停用", "校验数据域和依赖后再启用", "历史调用始终保留原工具标识"],
  },
  data: {
    eyebrow: "运营说明",
    title: "经营数据按完整批次生效",
    summary: "商机、项目与回款必须同时通过字段、关联和金额校验；失败会继续使用上一完整成功批次。",
    principles: ["连接测试只验证只读与结构契约", "可以先校验且不生效", "正式同步只切换完整成功批次"],
  },
};

function AdminGuide({ view, collapsed, onToggle }: { view: AdminView; collapsed: boolean; onToggle: () => void }) {
  const content = guideContent[view];
  return (
    <aside className="production-admin-guide" aria-label="当前页面说明">
      <button className="production-admin-guide-toggle" type="button" onClick={onToggle} aria-expanded={!collapsed} aria-label={collapsed ? "展开页面说明" : "收起页面说明"}><span aria-hidden="true">{collapsed ? "‹" : "›"}</span></button>
      {!collapsed && <div className="production-admin-guide-content"><small>{content.eyebrow}</small><h2>{content.title}</h2><p>{content.summary}</p><ol>{content.principles.map((item, index) => <li key={item}><span>{String(index + 1).padStart(2, "0")}</span>{item}</li>)}</ol><footer>说明栏仅解释当前功能，不影响任何运行配置。</footer></div>}
    </aside>
  );
}

function formatAdminTime(value: string | null) {
  if (!value) return "尚无记录";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

function syncStatusLabel(status: string) {
  if (status === "completed" || status === "succeeded") return "成功";
  if (status === "validated") return "校验通过";
  if (status === "rejected") return "已拒绝";
  if (status === "failed") return "失败";
  if (status === "running") return "同步中";
  if (status === "queued") return "排队中";
  return status || "未知";
}

function atomicStatusLabel(status: string) {
  if (status === "activated") return "完整批次已生效";
  if (status === "activating") return "完整批次生效中";
  if (status === "failed") return "批次生效失败";
  if (status === "rejected") return "批次已拒绝";
  if (status === "not_requested") return "仅校验，未生效";
  return status || "等待处理";
}

function shortHash(value: string | null | undefined) {
  if (!value) return "—";
  return value.length > 16 ? `${value.slice(0, 8)}…${value.slice(-6)}` : value;
}

function fieldTypeLabel(value: number) {
  const labels: Record<number, string> = {
    1: "文本",
    2: "数字",
    3: "单选",
    4: "多选",
    5: "日期",
    11: "人员",
    13: "电话",
    15: "链接",
    17: "附件",
    20: "公式",
    1001: "创建时间",
    1002: "最后更新时间",
  };
  return labels[value] ?? `类型 ${value}`;
}

function dataSourceDisplayName(value: string) {
  return value
    .replaceAll("飞书经营三表", "飞书经营数据源")
    .replaceAll("飞书三表", "飞书经营数据源");
}

function dataSourceTypeLabel(value: string) {
  if (value === "feishu_three_table") return "飞书多维表格";
  if (value === "postgres") return "标准 PostgreSQL";
  return value;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function formatValidationAmount(value: unknown) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "—";
  if (Math.abs(numeric) >= 10000) {
    return `${new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 }).format(numeric / 10000)} 万元`;
  }
  return `${new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 }).format(numeric)} 元`;
}

type DataOperationsView = "sources" | "runs" | "schedule" | "quality" | "policy";
type ExperienceWeightDraft = { high: number; medium: number; low: number; notes: string };

function DataOperationsPanel() {
  const [overview, setOverview] = useState<DataOperationsV3Overview | null>(null);
  const [sources, setSources] = useState<DataSource[]>([]);
  const [runs, setRuns] = useState<DataSyncRun[]>([]);
  const [tasks, setTasks] = useState<ScheduledTask[]>([]);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [activeView, setActiveView] = useState<DataOperationsView>("sources");
  const [confirmSourceId, setConfirmSourceId] = useState("");
  const [selectedRunId, setSelectedRunId] = useState("");
  const [weightDraft, setWeightDraft] = useState<ExperienceWeightDraft>({ high: 20, medium: 10, low: 5, notes: "" });

  async function load() {
    const [overviewResult, sourceResult, runResult, taskResult] = await Promise.all([
      productionServices.adminData.overview(),
      productionServices.adminData.sources(),
      productionServices.adminData.runs(),
      productionServices.adminData.scheduledTasks(),
    ]);
    setOverview(overviewResult);
    setSources(sourceResult.items);
    setRuns(runResult.items);
    setTasks(taskResult.items);
    setSelectedRunId((current) => current || runResult.items[0]?.id || "");
    setWeightDraft({
      high: overviewResult.experience_weight_policy.weights_json.high * 100,
      medium: overviewResult.experience_weight_policy.weights_json.medium * 100,
      low: overviewResult.experience_weight_policy.weights_json.low * 100,
      notes: overviewResult.experience_weight_policy.notes ?? "",
    });
  }

  useEffect(() => {
    let active = true;
    Promise.all([
      productionServices.adminData.overview(),
      productionServices.adminData.sources(),
      productionServices.adminData.runs(),
      productionServices.adminData.scheduledTasks(),
    ]).then(([overviewResult, sourceResult, runResult, taskResult]) => {
      if (!active) return;
      setOverview(overviewResult);
      setSources(sourceResult.items);
      setRuns(runResult.items);
      setTasks(taskResult.items);
      setSelectedRunId(runResult.items[0]?.id ?? "");
      setWeightDraft({
        high: overviewResult.experience_weight_policy.weights_json.high * 100,
        medium: overviewResult.experience_weight_policy.weights_json.medium * 100,
        low: overviewResult.experience_weight_policy.weights_json.low * 100,
        notes: overviewResult.experience_weight_policy.notes ?? "",
      });
    }).catch((loadError: unknown) => {
      if (active) setError(humanizeApiError(loadError));
    });
    return () => { active = false; };
  }, []);

  async function perform(key: string, action: () => Promise<unknown>, success: string) {
    if (busy) return;
    setBusy(key);
    setError("");
    setNotice("");
    try {
      await action();
      await load();
      setNotice(success);
    } catch (actionError) {
      setError(humanizeApiError(actionError));
    } finally {
      setBusy("");
    }
  }

  const latestRun = runs[0] ?? null;
  const selectedRun = runs.find((run) => run.id === selectedRunId) ?? latestRun;
  const enabledTasks = tasks.filter((task) => task.is_enabled).length;
  const tableBindings = overview?.sources.flatMap((source) => source.bindings) ?? [];
  const validatedBindings = tableBindings.filter((binding) => binding.validation_status === "validated").length;
  const latestSuccessful = overview?.sources.map((source) => source.latest_successful_run).filter((run): run is DataSyncRun => Boolean(run)).sort((left, right) => right.created_at.localeCompare(left.created_at))[0] ?? null;
  const latestRejected = overview?.sources.map((source) => source.latest_rejected_run).filter((run): run is DataSyncRun => Boolean(run)).sort((left, right) => right.created_at.localeCompare(left.created_at))[0] ?? null;
  const qualityRun = selectedRun ?? latestSuccessful;
  const qualityValidation = asRecord(qualityRun?.cross_table_validation_json);
  const relationshipChecks = asRecord(qualityValidation.relationship_checks);
  const amountChecks = asRecord(qualityValidation.amount_checks);
  const qualityWarnings = [
    ...(Array.isArray(qualityValidation.warnings) ? qualityValidation.warnings : []),
    ...(Array.isArray(qualityValidation.quality_warnings) ? qualityValidation.quality_warnings : []),
  ].map(asRecord).filter((item) => typeof item.message === "string");

  async function saveWeightPolicy(event: FormEvent) {
    event.preventDefault();
    const policy = overview?.experience_weight_policy;
    if (!policy || busy) return;
    if ([weightDraft.high, weightDraft.medium, weightDraft.low].some((value) => !Number.isFinite(value) || value < 0 || value > 100)) {
      setError("经验权重必须在 0% 至 100% 之间。");
      return;
    }
    if (!(weightDraft.high >= weightDraft.medium && weightDraft.medium >= weightDraft.low)) {
      setError("经验权重必须满足高 ≥ 中 ≥ 低。");
      return;
    }
    await perform("policy:save", () => productionServices.adminData.updateExperienceWeightPolicy({
      base_version: policy.version,
      weights: {
        high: weightDraft.high / 100,
        medium: weightDraft.medium / 100,
        low: weightDraft.low / 100,
      },
      label: `经验权重口径 v${policy.version + 1}`,
      notes: weightDraft.notes.trim(),
    }), `经验权重口径已生成 v${policy.version + 1}，只影响后续新批次与回答。`);
  }

  return (
    <main className="production-admin-main data-operations-main">
      <header className="production-admin-heading">
        <div><p>经营数据</p><h1>经营数据接入与同步</h1><span>集中检查只读数据源、调度计划和同步批次，不在这里修改业务事实。</span></div>
        <span className={`production-admin-status ${latestRun?.status === "failed" || latestRun?.status === "rejected" ? "risk" : latestSuccessful ? "positive" : "quiet"}`}><i aria-hidden="true" />{latestSuccessful ? "经营数据可用" : latestRun ? `最近${syncStatusLabel(latestRun.status)}` : "等待首次同步"}</span>
      </header>

      <div className="data-operations-console">
        <aside className="data-operations-nav" aria-label="经营数据模块"><strong>功能模块</strong><button className={activeView === "sources" ? "active" : ""} type="button" onClick={() => setActiveView("sources")}><span>数据接入</span><small>经营数据源</small></button><button className={activeView === "runs" ? "active" : ""} type="button" onClick={() => setActiveView("runs")}><span>同步运行</span><small>完整批次与拒绝</small></button><button className={activeView === "schedule" ? "active" : ""} type="button" onClick={() => setActiveView("schedule")}><span>调度计划</span><small>每日执行时间</small></button><button className={activeView === "quality" ? "active" : ""} type="button" onClick={() => setActiveView("quality")}><span>数据质量</span><small>关系与金额校验</small></button><button className={activeView === "policy" ? "active" : ""} type="button" onClick={() => setActiveView("policy")}><span>指标口径</span><small>经验权重版本</small></button></aside>
        <div className="data-operations-content">

      <section className="data-operations-summary" aria-label="经营数据摘要">
        <div><small>已接入数据域</small><strong>{validatedBindings} / {tableBindings.length || 3}</strong><span>已完成字段与 Schema 校验</span></div>
        <div><small>已启用调度</small><strong>{enabledTasks}</strong><span>{tasks.length ? `共 ${tasks.length} 项` : "尚未配置"}</span></div>
        <div><small>当前数据批次</small><strong>{latestSuccessful?.source_batch_id ? shortHash(latestSuccessful.source_batch_id) : "—"}</strong><span>{latestSuccessful ? atomicStatusLabel(latestSuccessful.atomic_activation_status) : "尚未激活"}</span></div>
        <div><small>指标口径版本</small><strong>v{overview?.experience_weight_policy.version ?? "—"}</strong><span>高 {weightDraft.high}% · 中 {weightDraft.medium}% · 低 {weightDraft.low}%</span></div>
      </section>

      {error && <p className="anspire-error" role="alert">{error}</p>}
      {notice && <p className="anspire-notice" role="status">{notice}</p>}

      {activeView === "sources" && <section className="data-operations-section data-source-v3-section">
        <header><div><h2>经营数据接入</h2></div><p>一个完整批次，全部数据域通过后才允许生效</p></header>
        <div className="data-source-v3-list">
          {sources.map((source) => {
            const operations = overview?.sources.find((item) => item.source_id === source.id);
            return <article className="data-source-v3-card" key={source.id}>
              <header><div className="data-source-identity"><i className={source.last_test_status ?? "pending"} aria-hidden="true" /><span><strong>{dataSourceDisplayName(source.display_name)}</strong><small>{dataSourceTypeLabel(source.source_type)} · ODS Schema {operations?.schema_version ?? source.schema_version}</small></span></div><div className="data-source-v3-policy"><small>切换策略</small><strong>完整批次切换</strong></div><label className="switch" title="启用数据源"><input type="checkbox" checked={source.is_enabled} disabled={Boolean(busy)} onChange={(event) => void perform(`source:${source.id}:toggle`, () => productionServices.adminData.updateSource(source.id, { is_enabled: event.target.checked }), event.target.checked ? "数据源已启用。" : "数据源已停用。")} /><span aria-hidden="true" /></label></header>
              <div className="feishu-binding-list">
                {operations?.bindings.map((binding) => <div className="feishu-binding-row" key={binding.domain}>
                  <div className="feishu-binding-heading"><span className={`binding-state ${binding.validation_status}`} aria-hidden="true" /><div><strong>{binding.display_name}</strong><small>{binding.configured ? `${binding.app_token_masked ?? "Base"} · ${binding.table_id ?? "Table ID 未返回"}` : "尚未绑定飞书表"}</small></div></div>
                  <dl><div><dt>字段</dt><dd>{binding.fields.length} 项</dd></div><div><dt>记录</dt><dd>{binding.record_count?.toLocaleString("zh-CN") ?? "—"}</dd></div><div><dt>Schema 哈希</dt><dd><code>{shortHash(binding.schema_hash)}</code></dd></div><div><dt>内容哈希</dt><dd><code>{shortHash(binding.content_hash)}</code></dd></div></dl>
                  <span className={`binding-status ${binding.validation_status}`}>{binding.validation_status === "validated" ? "校验通过" : binding.validation_status === "rejected" ? "已拒绝" : binding.validation_status === "configured" ? "等待校验" : "未配置"}</span>
                  <details className="feishu-field-contract"><summary>字段契约</summary><div>{binding.fields.map((field) => <span key={field.field_id}><b>{field.field_name}</b><small>{fieldTypeLabel(field.field_type)}{field.required ? " · 必填" : ""}</small></span>)}</div>{binding.warnings.map((warning) => <p key={warning}>{warning}</p>)}</details>
                </div>)}
                {!operations && <p className="data-operations-empty">正在等待 ODS 3.0 绑定状态。</p>}
              </div>
              <footer className="data-source-v3-footer"><span>最近连接测试：{formatAdminTime(source.last_tested_at)}</span><div><button className="secondary-button" type="button" disabled={Boolean(busy)} onClick={() => void perform(`source:${source.id}:test`, () => productionServices.adminData.testSource(source.id), "连接、只读权限与基础结构均已通过验证。")}>{busy === `source:${source.id}:test` ? "测试中…" : "测试连接"}</button><button className="secondary-button" type="button" disabled={Boolean(busy) || !source.is_enabled} onClick={() => void perform(`source:${source.id}:validate`, () => productionServices.adminData.validateSource(source.id), "已创建校验任务；本次不会切换当前经营数据。")}>{busy === `source:${source.id}:validate` ? "创建中…" : "校验但不生效"}</button><button className="primary-button" type="button" disabled={Boolean(busy) || !source.is_enabled} onClick={() => setConfirmSourceId(source.id)}>同步并切换批次</button></div></footer>
              {confirmSourceId === source.id && <div className="atomic-sync-confirmation" role="alertdialog" aria-label="确认同步并切换完整批次"><div><strong>确认创建正式同步任务？</strong><span>系统将读取当前经营数据源。只有字段、跨域关系与金额恒等式全部通过，才会一次性切换完整数据批次。</span></div><div><button type="button" className="secondary-button" onClick={() => setConfirmSourceId("")}>取消</button><button type="button" className="primary-button" disabled={Boolean(busy)} onClick={() => void perform(`source:${source.id}:sync`, () => productionServices.adminData.syncSource(source.id), "已创建正式同步任务；经营数据校验通过后将切换完整批次。").then(() => setConfirmSourceId(""))}>{busy === `source:${source.id}:sync` ? "创建中…" : "确认执行"}</button></div></div>}
              {source.last_test_error && <p className="data-source-error">{source.last_test_error}</p>}
            </article>;
          })}
          {!sources.length && <p className="data-operations-empty">尚未配置数据源。客户部署时需要先完成标准脱敏源库连接。</p>}
        </div>
      </section>}

      {activeView === "schedule" && <section className="data-operations-section">
        <header><div><h2>调度计划</h2></div><p>调度器只创建任务，数据由独立 Worker 处理</p></header>
        <div className="scheduled-task-list">
          {tasks.map((task) => <article key={task.id}><span className={`task-state ${task.is_enabled ? "enabled" : ""}`} aria-hidden="true" /><div><strong>{task.key}</strong><small>{task.cron_expression} · {task.timezone}</small></div><dl><div><dt>下次执行</dt><dd>{formatAdminTime(task.next_run_at)}</dd></div><div><dt>上次入队</dt><dd>{formatAdminTime(task.last_enqueued_at)}</dd></div></dl><button className="secondary-button" type="button" disabled={Boolean(busy) || !task.is_enabled} onClick={() => void perform(`task:${task.id}`, () => productionServices.adminData.runScheduledTask(task.id), "已按当前任务配置创建一次立即运行。")}>{busy === `task:${task.id}` ? "创建中…" : "运行一次"}</button></article>)}
          {!tasks.length && <p className="data-operations-empty">当前没有同步调度计划。</p>}
        </div>
      </section>}

      {activeView === "runs" && <section className="data-operations-section data-runs-v3-section">
        <header><div><h2>同步运行</h2></div><p>批次不可变，可复核成功与拒绝原因</p></header>
        <div className="batch-outcome-strip"><article><small>最近成功批次</small><strong>{latestSuccessful?.source_batch_id ? shortHash(latestSuccessful.source_batch_id) : "尚无"}</strong><span>{latestSuccessful ? `${formatAdminTime(latestSuccessful.activated_at)} · ${latestSuccessful.records_written} 条已激活` : "完成首次正式切换后显示"}</span></article><article className={latestRejected ? "risk" : ""}><small>最近拒绝批次</small><strong>{latestRejected?.source_batch_id ? shortHash(latestRejected.source_batch_id) : "无"}</strong><span>{latestRejected ? `${formatAdminTime(latestRejected.completed_at)} · ${latestRejected.error_code ?? "校验未通过"}` : "当前没有被拒绝的经营数据批次"}</span></article></div>
        <div className="data-sync-table" role="table" aria-label="最近数据同步运行">
          <div role="row"><span role="columnheader">状态</span><span role="columnheader">运行方式</span><span role="columnheader">商机 / 项目 / 回款</span><span role="columnheader">数据时间</span><span role="columnheader">完成时间</span></div>
          {runs.slice(0, 16).map((run) => <button type="button" role="row" aria-selected={selectedRun?.id === run.id} key={run.id} onClick={() => setSelectedRunId(run.id)}><span role="cell"><i className={`sync-run-state ${run.status}`} aria-hidden="true" />{syncStatusLabel(run.status)}<small>{atomicStatusLabel(run.atomic_activation_status)}</small></span><span role="cell">{run.trigger_type === "manual_validation" ? "仅校验" : run.trigger_type === "manual" ? "人工同步" : run.trigger_type}</span><span role="cell">{run.source_record_counts_json.opportunity ?? "—"} / {run.source_record_counts_json.delivery ?? "—"} / {run.source_record_counts_json.collection ?? "—"}</span><span role="cell">{formatAdminTime(run.source_data_as_of)}</span><span role="cell">{formatAdminTime(run.completed_at)}</span></button>)}
          {!runs.length && <p className="data-operations-empty">同步开始后，批次状态会在这里显示。</p>}
        </div>
        {selectedRun && <div className="selected-run-detail"><div><small>批次 ID</small><code>{selectedRun.source_batch_id ?? selectedRun.id}</code></div><div><small>数据集版本</small><strong>{selectedRun.dataset_version ?? "—"}</strong></div><div><small>切换结果</small><strong>{atomicStatusLabel(selectedRun.atomic_activation_status)}</strong></div><div><small>经验权重版本</small><strong>{selectedRun.experience_weight_policy_id ? shortHash(selectedRun.experience_weight_policy_id) : "未记录"}</strong></div>{qualityWarnings.length > 0 && <p className="warning">{qualityWarnings.length} 项兼容或身份变化提示，请在数据质量中复核。</p>}{selectedRun.error_message && <p>{selectedRun.error_message}</p>}</div>}
      </section>}

      {activeView === "quality" && <section className="data-operations-section data-quality-section data-quality-v3-section"><header><div><h2>数据质量</h2></div><p>逐项展示关系和金额校验，不折叠成单一分数</p></header>{qualityRun ? <>
        <div className="data-quality-metrics"><article><small>商机记录</small><strong>{qualityRun.source_record_counts_json.opportunity ?? 0}</strong><span>业务主键必须唯一</span></article><article><small>项目记录</small><strong>{qualityRun.source_record_counts_json.delivery ?? 0}</strong><span>必须关联赢单商机</span></article><article><small>回款记录</small><strong>{qualityRun.source_record_counts_json.collection ?? 0}</strong><span>必须关联项目与商机</span></article><article className={qualityValidation.valid === false ? "risk" : ""}><small>经营数据校验</small><strong>{qualityValidation.valid === true ? "通过" : qualityValidation.valid === false ? "拒绝" : "待校验"}</strong><span>{atomicStatusLabel(qualityRun.atomic_activation_status)}</span></article></div>
        <div className="data-quality-detail-grid"><section><small>跨表关联检查</small><h3>关系完整性</h3><dl>{Object.entries(relationshipChecks).map(([key, value]) => <div key={key}><dt>{key.replaceAll("_", " ")}</dt><dd className={value === "passed" ? "passed" : ""}>{value === "passed" ? "通过" : String(value)}</dd></div>)}</dl>{!Object.keys(relationshipChecks).length && <p>当前批次没有可展示的关系检查。</p>}</section><section><small>金额恒等式</small><h3>经营金额对账</h3><div className="financial-invariant-grid"><span><small>签约金额</small><strong>{formatValidationAmount(amountChecks.signed_amount)}</strong></span><span><small>项目合同额</small><strong>{formatValidationAmount(amountChecks.contract_amount)}</strong></span><span><small>回款应收额</small><strong>{formatValidationAmount(amountChecks.receivable_amount)}</strong></span><span><small>已回款</small><strong>{formatValidationAmount(amountChecks.collected_amount)}</strong></span><span><small>未回款</small><strong>{formatValidationAmount(amountChecks.outstanding_amount)}</strong></span></div><p>必须同时满足：签约额 = 合同额 = 应收额；应收额 = 已回款 + 未回款。</p></section></div>
        {qualityWarnings.length > 0 && <div className="data-quality-warning-list"><strong>需人工复核</strong><div>{qualityWarnings.map((warning, index) => <p key={`${String(warning.code ?? "warning")}-${index}`}><span>{String(warning.domain ?? "batch")}</span>{String(warning.message)}</p>)}</div></div>}
        <div className="hash-verification-list"><strong>批次哈希</strong>{(["opportunity", "delivery", "collection"] as const).map((domain) => <div key={domain}><span>{domain === "opportunity" ? "商机总览" : domain === "delivery" ? "项目交付" : "财务回款"}</span><code>Schema {shortHash(qualityRun.source_schema_hashes_json[domain])}</code><code>内容 {shortHash(qualityRun.source_content_hashes_json[domain])}</code></div>)}</div>
      </> : <p className="data-operations-empty">完成首次校验后，这里会显示跨域关系、金额与哈希。</p>}</section>}

      {activeView === "policy" && <section className="data-operations-section experience-policy-section"><header><div><h2>指标口径</h2></div><p>独立于 Harness Prompt，由经营计算层强制执行</p></header>{overview ? <form onSubmit={(event) => void saveWeightPolicy(event)}><div className="experience-policy-heading"><div><small>当前版本</small><strong>v{overview.experience_weight_policy.version}</strong><span>{overview.experience_weight_policy.label}</span></div><div><small>观察窗口</small><strong>{overview.experience_weight_policy.observation_windows_json.join(" / ")} 天</strong><span>只报告偏差，不自动调权</span></div><div><small>生效时间</small><strong>{formatAdminTime(overview.experience_weight_policy.activated_at)}</strong><span>每次保存生成不可变新版本</span></div></div><div className="experience-policy-editor"><label><span>高靠谱度</span><div><input type="number" min={0} max={100} step={1} value={weightDraft.high} onChange={(event) => setWeightDraft((current) => ({ ...current, high: Number(event.target.value) }))} /><b>%</b></div><small>当前默认 20%</small></label><label><span>中靠谱度</span><div><input type="number" min={0} max={100} step={1} value={weightDraft.medium} onChange={(event) => setWeightDraft((current) => ({ ...current, medium: Number(event.target.value) }))} /><b>%</b></div><small>当前默认 10%</small></label><label><span>低靠谱度</span><div><input type="number" min={0} max={100} step={1} value={weightDraft.low} onChange={(event) => setWeightDraft((current) => ({ ...current, low: Number(event.target.value) }))} /><b>%</b></div><small>当前默认 5%</small></label></div><label className="experience-policy-notes"><span>版本备注</span><textarea rows={3} maxLength={1000} value={weightDraft.notes} onChange={(event) => setWeightDraft((current) => ({ ...current, notes: event.target.value }))} placeholder="记录本次调整依据；不要填写客户敏感信息。" /></label><div className="experience-policy-note"><strong>这不是赢单概率</strong><p>经验权重预测 = 在途商机预估金额 × 当前靠谱度权重。赢单使用实际签约金额，搁置与归档不进入预测；模型不能自行改变该口径。</p></div><footer><span>保存只影响后续新同步批次和回答，历史证据继续保留原口径版本。</span><button className="primary-button" type="submit" disabled={Boolean(busy)}>{busy === "policy:save" ? "正在生成新版本…" : "保存为新版本"}</button></footer></form> : <div className="anspire-loading">正在读取指标口径…</div>}</section>}
        </div>
      </div>
    </main>
  );
}

function ModelProviderPanel() {
  const [config, setConfig] = useState<ModelProviderConfig | null>(null);
  const [catalog, setCatalog] = useState<AdminModelCatalog | null>(null);
  const [apiKey, setApiKey] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [modelSearch, setModelSearch] = useState("");
  const [modelView, setModelView] = useState<"all" | "authorized" | "pending">("all");

  useEffect(() => {
    let active = true;
    Promise.all([
      productionServices.adminModels.get(),
      productionServices.adminModels.catalog(),
    ])
      .then(([result, catalogResult]) => {
        if (!active) return;
        setConfig(result);
        setCatalog(catalogResult);
      })
      .catch((loadError: unknown) => {
        if (active) setError(humanizeApiError(loadError));
      });
    return () => { active = false; };
  }, []);

  async function reload() {
    const [result, catalogResult] = await Promise.all([
      productionServices.adminModels.get(),
      productionServices.adminModels.catalog(),
    ]);
    setConfig(result);
    setCatalog(catalogResult);
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!config || busy) return;
    setBusy("save");
    setError("");
    setNotice("");
    try {
      const next = await productionServices.adminModels.update({
        model_id: config.model_id,
        ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
      });
      setConfig(next);
      setApiKey("");
      await reload();
      setNotice("网关凭证已加密保存。凭证发生变化后，各模型需要重新测试与授权。");
    } catch (saveError) {
      setError(humanizeApiError(saveError));
    } finally {
      setBusy(null);
    }
  }

  async function testModel(model: AdminModelAuthorization) {
    if (!config || busy) return;
    setBusy(`test:${model.model_id}`);
    setError("");
    setNotice("");
    try {
      const result = await productionServices.adminModels.testModel(model.model_id);
      await reload();
      setNotice(`${model.display_name} 测试通过，${result.latency_ms} ms。现在可以授权给董事长。`);
    } catch (testError) {
      setError(humanizeApiError(testError));
      await reload();
    } finally {
      setBusy(null);
    }
  }

  async function toggleAuthorization(model: AdminModelAuthorization) {
    if (!config || busy) return;
    setBusy(`authorize:${model.model_id}`);
    setError("");
    setNotice("");
    try {
      await productionServices.adminModels.authorize(
        model.model_id,
        !model.is_authorized,
        model.display_name,
      );
      await reload();
      setNotice(model.is_authorized ? `${model.display_name} 已取消授权。` : `${model.display_name} 已授权给董事长。`);
    } catch (toggleError) {
      setError(humanizeApiError(toggleError));
    } finally {
      setBusy(null);
    }
  }

  async function setDefault(model: AdminModelAuthorization) {
    if (busy || model.is_default) return;
    setBusy(`default:${model.model_id}`);
    setError("");
    setNotice("");
    try {
      await productionServices.adminModels.setDefault(model.model_id);
      await reload();
      setNotice(`${model.display_name} 已设为新会话默认模型。`);
    } catch (defaultError) {
      setError(humanizeApiError(defaultError));
    } finally {
      setBusy(null);
    }
  }

  const status = !config?.is_configured
    ? { label: "未配置", tone: "quiet" }
    : catalog?.models.some((item) => item.is_authorized)
      ? { label: `${catalog.models.filter((item) => item.is_authorized).length} 个模型已授权`, tone: "positive" }
      : { label: "等待模型授权", tone: "attention" };
  const visibleModels = (catalog?.models ?? [])
    .filter((model) => model.selectable)
    .filter((model) => modelView === "all"
      || (modelView === "authorized" ? model.is_authorized : !model.is_authorized))
    .filter((model) => {
      const query = modelSearch.trim().toLocaleLowerCase("zh-CN");
      return !query || [model.display_name, model.name, model.family, model.model_id]
        .some((value) => value.toLocaleLowerCase("zh-CN").includes(query));
    });

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
        <header><div><p>企业共享网关</p><h2>Anspire 凭证</h2></div><span>模型授权与凭证分离管理</span></header>
        {!config ? <div className="anspire-loading" aria-live="polite">正在读取企业模型配置…</div> : <>
          <div className="anspire-settings-grid">
            <label><span>API Key</span><input type="password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder={config.api_key_masked ?? "输入 Anspire API Key"} autoComplete="off" spellCheck={false} /><small>{config.is_configured ? `已保存 ${config.api_key_masked}；留空不会替换。` : "保存后以企业独立密钥加密，页面不会再次返回明文。"}</small></label>
            <label className="wide"><span>API 接口</span><input value={config.endpoint_url} readOnly aria-readonly="true" /><small>地址由系统锁定，管理员不能改成其他兼容网关。</small></label>
            <label className="wide"><span>授权策略</span><input value="逐模型测试通过后，由管理员加入授权" readOnly aria-readonly="true" /><small>凭证只定义企业共享网关；默认模型和董事长可选范围在下方单独管理。</small></label>
          </div>
          {config.last_test_error && <p className="anspire-error" role="alert">{config.last_test_error}</p>}
          {error && <p className="anspire-error" role="alert">{error}</p>}
          {notice && <p className="anspire-notice" role="status">{notice}</p>}
          <footer><p>密钥不会写入浏览器存储、日志或回答证据；更换凭证会自动撤销旧测试结论。</p><div><button className="primary-button" type="submit" disabled={Boolean(busy)}>{busy === "save" ? "正在保存…" : "保存网关配置"}</button></div></footer>
        </>}
      </form>
      <section className="admin-model-authorization">
        <header><div><small>董事长可用模型</small><h2>测试与授权</h2><p>只有使用当前凭证测试成功的模型，才允许出现在董事长工作台。</p></div><span>{catalog?.credential_version ? `凭证版本 v${catalog.credential_version}` : "等待配置"}</span></header>
        {catalog && <div className="admin-model-directory-controls"><label><span className="sr-only">搜索模型</span><input type="search" value={modelSearch} onChange={(event) => setModelSearch(event.target.value)} placeholder="搜索模型名称、系列或 ID" /></label><div role="group" aria-label="模型目录筛选"><button type="button" className={modelView === "all" ? "active" : ""} onClick={() => setModelView("all")}>全部</button><button type="button" className={modelView === "authorized" ? "active" : ""} onClick={() => setModelView("authorized")}>已授权</button><button type="button" className={modelView === "pending" ? "active" : ""} onClick={() => setModelView("pending")}>待评估</button></div><span>{visibleModels.length} 个模型</span></div>}
        {!catalog ? <div className="anspire-loading">正在读取模型授权目录…</div> : <div className="admin-model-list">{visibleModels.map((model) => {
          const testCurrent = model.test_status === "success" && model.tested_credential_version === model.current_credential_version;
          return <article key={model.model_id} className={model.is_authorized ? "authorized" : ""}><div className="admin-model-identity"><span className={`model-test-dot ${testCurrent ? "success" : model.test_status}`} aria-hidden="true" /><div><strong>{model.display_name}</strong><small>{model.family} · {model.model_id}</small></div></div><p>{model.profile}</p><div className="admin-model-state"><span>{testCurrent ? `${model.last_test_latency_ms ?? "—"} ms` : model.test_status === "failed" ? "测试失败" : model.tested_credential_version ? "凭证变更，需复测" : "尚未测试"}</span>{model.is_default && <b>默认</b>}{model.is_authorized && !model.is_default && <button type="button" disabled={Boolean(busy)} onClick={() => void setDefault(model)}>{busy === `default:${model.model_id}` ? "设置中…" : "设为默认"}</button>}</div><div className="admin-model-actions"><button className="secondary-button" type="button" disabled={Boolean(busy) || !config?.is_configured} onClick={() => void testModel(model)}>{busy === `test:${model.model_id}` ? "测试中…" : testCurrent ? "重新测试" : "测试模型"}</button><button className={model.is_authorized ? "secondary-button" : "primary-button"} type="button" disabled={Boolean(busy) || (!model.is_authorized && !testCurrent)} onClick={() => void toggleAuthorization(model)}>{busy === `authorize:${model.model_id}` ? "更新中…" : model.is_authorized ? "取消授权" : "加入授权"}</button></div></article>;
        })}{!visibleModels.length && <p className="data-operations-empty">没有符合当前筛选条件的模型。</p>}</div>}
      </section>
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

type HarnessModule = keyof HarnessBusinessConfig["prompts"] | "glossary" | "rules" | "simulate" | "versions";

const harnessModuleGroups: Array<{ label: string; items: Array<{ key: HarnessModule; label: string }> }> = [
  { label: "身份与回答", items: [{ key: "system", label: "基础身份" }, { key: "data_answer", label: "经营回答" }, { key: "general_answer", label: "个人泛化回答" }] },
  { label: "理解与规划", items: [{ key: "route", label: "意图识别" }, { key: "rewrite", label: "查询改写" }, { key: "plan", label: "任务规划" }, { key: "glossary", label: "业务术语表" }] },
  { label: "规则与验证", items: [{ key: "rules", label: "快速规则" }, { key: "simulate", label: "问题模拟与追踪" }, { key: "versions", label: "版本记录" }] },
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
  const [activeModule, setActiveModule] = useState<HarnessModule>("rewrite");

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
  const activePrompt = harnessPromptFields.find((field) => field.key === activeModule) ?? null;

  return (
    <main className="production-admin-main harness-admin-main">
      <header className="production-admin-heading">
        <div><p>编排策略</p><h1>可运营 Harness</h1><span>选择一个模块专注编辑；权限、工具白名单与证据约束由安全内核强制执行。</span></div>
        <div className="production-admin-heading-actions"><span className="production-admin-status positive"><i aria-hidden="true" />{current ? `当前 v${current.version}` : "正在读取"}</span><button className="secondary-button" type="button" onClick={() => setActiveModule("simulate")}>运行模拟</button></div>
      </header>

      {!draft || !current ? <div className="anspire-loading">正在读取编排策略…</div> : <>
        <section className="harness-safety-strip">
          <div><small>不可编辑安全内核</small><strong>服务端权限 · 注册工具白名单 · 数据回答强制证据</strong></div>
          <span>最多 4 次工具调用 · 并发 3 · 修正规划 1 次 · 禁止联网、文件与任意代码</span>
        </section>

        <div className="harness-console">
          <aside className="harness-module-nav" aria-label="编排策略模块">
            <strong>编辑模块</strong>
            {harnessModuleGroups.map((group) => <section key={group.label}><small>{group.label}</small>{group.items.map((item) => <button className={activeModule === item.key ? "active" : ""} type="button" key={item.key} onClick={() => setActiveModule(item.key)}>{item.label}</button>)}</section>)}
          </aside>
          <section className="harness-module-canvas">
            {activePrompt && <div className="harness-prompt-editor"><header><div><small>当前 v{current.version} · 保存后只影响新任务</small><h2>{activePrompt.label}</h2><p>{activePrompt.note}；输出结构与权限范围不可由 Prompt 覆盖。</p></div><span>{draft.prompts[activePrompt.key].length.toLocaleString("zh-CN")} 字符</span></header><textarea aria-label={activePrompt.label} value={draft.prompts[activePrompt.key]} onChange={(event) => updatePrompt(activePrompt.key, event.target.value)} spellCheck={false} /><footer><code>{"{question}  {organization_scope}  {conversation_context}"}</code><span>安全变量由服务端按授权范围注入</span></footer></div>}

            {activeModule === "glossary" && <div className="harness-focused-section"><header><div><small>理解与规划</small><h2>业务术语表</h2><p>把企业内部表达映射为稳定业务含义，不改变指标口径。</p></div><button className="secondary-button" type="button" onClick={() => setDraft((existing) => existing ? { ...existing, glossary: [...existing.glossary, { term: "", canonical: "", category: "其他", enabled: true }] } : existing)}>新增术语</button></header><div className="harness-glossary focused">{draft.glossary.map((entry, index) => <div key={`${entry.term}-${index}`}><input aria-label="术语" value={entry.term} placeholder="术语" onChange={(event) => setDraft((existing) => existing ? { ...existing, glossary: existing.glossary.map((item, itemIndex) => itemIndex === index ? { ...item, term: event.target.value } : item) } : existing)} /><input aria-label="标准名称" value={entry.canonical} placeholder="标准名称" onChange={(event) => setDraft((existing) => existing ? { ...existing, glossary: existing.glossary.map((item, itemIndex) => itemIndex === index ? { ...item, canonical: event.target.value } : item) } : existing)} /><input aria-label="类别" value={entry.category} placeholder="类别" onChange={(event) => setDraft((existing) => existing ? { ...existing, glossary: existing.glossary.map((item, itemIndex) => itemIndex === index ? { ...item, category: event.target.value } : item) } : existing)} /><label className="switch"><input type="checkbox" checked={entry.enabled} onChange={(event) => setDraft((existing) => existing ? { ...existing, glossary: existing.glossary.map((item, itemIndex) => itemIndex === index ? { ...item, enabled: event.target.checked } : item) } : existing)} /><span aria-hidden="true" /></label><button type="button" aria-label="移除术语" onClick={() => setDraft((existing) => existing ? { ...existing, glossary: existing.glossary.filter((_, itemIndex) => itemIndex !== index) } : existing)}>×</button></div>)}</div></div>}

            {activeModule === "rules" && <div className="harness-focused-section"><header><div><small>规则与验证</small><h2>快速规则</h2><p>只加速高置信路由；查询改写、权限与证据校验始终执行。</p></div><button className="secondary-button" type="button" onClick={addRule}>新增规则</button></header><div className="harness-rule-list focused">{draft.fast_rules.map((rule, index) => <article key={rule.id}><header><label className="switch"><input type="checkbox" checked={rule.enabled} onChange={(event) => updateRule(index, { enabled: event.target.checked })} /><span aria-hidden="true" /></label><input value={rule.name} aria-label="规则名称" onChange={(event) => updateRule(index, { name: event.target.value })} /><span>优先级 <input type="number" min={0} max={1000} value={rule.priority} onChange={(event) => updateRule(index, { priority: Number(event.target.value) })} /></span><button type="button" aria-label="删除规则" onClick={() => setDraft((existing) => existing ? { ...existing, fast_rules: existing.fast_rules.filter((_, ruleIndex) => ruleIndex !== index) } : existing)}>×</button></header><div className="harness-rule-fields"><label><span>路由</span><select value={rule.route} onChange={(event) => updateRule(index, { route: event.target.value as HarnessFastRule["route"], candidate_tools: event.target.value === "general" ? [] : rule.candidate_tools })}><option value="data">经营问数</option><option value="general">个人泛化</option></select></label><label><span>匹配方式</span><select value={rule.match_mode} onChange={(event) => updateRule(index, { match_mode: event.target.value as HarnessFastRule["match_mode"] })}><option value="any">任一命中</option><option value="all">全部命中</option></select></label><label><span>关键词（逗号分隔）</span><input value={rule.terms.join("，")} onChange={(event) => updateRule(index, { terms: event.target.value.split(/[，,]/).map((item) => item.trim()).filter(Boolean) })} /></label><label><span>排除词（逗号分隔）</span><input value={rule.exclusions.join("，")} onChange={(event) => updateRule(index, { exclusions: event.target.value.split(/[，,]/).map((item) => item.trim()).filter(Boolean) })} /></label></div>{rule.route === "data" && <div className="harness-tool-picks"><span>候选 MCP（最多 4 个）</span>{plannerTools.map((tool) => <label key={tool.tool_name}><input type="checkbox" checked={rule.candidate_tools.includes(tool.tool_name)} onChange={() => toggleCandidateTool(index, tool.tool_name)} /><span>{tool.display_name}</span></label>)}</div>}</article>)}</div></div>}

            {activeModule === "simulate" && <div className="harness-focused-section"><header><div><small>规则与验证</small><h2>问题模拟与技术追踪</h2><p>模拟不调用经营工具；正式追踪默认只显示脱敏技术摘要。</p></div></header><div className="harness-validation-grid"><div className="harness-simulator"><label><span>示例问题</span><textarea rows={4} value={question} onChange={(event) => setQuestion(event.target.value)} /></label><button type="button" className="secondary-button" disabled={busy === "simulate" || !question.trim()} onClick={() => void simulate()}>{busy === "simulate" ? "正在模拟…" : "运行模拟"}</button>{simulation && <dl><div><dt>路由</dt><dd>{simulation.route}</dd></div><div><dt>来源</dt><dd>{simulation.route_source}{simulation.matched_rule_id ? ` · ${simulation.matched_rule_id}` : ""}</dd></div><div><dt>候选工具</dt><dd>{simulation.candidate_tools.join("、") || "无"}</dd></div><div><dt>歧义</dt><dd>{simulation.validation_issues.join("；") || "无"}</dd></div></dl>}</div><div className="harness-metrics"><strong>近 {metrics?.window_days ?? 30} 天</strong><div><span><b>{metrics?.message_count ?? 0}</b>消息任务</span><span><b>{Math.round((metrics?.structured_output_rate ?? 0) * 100)}%</b>结构有效</span><span><b>{Math.round((metrics?.tool_success_rate ?? 0) * 100)}%</b>工具成功</span></div><small>意图准确率需由基准集人工标注，不用线上自循环分数替代。</small></div></div><div className="harness-traces"><header><strong>最近技术追踪</strong><small>不显示问题、回答、个人记忆和业务正文</small></header>{traces.slice(0, 8).map((trace) => <article key={trace.message_id}><span className={`trace-route ${trace.route}`}>{trace.route ?? "—"}</span><div><strong>{trace.route_source ?? "unknown"} · v{trace.harness_version ?? "—"}</strong><small>{trace.organization_unit_count} 个事业部 · {trace.tools.join("、") || "未调用工具"}</small></div><span>{trace.stages.length} 阶段</span>{trace.diagnostic_shared_until && <i>已授权正文诊断</i>}</article>)}{!traces.length && <p>尚无正式任务追踪。</p>}</div></div>}

            {activeModule === "versions" && <div className="harness-focused-section"><header><div><small>规则与验证</small><h2>版本记录</h2><p>每次保存与恢复都会生成不可变版本，运行中的任务继续使用创建时快照。</p></div><code>{current.config_hash.slice(0, 12)}</code></header><section className="harness-version-rail focused"><div>{versions.slice(0, 12).map((version) => <article key={version.id}><span>v{version.version}</span><small>{new Date(version.activated_at).toLocaleString("zh-CN")}</small><code>{version.config_hash.slice(0, 10)}</code><button type="button" disabled={version.is_active || busy === "restore"} onClick={() => void restore(version)}>{version.is_active ? "当前" : "恢复"}</button></article>)}</div></section></div>}
          </section>
        </div>

        {error && <p className="anspire-error" role="alert">{error}</p>}
        {notice && <p className="anspire-notice" role="status">{notice}</p>}
        <div className="harness-save-bar"><span>{dirty ? "当前模块有尚未保存的策略修改" : `所有更改已保存 · v${current.version}`}</span><div><button type="button" className="secondary-button" disabled={!dirty || Boolean(busy)} onClick={() => setDraft(copyHarnessConfig(current.config))}>放弃</button><button type="button" className="secondary-button" disabled={busy === "simulate"} onClick={() => setActiveModule("simulate")}>先模拟</button><button type="button" className="primary-button" disabled={!dirty || Boolean(busy)} onClick={() => void save()}>{busy === "save" ? "正在校验并保存…" : "保存并生效"}</button></div></div>
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
  const [createOpen, setCreateOpen] = useState(false);
  const [createDraft, setCreateDraft] = useState({ tool_name: "custom_", display_name: "", description: "", category: "综合经营", component_tools: [] as string[], operator_note: "" });

  async function loadCatalog(preferredToolName?: string) {
    const result = await productionServices.adminMcp.list();
    setCatalog(result);
    const next = result.tools.find((item) => item.tool_name === preferredToolName)
      ?? result.tools.find((item) => item.tool_name === selectedName)
      ?? result.tools[0];
    setSelectedName(next?.tool_name ?? "");
    if (next) {
      setDraft({
        display_name: next.display_name,
        description: next.description,
        timeout_seconds: next.timeout_seconds,
        max_rows: next.max_rows,
        operator_note: next.operator_note ?? "",
      });
    }
  }

  useEffect(() => {
    let active = true;
    productionServices.adminMcp.list().then((result) => {
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
      }).catch((loadError: unknown) => {
        if (active) setError(humanizeApiError(loadError));
      });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!createOpen) return;
    const previousOverflow = document.body.style.overflow;
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape" && !busy) setCreateOpen(false);
    }
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", closeOnEscape);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", closeOnEscape);
    };
  }, [busy, createOpen]);

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

  function toggleComponent(toolName: string) {
    setCreateDraft((current) => {
      const selected = current.component_tools.includes(toolName);
      if (!selected && current.component_tools.length >= 4) return current;
      return {
        ...current,
        component_tools: selected
          ? current.component_tools.filter((name) => name !== toolName)
          : [...current.component_tools, toolName],
      };
    });
  }

  async function createCompositeTool(event: FormEvent) {
    event.preventDefault();
    if (busy || createDraft.component_tools.length === 0) return;
    setBusy("create");
    setError("");
    setNotice("");
    try {
      const created = await productionServices.adminMcp.create({
        tool_name: createDraft.tool_name.trim(),
        display_name: createDraft.display_name.trim(),
        description: createDraft.description.trim(),
        category: createDraft.category.trim(),
        component_tools: createDraft.component_tools,
        operator_note: createDraft.operator_note.trim() || undefined,
      });
      await loadCatalog(created.tool_name);
      setCreateOpen(false);
      setCreateDraft({ tool_name: "custom_", display_name: "", description: "", category: "综合经营", component_tools: [], operator_note: "" });
      setNotice("组合工具已创建并保持停用。完成就绪度校验后再启用执行与自动规划。");
    } catch (createError) {
      setError(humanizeApiError(createError));
    } finally {
      setBusy("");
    }
  }

  const builtInTools = catalog?.tools.filter((tool) => tool.source_type === "built_in") ?? [];

  return (
    <main className="production-admin-main mcp-admin-main">
      <header className="production-admin-heading">
        <div><p>执行能力</p><h1>MCP 工具注册表</h1><span>只开放经过审计的经营工具；查询规划、意图路由和后续 Skill 共用同一配置。</span></div>
        <div className="production-admin-heading-actions"><span className="production-admin-status positive"><i aria-hidden="true" />{catalog ? `${catalog.enabled_count} / ${catalog.tools.length} 已启用` : "正在读取"}</span><button className="primary-button" type="button" onClick={() => { setError(""); setNotice(""); setCreateOpen(true); }}>新增工具</button></div>
      </header>
      <section className="mcp-boundary-note"><strong>受控边界</strong><span>可以新增由 1–4 个系统工具组成的企业组合工具；仍不接受任意 SQL、脚本或外部地址。</span></section>
      <div className="mcp-registry-layout">
        <section className="mcp-tool-index" aria-label="MCP 工具列表">
          <header><div><strong>工具</strong><small>{catalog ? `${catalog.planner_count} 个可被规划器选择` : "加载中"}</small></div><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索工具" aria-label="搜索 MCP 工具" /></header>
          <div>{filtered.map((tool) => <article className={selectedName === tool.tool_name ? "selected" : ""} key={tool.tool_name}><button type="button" onClick={() => selectTool(tool)}><span><strong>{tool.display_name}</strong><small>{tool.tool_name}</small><em>{tool.source_type === "composite" ? "企业组合" : "系统内置"}</em></span><i className={`mcp-readiness ${tool.readiness}`} title={tool.readiness_issues.join("；")} aria-label={tool.readiness} /></button><label className="switch mcp-inline-switch" title="启用工具"><input type="checkbox" checked={tool.is_enabled} disabled={Boolean(busy)} onChange={(event) => void updateTool(tool.tool_name, { is_enabled: event.target.checked }, `enable:${tool.tool_name}`)} /><span aria-hidden="true" /></label></article>)}</div>
          {!filtered.length && <p className="mcp-empty">没有匹配的工具。</p>}
        </section>
        <section className="mcp-tool-detail" aria-live="polite">
          {!selected ? <div className="anspire-loading">请选择一个 MCP 工具。</div> : <form onSubmit={save}>
            <header><div><small>{selected.category} · {selected.source_type === "composite" ? "企业组合" : "系统内置"}</small><h2>{selected.display_name}</h2><code>{selected.tool_name} · v{selected.definition_version}</code></div><span className={`mcp-detail-status ${selected.readiness}`}>{selected.readiness === "ready" ? "可运行" : selected.readiness === "disabled" ? "已停用" : "数据未就绪"}</span></header>
            <div className="mcp-tool-controls"><label><span>允许执行</span><span className="switch"><input type="checkbox" checked={selected.is_enabled} disabled={Boolean(busy)} onChange={(event) => void updateTool(selected.tool_name, { is_enabled: event.target.checked }, "enable")} /><span aria-hidden="true" /></span><small>关闭后，MCP Hub 会直接拒绝调用。</small></label><label><span>允许自动规划</span><span className="switch"><input type="checkbox" checked={selected.planner_enabled} disabled={Boolean(busy) || !selected.is_enabled} onChange={(event) => void updateTool(selected.tool_name, { planner_enabled: event.target.checked }, "planner")} /><span aria-hidden="true" /></span><small>关闭后仍可保留工具，但 Harness 不会自动选择。</small></label></div>
            <div className="mcp-tool-form"><label><span>显示名称</span><input value={draft.display_name} maxLength={160} onChange={(event) => setDraft((current) => ({ ...current, display_name: event.target.value }))} /></label><label className="wide"><span>用途说明</span><textarea rows={3} value={draft.description} maxLength={2000} onChange={(event) => setDraft((current) => ({ ...current, description: event.target.value }))} /></label><label><span>超时（秒）</span><input type="number" min={3} max={60} value={draft.timeout_seconds} onChange={(event) => setDraft((current) => ({ ...current, timeout_seconds: Number(event.target.value) }))} /></label><label><span>最大返回行数</span><input type="number" min={1} max={100} value={draft.max_rows} onChange={(event) => setDraft((current) => ({ ...current, max_rows: Number(event.target.value) }))} /></label><label className="wide"><span>运维备注</span><textarea rows={2} value={draft.operator_note} maxLength={500} onChange={(event) => setDraft((current) => ({ ...current, operator_note: event.target.value }))} placeholder="仅管理端可见" /></label></div>
            <section className="mcp-schema"><header><strong>规划器可用参数</strong><small>{selected.domains.length ? `依赖数据域：${selected.domains.join("、")}` : "不依赖经营事实"}</small></header><div>{Object.entries(selected.parameters).map(([name, schema]) => <span key={name}><code>{name}</code><small>{String(schema.description ?? schema.type ?? "参数")}</small></span>)}{!Object.keys(selected.parameters).length && <p>该工具不接受可变业务参数，查询范围由权限令牌注入。</p>}</div></section>
            {selected.source_type === "composite" && <section className="mcp-composition"><header><strong>组合执行</strong><small>按依赖工具各自的权限与返回边界执行</small></header><div>{selected.component_tools.map((name, index) => <span key={name}><i>{String(index + 1).padStart(2, "0")}</i><strong>{catalog?.tools.find((tool) => tool.tool_name === name)?.display_name ?? name}</strong><code>{name}</code></span>)}</div></section>}
            {selected.readiness_issues.length > 0 && <p className="anspire-error" role="alert">{selected.readiness_issues.join("；")}</p>}
            {error && <p className="anspire-error" role="alert">{error}</p>}
            {notice && <p className="anspire-notice" role="status">{notice}</p>}
            <footer><span>配置变更会写入审计日志，并由规划器和 MCP Hub 同时执行。</span><div><button className="secondary-button" type="button" disabled={Boolean(busy)} onClick={() => void validate()}>{busy === "validate" ? "正在校验…" : "校验就绪度"}</button><button className="primary-button" type="submit" disabled={Boolean(busy) || !draft.display_name.trim() || !draft.description.trim()}>{busy === "save" ? "正在保存…" : "保存配置"}</button></div></footer>
          </form>}
        </section>
      </div>
      {createOpen && <div className="mcp-create-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !busy) setCreateOpen(false); }}><section className="mcp-create-dialog" role="dialog" aria-modal="true" aria-labelledby="mcp-create-title"><header><div><small>企业组合工具</small><h2 id="mcp-create-title">新增 MCP 工具</h2><p>组合已有的受审查询能力，不创建新的 SQL 或外部连接。</p></div><button type="button" disabled={Boolean(busy)} onClick={() => setCreateOpen(false)} aria-label="关闭">×</button></header><form onSubmit={createCompositeTool}><div className="mcp-create-grid"><label><span>工具名称</span><input value={createDraft.display_name} maxLength={160} autoFocus onChange={(event) => setCreateDraft((current) => ({ ...current, display_name: event.target.value }))} placeholder="例如：重点客户风险体检" /></label><label><span>工具标识</span><input value={createDraft.tool_name} maxLength={64} spellCheck={false} onChange={(event) => setCreateDraft((current) => ({ ...current, tool_name: event.target.value.toLowerCase().replace(/[^a-z0-9_]/g, "" ) }))} /><small>必须以 custom_ 开头，发布后不可修改。</small></label><label><span>业务分类</span><input value={createDraft.category} maxLength={80} onChange={(event) => setCreateDraft((current) => ({ ...current, category: event.target.value }))} /></label><label className="wide"><span>用途说明</span><textarea rows={3} value={createDraft.description} maxLength={2000} onChange={(event) => setCreateDraft((current) => ({ ...current, description: event.target.value }))} placeholder="说明规划器何时应使用这个工具，以及它能够回答什么问题。" /></label></div><fieldset className="mcp-component-picker"><legend>选择组成工具 <small>{createDraft.component_tools.length} / 4</small></legend><p>执行时会自动合并共同参数、数据时间和数字证据。</p><div>{builtInTools.map((tool) => { const checked = createDraft.component_tools.includes(tool.tool_name); return <label className={checked ? "selected" : ""} key={tool.tool_name}><input type="checkbox" checked={checked} disabled={!checked && createDraft.component_tools.length >= 4} onChange={() => toggleComponent(tool.tool_name)} /><span><strong>{tool.display_name}</strong><small>{tool.category} · {tool.domains.join("、") || "权限范围"}</small></span><i aria-hidden="true">{checked ? "✓" : "+"}</i></label>; })}</div></fieldset><label className="mcp-create-note"><span>运维备注</span><textarea rows={2} value={createDraft.operator_note} maxLength={500} onChange={(event) => setCreateDraft((current) => ({ ...current, operator_note: event.target.value }))} placeholder="仅管理员可见，可留空" /></label>{error && <p className="anspire-error" role="alert">{error}</p>}<footer><p>创建后默认停用。请先校验依赖工具和数据域，再手动启用。</p><div><button className="secondary-button" type="button" disabled={Boolean(busy)} onClick={() => setCreateOpen(false)}>取消</button><button className="primary-button" type="submit" disabled={Boolean(busy) || !createDraft.tool_name.match(/^custom_[a-z0-9_]+$/) || !createDraft.display_name.trim() || createDraft.description.trim().length < 12 || !createDraft.category.trim() || createDraft.component_tools.length === 0}>{busy === "create" ? "正在创建…" : "创建工具"}</button></div></footer></form></section></div>}
    </main>
  );
}
