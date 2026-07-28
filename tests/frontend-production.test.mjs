import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { resolveAppMode } from "../app/production/runtime.mjs";

const read = (path) => readFile(new URL(path, import.meta.url), "utf8");

test("production mode is explicit and invalid configuration fails closed", () => {
  assert.equal(resolveAppMode(undefined), "demo");
  assert.equal(resolveAppMode("demo"), "demo");
  assert.equal(resolveAppMode(" production "), "production");
  assert.throws(() => resolveAppMode("prod"), /Invalid NEXT_PUBLIC_APP_MODE/);
});

test("demo and production use physically separate route entrypoints", async () => {
  const page = await read("../app/page.tsx");
  const productionPage = await read("../app/page.production.tsx");
  assert.match(page, /return <DemoProductPrototype \/>/);
  assert.doesNotMatch(page, /ProductionApplication|production-app/);
  assert.match(productionPage, /return <ProductionApplication \/>/);
  assert.doesNotMatch(productionPage, /prototype-data|DemoProductPrototype/);
});

test("production application has no fixture dependency or demo credential", async () => {
  const productionApp = await read("../app/production/production-app.tsx");
  const productionWorkspace = await read("../app/production/production-workspace.tsx");
  const productionSource = `${productionApp}\n${productionWorkspace}`;
  const types = await read("../app/production/types.ts");
  assert.match(productionApp, /ProductionWorkspace/);
  assert.doesNotMatch(productionSource, /prototype-data|initialConversations|organizationCatalog|Demo@2026|Admin@2026/);
  assert.match(productionSource, /生产模式不会使用演示数据/);
  assert.match(productionSource, /尚未配置可分析事业部/);
  assert.match(productionSource, /organizationUnits\.map/);
  assert.match(productionSource, /脱敏演示环境/);
  assert.match(productionSource, /data-app-environment/);
  assert.match(productionSource, /report\.status === "published"/);
  assert.match(productionSource, /正在等待真实处理结果/);
  assert.match(productionWorkspace, /new EventSource/);
  assert.match(productionWorkspace, /streamUrl\(activeConversationId/);
  assert.match(productionWorkspace, /StructuredBarChart/);
  assert.match(productionWorkspace, /answer-structured-chart/);
  assert.match(types, /app_env: AppEnvironment/);
  assert.match(types, /app_mode: BackendAppMode/);
});

test("API client sends cookie credentials and CSRF on mutations", async () => {
  const client = await read("../app/production/api-client.ts");
  assert.match(client, /credentials: "include"/);
  assert.match(client, /const CSRF_COOKIE_NAME = "exec_csrf"/);
  assert.match(client, /const CSRF_HEADER_NAME = "X-CSRF-Token"/);
  assert.match(client, /headers\.set\(CSRF_HEADER_NAME, csrf\)/);
  assert.match(client, /skipCsrf/);
  assert.match(client, /cache: "no-store"/);
});

test("production services cover the active user domains without file upload", async () => {
  const services = await read("../app/production/services.ts");
  for (const path of [
    "/auth/login",
    "/auth/me",
    "/auth/change-password",
    "/auth/logout",
    "/organization-units",
    "/conversations",
    "/projects",
    "/memories",
    "/reports",
    "/jobs",
  ]) {
    assert.match(services, new RegExp(path.replaceAll("/", "\\/")));
  }
  assert.match(services, /authorizedOrganizationIds\.has\(unit\.id\) && unit\.enabled_for_analysis && unit\.data_connected/);
  assert.match(services, /me\.user\.role !== "executive"/);
  assert.match(services, /"Idempotency-Key"/);
  assert.match(services, /organization_unit_id/);
  assert.doesNotMatch(services, /FormData|FileMetadata|\/files/);
  assert.doesNotMatch(services, /prototype-data|organizationCatalog/);
});

test("management surface exposes only the controlled Anspire model channel", async () => {
  const application = await read("../app/production/production-app.tsx");
  const admin = await read("../app/production/production-admin.tsx");
  const services = await read("../app/production/services.ts");
  const types = await read("../app/production/types.ts");

  assert.match(application, /ProductionAdmin/);
  assert.match(admin, /Anspire 单一模型通道/);
  assert.match(admin, /open-gateway\.anspire\.ai/);
  assert.match(admin, /modelFamilies/);
  assert.match(admin, /optgroup/);
  assert.match(admin, /全量模型目录/);
  assert.match(admin, /item\.selectable/);
  assert.match(admin, /固定|锁定/);
  assert.match(admin, /保存配置/);
  assert.match(admin, /测试连接/);
  assert.match(admin, /启用 Anspire/);
  assert.match(services, /\/admin\/model-provider/);
  assert.match(types, /provider: "anspire"/);
  assert.doesNotMatch(admin, /OpenAI|Anthropic|自定义接口|endpoint_url.*onChange/);
  assert.doesNotMatch(services, /OPENAI_BASE_URL|HERMES_PROVIDER|model_api_key/);
});

test("management exposes a restrained MCP registry instead of arbitrary execution", async () => {
  const admin = await read("../app/production/production-admin.tsx");
  const services = await read("../app/production/services.ts");
  const types = await read("../app/production/types.ts");

  assert.match(admin, /MCP 工具/);
  assert.match(admin, /允许自动规划/);
  assert.match(admin, /最大返回行数/);
  assert.match(admin, /依赖数据域/);
  assert.match(admin, /规划器可用参数/);
  assert.match(admin, /新增工具/);
  assert.match(admin, /企业组合工具/);
  assert.match(admin, /1–4 个系统工具/);
  assert.match(services, /adminMcp[\s\S]*create/);
  assert.match(types, /McpCompositeToolCreate/);
  assert.match(services, /\/admin\/mcp-tools/);
  assert.match(types, /planner_enabled/);
  assert.doesNotMatch(admin, /SQL 编辑器|脚本编辑器|自定义工具 URL/);
});

test("management includes controlled data operations and a collapsible guide", async () => {
  const admin = await read("../app/production/production-admin.tsx");
  const services = await read("../app/production/services.ts");
  const types = await read("../app/production/types.ts");

  assert.match(admin, /数据运营/);
  assert.match(admin, /DataOperationsPanel/);
  assert.match(admin, /飞书三表绑定/);
  assert.match(admin, /校验但不生效/);
  assert.match(admin, /同步并原子切换/);
  assert.match(admin, /跨表关联检查/);
  assert.match(admin, /金额恒等式/);
  assert.match(admin, /经验权重口径/);
  assert.match(admin, /这不是赢单概率/);
  assert.match(admin, /AdminGuide/);
  assert.match(admin, /收起页面说明/);
  assert.match(admin, /executive-workbench-theme/);
  assert.match(admin, /saved.*\?.*saved.*: "system"/);
  assert.match(services, /\/admin\/data-operations\/overview/);
  assert.match(services, /\/admin\/data-sources/);
  assert.match(services, /\/validate/);
  assert.match(services, /\/admin\/data-sync-runs/);
  assert.match(services, /\/admin\/scheduled-tasks/);
  assert.match(services, /\/admin\/metric-policies\/opportunity-experience-weight/);
  assert.match(types, /source_schema_hashes_json/);
  assert.match(types, /source_record_counts_json/);
  assert.match(types, /cross_table_validation_json/);
  assert.match(types, /OpportunityExperienceWeightPolicy/);
});

test("phase three workspace uses scoped multi-organization chat without legacy empty-state copy", async () => {
  const workspace = await read("../app/production/production-workspace.tsx");
  const services = await read("../app/production/services.ts");
  const types = await read("../app/production/types.ts");

  assert.match(types, /mode: "all_authorized" \| "selected"/);
  assert.match(services, /organization_scope: organizationScope/);
  assert.match(workspace, /function OrganizationPicker/);
  assert.match(workspace, /function apply\(\)/);
  assert.match(workspace, /organization-apply/);
  assert.match(workspace, /scope-change-divider/);
  assert.match(workspace, /workspace-topbar-date/);
  assert.doesNotMatch(workspace, /今天需要我先看什么？|今天需要我先看什麼？|从一个问题开始|從一個問題開始/);
});

test("phase three management exposes versioned harness configuration and redacted traces", async () => {
  const admin = await read("../app/production/production-admin.tsx");
  const services = await read("../app/production/services.ts");

  assert.match(admin, /编排策略/);
  assert.match(admin, /安全内核/);
  assert.match(admin, /快速规则/);
  assert.match(admin, /消息任务/);
  assert.match(admin, /恢复会生成一个新的当前版本/);
  assert.match(services, /\/admin\/harness\/config/);
  assert.match(services, /\/admin\/harness\/simulate/);
  assert.match(services, /\/admin\/harness\/traces/);
  assert.doesNotMatch(admin, /type="url"|placeholder="SQL"|<textarea[^>]*aria-label="脚本/);
});
