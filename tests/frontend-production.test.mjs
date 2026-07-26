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
  const types = await read("../app/production/types.ts");
  assert.doesNotMatch(productionApp, /prototype-data|initialConversations|organizationCatalog|Demo@2026|Admin@2026/);
  assert.match(productionApp, /生产模式不会使用演示数据/);
  assert.match(productionApp, /尚未配置可分析事业部/);
  assert.match(productionApp, /organizationUnits\.map/);
  assert.match(productionApp, /脱敏演示环境/);
  assert.match(productionApp, /data-app-environment/);
  assert.match(productionApp, /report\.status === "published"/);
  assert.match(productionApp, /正在等待真实处理结果/);
  assert.match(productionApp, /hasPendingAssistant/);
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

test("production services cover phase-one user domains", async () => {
  const services = await read("../app/production/services.ts");
  for (const path of [
    "/auth/login",
    "/auth/me",
    "/auth/change-password",
    "/auth/logout",
    "/organization-units",
    "/conversations",
    "/projects",
    "/files",
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
  assert.match(services, /original_name|FileMetadata/);
  assert.doesNotMatch(services, /prototype-data|organizationCatalog/);
});
