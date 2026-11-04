import assert from "node:assert/strict";
import test from "node:test";

async function render(path = "/") {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);

  return worker.fetch(
    new Request(`http://localhost${path}`, {
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

test("server-renders the public homepage without requiring authentication", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);

  const html = await response.text();
  assert.match(html, /<title>海宇绘品 - AI电商图片工作台｜商品主图详情图一键复刻<\/title>/i);
  assert.doesNotMatch(html, /正在确认登录状态/);
  assert.match(html, /海宇绘品/);
  assert.doesNotMatch(html, /Your site is taking shape|codex-preview/i);
});

test("server-renders the authentication gate for protected assets", async () => {
  const response = await render("/assets");
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /正在确认登录状态/);
  assert.match(html, /安全地恢复你的会话/);
});

test("server-renders the public login route", async () => {
  const response = await render("/auth/login?returnTo=%2Fassets");
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);

  const html = await response.text();
  assert.match(html, /id="auth-title">登录/);
  assert.match(html, /让每一个好商品，都有一张好图片/);
  assert.match(html, /手机号/);
  assert.match(html, /验证码/);
  assert.match(html, /密码登录/);
  assert.match(html, /立即注册/);
});

test("core product routes render", async () => {
  for (const path of [
    "/workspace/local-edit",
    "/workspace/detail-pack",
    "/jobs",
    "/assets",
  ]) {
    const response = await render(path);
    assert.equal(response.status, 200, `${path} should render`);
  }
});

test("anonymous tool HTML includes the public introduction without mounting the private workspace", async () => {
  for (const path of ["/style-replica", "/workspace/product-image/change-color", "/workspace/detail-pack"]) {
    const response = await render(path);
    const html = await response.text();
    assert.equal(response.status, 200);
    assert.match(html, /data-public-tool=/);
    assert.match(html, /如何开始使用/);
    assert.match(html, /application\/ld\+json/);
    assert.doesNotMatch(html, /正在确认登录状态/);
    assert.match(html, /rel="nofollow"/);
  }
});
