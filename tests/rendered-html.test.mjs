import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);

  return worker.fetch(
    new Request("http://localhost/", {
      headers: { accept: "text/html" },
    }),
    {
      ASSETS: {
        fetch: async () => new Response("Not found", { status: 404 }),
      },
    },
    {
      waitUntil() {},
      passThroughOnException() {},
    },
  );
}

test("server-renders the configured executive assistant entry", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);

  const html = await response.text();
  assert.match(html, /<html lang="zh-CN">/i);
  assert.match(html, /<title>董事长 AI 秘书 \| 经营决策工作台<\/title>/i);
  if (/data-app-mode="production"/.test(html)) {
    assert.match(html, /本机生产环境/);
    assert.match(html, /可信经营服务正在准备/);
    assert.match(html, /生产模式只读取已授权的企业数据/);
    assert.match(html, /正在验证安全会话/);
    assert.doesNotMatch(html, /当前原型全部经营数据均为演示样本/);
  } else {
    assert.match(html, /先核对范围，再回答经营问题/);
    assert.match(html, /高层端/);
    assert.match(html, /管理端/);
    assert.match(html, /企业数字有来源、有时间、有口径/);
    assert.match(html, /当前原型全部经营数据均为演示样本/);
  }
  assert.doesNotMatch(html, /今日经营变化/);
  assert.doesNotMatch(html, /codex-preview|SkeletonPreview|react-loading-skeleton/i);
});

test("includes accessible controls for login and first-use security", async () => {
  const productionApp = await readFile(new URL("../app/production/production-app.tsx", import.meta.url), "utf8");

  assert.match(productionApp, /href="#production-login-form"/);
  assert.match(productionApp, /id="production-login-form"/);
  assert.match(productionApp, /autoComplete="username"/);
  assert.match(productionApp, /autoComplete="current-password"/);
  assert.match(productionApp, /type=\{showPassword \? "text" : "password"\}/);
  assert.match(productionApp, /联系企业管理员/);
});

test("prototype source contains the required functional contracts", async () => {
  const page = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");
  const data = await readFile(new URL("../app/prototype-data.ts", import.meta.url), "utf8");
  const styles = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");

  assert.match(page, /accept="\.pdf,\.docx,\.xlsx,\.pptx"/);
  assert.match(page, /十个标准演示场景/);
  assert.match(data, /最多两轮范围选择/);
  assert.match(page, /不会检索其他会话文件/);
  assert.match(page, /没有生成近似金额/);
  assert.match(page, /const COMPOSER_MAX_LENGTH = 8000/);
  assert.match(page, /const COMPOSER_HINT_THRESHOLD = COMPOSER_MAX_LENGTH \* 0\.8/);
  assert.match(page, /function OrganizationPicker/);
  assert.match(page, /configuredByAdmin/);
  assert.match(page, /function PersonalCenterWindow/);
  assert.match(page, /function ProjectDialog/);
  assert.match(page, /aria-label="新建项目"/);
  assert.match(page, /const \[sidebarProjects, setSidebarProjects\] = useState<SidebarProject\[]>/);
  assert.match(page, /function requestArchiveConversation/);
  assert.match(page, /function requestArchiveProjectTasks/);
  assert.match(page, /function requestRemoveProject/);
  assert.match(page, /归档后，这条会话将从置顶、项目和最近列表中隐藏/);
  assert.match(page, /只会移除项目分组/);
  assert.match(page, /aria-haspopup="menu"/);
  assert.match(page, /"zh-CN".*"zh-TW".*"en"/s);
  assert.doesNotMatch(page, /ResponsePreferenceControl|回答：标准|maxLength=\{1200\}/);
  assert.match(styles, /\.preferences-window/);
  assert.match(styles, /\.organization-popover/);
  assert.match(styles, /\.project-dialog-layer/);
  assert.match(styles, /\.sidebar-project-context-menu/);
  for (const id of ["overview", "target", "change", "forecast", "customers", "delivery", "collection", "organization"]) {
    assert.match(data, new RegExp(`\\b${id}: \\{`));
  }
});
