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

test("server-renders the protected executive assistant entry", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);

  const html = await response.text();
  assert.match(html, /<html lang="zh-CN">/i);
  assert.match(html, /<title>董事长 AI 秘书 \| 经营决策工作台<\/title>/i);
  assert.match(html, /先核对范围，再回答经营问题/);
  assert.match(html, /高层端/);
  assert.match(html, /管理端/);
  assert.match(html, /企业数字有来源、有时间、有口径/);
  assert.match(html, /当前原型全部经营数据均为演示样本/);
  assert.doesNotMatch(html, /今日经营变化/);
  assert.doesNotMatch(html, /codex-preview|SkeletonPreview|react-loading-skeleton/i);
});

test("includes accessible controls for login and first-use security", async () => {
  const response = await render();
  const html = await response.text();

  assert.match(html, /href="#login-form"/);
  assert.match(html, /id="login-form"/);
  assert.match(html, /autoComplete="username"/);
  assert.match(html, /autoComplete="current-password"/);
  assert.match(html, /type="password"/);
  assert.match(html, /联系企业管理员/);
});

test("prototype source contains the required functional contracts", async () => {
  const page = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");
  const data = await readFile(new URL("../app/prototype-data.ts", import.meta.url), "utf8");

  assert.match(page, /accept="\.pdf,\.docx,\.xlsx,\.pptx"/);
  assert.match(page, /十个标准演示场景/);
  assert.match(data, /最多两轮范围选择/);
  assert.match(page, /不会检索其他会话文件/);
  assert.match(page, /没有生成近似金额/);
  for (const id of ["overview", "target", "change", "forecast", "customers", "delivery", "collection", "organization"]) {
    assert.match(data, new RegExp(`\\b${id}: \\{`));
  }
});
