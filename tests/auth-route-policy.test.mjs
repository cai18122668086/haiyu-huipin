import assert from "node:assert/strict";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

import { build } from "esbuild";

const root = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const utf8 = (relativePath) => readFile(path.join(root, relativePath), "utf8");

async function importBundled(entry) {
  const directory = await mkdtemp(path.join(os.tmpdir(), "polaris-auth-test-"));
  const output = path.join(directory, "module.mjs");
  await build({
    absWorkingDir: root,
    entryPoints: [entry],
    bundle: true,
    format: "esm",
    platform: "node",
    outfile: output,
    logLevel: "silent",
  });
  const importedModule = await import(`${pathToFileURL(output).href}?t=${Date.now()}`);
  await rm(directory, { recursive: true, force: true });
  return importedModule;
}

test("visitors can browse display pages while tools and personal data require authentication", async () => {
  const routes = await importBundled("lib/auth/routes.ts");
  assert.equal(routes.isPublicRoute("/auth/login"), true);
  assert.equal(routes.isPublicRoute("/auth/register"), true);
  assert.equal(routes.isPublicRoute("/legal/terms"), true);
  assert.equal(routes.isPublicRoute("/legal/privacy"), true);
  for (const pathname of ["/", "/tools", "/help", "/inspiration"]) {
    assert.equal(routes.isPublicRoute(pathname), true, pathname);
    assert.equal(routes.routeRequiresWorkspace(pathname), false, pathname);
  }
  for (const pathname of ["/workspace/local-edit", "/workspace/detail-pack", "/workspace/reference-remix", "/style-replica", "/commerce-crawler", "/jobs", "/account", "/dashboard", "/admin", "/unknown"]) {
    assert.equal(routes.isPublicRoute(pathname), false, pathname);
  }
  assert.equal(routes.isPublicRoute("/assets"), false);
  assert.equal(routes.routeRequiresWorkspace("/account"), false);
  assert.equal(routes.routeRequiresWorkspace("/legal/privacy"), false);
  assert.equal(routes.routeRequiresWorkspace("/assets"), true);
});

test("auth checks terminate stalled SDK work and abort slow requests", async () => {
  const { withAuthTimeout, authRequestInit } = await importBundled("lib/auth/request-timeout.ts");
  await assert.rejects(withAuthTimeout(new Promise(() => {}), 20), /AUTH_CHECK_TIMEOUT/);
  assert.equal(await withAuthTimeout(Promise.resolve(true), 20), true);
  const pending = authRequestInit({ credentials: "include" }, 20);
  await withAuthTimeout(new Promise((resolve) => pending.signal.addEventListener("abort", resolve)), 2000);
  assert.equal(pending.signal.aborted, true);
  assert.equal(pending.credentials, "include");
  const caller = new AbortController();
  const combined = authRequestInit({ signal: caller.signal });
  caller.abort();
  assert.equal(combined.signal.aborted, true);
});

test("phone registration verifies ownership, sets a password and requires explicit agreement", async () => {
  const [registerForm, phoneForm, authApi, loginForm] = await Promise.all([
    utf8("features/auth/components/register-form.tsx"),
    utf8("features/auth/components/phone-login-form.tsx"),
    utf8("features/auth/api/auth.ts"),
    utf8("features/auth/components/login-form.tsx"),
  ]);
  assert.match(registerForm, /flow="register"/);
  assert.match(phoneForm, /if \(!accepted\)/);
  assert.match(phoneForm, /signInWithPhoneCode\(code, flow/);
  assert.match(phoneForm, /password !== confirmation/);
  assert.match(phoneForm, /autoComplete="new-password"/);
  assert.match(phoneForm, /autoComplete="one-time-code"/);
  assert.match(phoneForm, /target="_blank"/);
  assert.match(phoneForm, /rel="noreferrer"/);
  assert.match(authApi, /acceptedAgreements: true, flow, password/);
  assert.match(loginForm, /signInWithPhonePassword/);
  assert.doesNotMatch(loginForm, /邮箱|EmailPassword/);
  assert.doesNotMatch(registerForm, /邮箱|EmailPassword/);
});

test("legal documents use confirmed operator details and actual data flows", async () => {
  const terms = await utf8("app/legal/terms/page.tsx");
  const privacy = await utf8("app/legal/privacy/page.tsx");

  for (const document of [terms, privacy]) {
    assert.match(document, /海宇数科（广东横琴）科技有限公司/);
    assert.match(document, /人工智能处理环境/);
    assert.match(document, /一个月/);
    assert.doesNotMatch(document, /蕉点AI|武汉光瞳科技有限公司/);
  }

  assert.match(terms, /用于模型训练/);
  assert.match(privacy, /main@haiyushuke\.cn/);
  assert.match(privacy, /17722052522/);
  assert.doesNotMatch(terms + privacy, /揭阳市腾利科技服务有限公司|1474953130@qq\.com|新亨商务中心/);
  assert.match(privacy, /中国香港/);
  assert.match(privacy, /用于模型训练/);
});

test("returnTo accepts internal business routes and rejects open redirects or auth loops", async () => {
  const { sanitizeReturnTo } = await importBundled("lib/auth/return-to.ts");
  assert.equal(sanitizeReturnTo("/inspiration?case=ins_test"), "/inspiration?case=ins_test");
  assert.equal(sanitizeReturnTo("/workspace/local-edit?draft=123"), "/workspace/local-edit?draft=123");
  assert.equal(sanitizeReturnTo("/assets?kind=image#recent"), "/assets?kind=image#recent");
  for (const unsafe of [
    "https://evil.example/steal",
    "//evil.example/steal",
    "/\\evil.example/steal",
    "/%5cevil.example/steal",
    "/auth/login",
    "/auth/register?returnTo=/assets",
  ]) {
    assert.equal(sanitizeReturnTo(unsafe), "/", unsafe);
  }
});

test("final API 401 is centralised without browser token storage", async () => {
  const [client, boundary, sessionEvents, providers] = await Promise.all([
    utf8("lib/api/client.ts"),
    utf8("components/auth/auth-boundary.tsx"),
    utf8("lib/auth/session-events.ts"),
    utf8("app/providers.tsx"),
  ]);

  assert.match(client, /response\.status !== 401/);
  assert.match(client, /emitAuthUnauthorised\("api-401"\)/);
  assert.match(client, /credentials: "include"/);
  assert.match(client, /globalThis\.fetch\(request\)/);
  assert.match(client, /fetch: runtimeFetch/);
  assert.match(boundary, /queryClient\.clear\(\)/);
  assert.match(boundary, /onAuthUnauthorised\(\(\) =>/);
  assert.match(boundary, /if \(!publicRoute\) redirectToLogin\(\)/);
  assert.match(boundary, /if \(!developmentAuth\) initializeAuthClient/);
  assert.match(boundary, /onAuthSessionChanged/);
  assert.match(boundary, /if \(publicRoute \|\| developmentAuth\) return;\s*let cancelled = false/);
  assert.match(sessionEvents, /polaris:auth:unauthorised/);
  assert.match(providers, /<AuthBoundary>\{children\}<\/AuthBoundary>/);

  const authSources = await Promise.all([
    utf8("lib/auth/client.ts"),
    utf8("features/auth/api/auth.ts"),
    utf8("components/auth/auth-boundary.tsx"),
  ]);
  for (const source of authSources) {
    assert.doesNotMatch(source, /localStorage\s*\./);
    assert.doesNotMatch(source, /sessionStorage\s*\./);
    assert.doesNotMatch(source, /Authorization\s*:/i);
  }
});
