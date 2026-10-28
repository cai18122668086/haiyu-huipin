import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";
import test from "node:test";
import { build } from "esbuild";

async function load(entry) {
  const directory = await mkdtemp(path.join(os.tmpdir(), "polaris-diagnostic-"));
  try {
    const output = path.join(directory, "entry.mjs");
    const source = entry === "auth-runtime" ? { stdin: { contents: 'export * from "./lib/auth/diagnostics"; export * from "./lib/auth/fetch-observer"; export * from "./lib/auth/request-timeout"; export * from "./lib/auth/diagnostic-payload";', resolveDir: process.cwd() } } : { entryPoints: [entry] };
    await build({ ...source, bundle: true, platform: "node", format: "esm", outfile: output, logLevel: "silent" });
    return await import(pathToFileURL(output).href);
  } finally { await rm(directory, { recursive: true, force: true }); }
}

const diagnostic = {
  id: "auth_12345678-1234-1234-1234-123456789abc",
  occurred_at: "2026-10-22T08:00:00.000Z",
  phase: "email_verification", reason: "server", path: "/assets",
  elapsed_ms: 9000, http_status: 503, request_ids: ["req_abc123"],
};

test("authentication retries only transient failures and never a final 4xx or offline state", async () => {
  const { authFailureReason: reason, authFailureCanRetry: retry } = await load("lib/auth/diagnostic-payload.ts");
  assert.equal(reason(new Error("AUTH_CHECK_TIMEOUT"), true), "timeout");
  assert.equal(reason(new TypeError("fetch failed"), true), "network");
  assert.equal(reason(new Error(), true, 503), "server");
  assert.equal(reason(new Error(), false), "offline");
  assert.equal(retry("timeout"), true);
  assert.equal(retry("server", 503), true);
  assert.equal(retry("network", 401), false);
  assert.equal(retry("server", 403), false);
  assert.equal(retry("offline"), false);
  assert.equal(retry("unexpected"), false);
});

const pending = () => new Promise(() => {});
async function browserGlobals(operation) {
  const oldWindow = globalThis.window;
  globalThis.window = { location: { pathname: "/admin" } };
  Object.defineProperty(globalThis.navigator, "onLine", { value: true, configurable: true });
  try { return await operation(); }
  finally {
    if (oldWindow === undefined) delete globalThis.window; else globalThis.window = oldWindow;
    delete globalThis.navigator.onLine;
  }
}
async function requestFor(runtime, context, path = "/user/email/verify") {
  return runtime.boundedAuthRequest({ url: `https://example.test/api/auth${path}`, requestInit: { method: path.endsWith("refresh") ? "POST" : "GET", credentials: "include" }, userContext: { polarisAuthTrace: context } });
}
function timeoutReport(runtime, run, phase = "email_verification") {
  return runtime.createAuthDiagnostic(new Error("AUTH_CHECK_TIMEOUT"), phase, run.started, run);
}

test("v2 distinguishes a stalled transport from a 200 response stalled in body reading", async () => browserGlobals(async () => {
  const runtime = await load("auth-runtime");
  const run = runtime.beginAuthDiagnostics();
  try {
    const fetch = runtime.observeAuthFetch(pending, "transport");
    const operation = runtime.observeAuthCheck(run, 1, "email_verification", async (context) => {
      const request = await requestFor(runtime, context);
      return fetch(request.url, request.requestInit);
    });
    await assert.rejects(runtime.withAuthTimeout(operation, 15), /AUTH_CHECK_TIMEOUT/);
    const report = timeoutReport(runtime, run);
    assert.equal(report.evidence.location, "AUTH_WAIT_HEADERS");
    assert.equal(report.evidence.requests[0].http_status, undefined);
  } finally { runtime.finishAuthDiagnostics(run); }

  const bodyRun = runtime.beginAuthDiagnostics();
  let controller;
  const body = new ReadableStream({ start(value) { controller = value; value.enqueue(new TextEncoder().encode('{"status":"OK","isVerified":true}')); } });
  const response = new Response(body, { status: 200 });
  try {
    const fetch = runtime.observeAuthFetch(async () => response, "transport");
    const operation = runtime.observeAuthCheck(bodyRun, 1, "email_verification", async (context) => {
      const request = await requestFor(runtime, context);
      const returned = await fetch(request.url, request.requestInit);
      assert.equal(returned, response, "observer preserves the actual Response object");
      return returned.clone().json();
    });
    await assert.rejects(runtime.withAuthTimeout(operation, 15), /AUTH_CHECK_TIMEOUT/);
    const report = timeoutReport(runtime, bodyRun);
    assert.equal(report.evidence.location, "AUTH_WAIT_BODY");
    assert.equal(report.evidence.requests[0].http_status, 200);
    assert.equal(report.http_status, null, "legacy retry status remains error-only");
    assert.ok(Number.isInteger(report.evidence.requests[0].headers_received_ms));
    assert.ok(Number.isInteger(report.evidence.requests[0].body_started_ms));
    controller.close();
    assert.deepEqual(await operation, { status: "OK", isVerified: true });
    assert.equal(response.bodyUsed, false, "observer did not consume the original body");
  } finally { runtime.finishAuthDiagnostics(bodyRun); }
}));

test("v2 preserves both attempts and separates SDK waiting before a request and after a complete body", async () => browserGlobals(async () => {
  const runtime = await load("auth-runtime");
  const run = runtime.beginAuthDiagnostics();
  try {
    const first = runtime.observeAuthCheck(run, 1, "email_verification", pending);
    await assert.rejects(runtime.withAuthTimeout(first, 10), /AUTH_CHECK_TIMEOUT/);
    const initial = timeoutReport(runtime, run);
    assert.equal(initial.evidence.location, "AUTH_BEFORE_REQUEST");
    const fetch = runtime.observeAuthFetch(async () => new Response('{"status":"OK","isVerified":true}', { status: 200 }), "transport");
    const second = runtime.observeAuthCheck(run, 2, "email_verification", async (context) => {
      const request = await requestFor(runtime, context);
      const response = await fetch(request.url, request.requestInit);
      await runtime.observeAuthResponse({ fetchResponse: response.clone(), requestInit: request.requestInit });
      await response.clone().json();
      return pending();
    });
    await assert.rejects(runtime.withAuthTimeout(second, 15), /AUTH_CHECK_TIMEOUT/);
    const final = timeoutReport(runtime, run);
    assert.equal(final.id, initial.id, "one stable diagnostic ID covers both retries");
    assert.equal(final.evidence.location, "AUTH_AFTER_BODY");
    assert.equal(final.evidence.checks.length, 2);
    assert.ok(final.evidence.checks.every((check) => check.timed_out_ms !== undefined));
    assert.equal(final.evidence.requests[0].attempt, 2);
    assert.equal(final.evidence.requests[0].http_status, 200);
    assert.deepEqual(runtime.parseAuthDiagnostic(final), final);
  } finally { runtime.finishAuthDiagnostics(run); }
}));

test("late responses from an earlier attempt cannot overwrite the next attempt's status", async () => browserGlobals(async () => {
  const runtime = await load("auth-runtime");
  const run = runtime.beginAuthDiagnostics();
  let release;
  try {
    const firstFetch = runtime.observeAuthFetch(() => new Promise((resolve) => { release = resolve; }), "transport");
    const first = runtime.observeAuthCheck(run, 1, "email_verification", async (context) => {
      const request = await requestFor(runtime, context);
      return firstFetch(request.url, request.requestInit);
    });
    await assert.rejects(runtime.withAuthTimeout(first, 10), /AUTH_CHECK_TIMEOUT/);
    timeoutReport(runtime, run);
    const second = runtime.observeAuthCheck(run, 2, "email_verification", async (context) => { await requestFor(runtime, context); return pending(); });
    await assert.rejects(runtime.withAuthTimeout(second, 10), /AUTH_CHECK_TIMEOUT/);
    release(new Response(null, { status: 503 }));
    await first;
    const report = timeoutReport(runtime, run);
    assert.equal(report.http_status, null);
    assert.equal(report.reason, "timeout");
    assert.equal(report.evidence.requests[0].http_status, 503);
    assert.equal(report.evidence.requests[1].http_status, undefined);
    assert.equal(report.evidence.location, "AUTH_BEFORE_FETCH");
  } finally { runtime.finishAuthDiagnostics(run); }
}));

test("untracked requests and frozen responses keep their original results and errors", async () => browserGlobals(async () => {
  const runtime = await load("auth-runtime");
  const response = new Response("private body");
  const fetch = runtime.observeAuthFetch(async () => response, "transport");
  assert.equal(await fetch("https://example.test/api/v1/assets"), response);
  assert.equal(Object.hasOwn(response, "clone"), false);
  const run = runtime.beginAuthDiagnostics();
  const originalError = new TypeError("SECRET_PROVIDER_MESSAGE");
  try {
    const operation = runtime.observeAuthCheck(run, 1, "email_verification", async (context) => {
      const request = await requestFor(runtime, context);
      const fail = runtime.observeAuthFetch(async () => { throw originalError; }, "transport");
      return fail(request.url, request.requestInit);
    });
    await assert.rejects(operation, (error) => error === originalError);
    const report = runtime.createAuthDiagnostic(originalError, "email_verification", run.started, run);
    assert.equal(report.evidence.location, "AUTH_REQUEST_ERROR");
    assert.equal(report.evidence.requests[0].error_name, "TypeError");
    assert.ok(!JSON.stringify(report).includes("SECRET_PROVIDER_MESSAGE"));
  } finally { runtime.finishAuthDiagnostics(run); }
  const frozenRun = runtime.beginAuthDiagnostics();
  try {
    await runtime.observeAuthCheck(frozenRun, 1, "email_verification", async (context) => {
      const request = await requestFor(runtime, context);
      const frozen = Object.freeze(new Response('{"ok":true}'));
      const fetch = runtime.observeAuthFetch(async () => frozen, "transport");
      assert.deepEqual(await (await fetch(request.url, request.requestInit)).json(), { ok: true });
    });
    assert.equal(frozenRun.requests[0].body_observer_unavailable, true);
  } finally { runtime.finishAuthDiagnostics(frozenRun); }
}));

test("v2 nested evidence is bounded and strips secrets at every level", async () => browserGlobals(async () => {
  const runtime = await load("auth-runtime");
  const run = runtime.beginAuthDiagnostics();
  try {
    const operation = runtime.observeAuthCheck(run, 1, "session", async (context) => { await requestFor(runtime, context, "/session/refresh"); return pending(); });
    await assert.rejects(runtime.withAuthTimeout(operation, 10), /AUTH_CHECK_TIMEOUT/);
    const report = timeoutReport(runtime, run, "session");
    assert.equal(report.evidence.location, "AUTH_REFRESH_PENDING");
    report.evidence.cookie = "SECRET";
    report.evidence.environment.token = "SECRET";
    report.evidence.checks[0].message = "SECRET";
    report.evidence.requests[0].body = "SECRET";
    const parsed = runtime.parseAuthDiagnostic(report);
    assert.ok(parsed);
    assert.ok(!JSON.stringify(parsed).includes("SECRET"));
    assert.equal(runtime.parseAuthDiagnostic({ ...report, evidence: { ...report.evidence, location: "constructor" } }), null);
    assert.equal(runtime.parseAuthDiagnostic({ ...report, evidence: { ...report.evidence, requests: Array(7).fill(report.evidence.requests[0]) } }), null);
    assert.equal(runtime.parseAuthDiagnostic({ ...report, evidence: { ...report.evidence, requests: [{ ...report.evidence.requests[0], http_status: 999 }] } }), null);
    assert.ok(Buffer.byteLength(JSON.stringify(parsed)) < 8192);
  } finally { runtime.finishAuthDiagnostics(run); }
}));

test("diagnostic delivery remains independent of a stalled SDK fetch interceptor", async () => {
  const runtime = await load("auth-runtime");
  const sdkFetch = globalThis.fetch;
  let delivered;
  globalThis.fetch = () => { throw new Error("SDK fetch must not be used"); };
  try {
    runtime.configureAuthDiagnosticTransport(async (url, init) => { delivered = { url, init }; return new Response(null, { status: 202 }); });
    runtime.reportAuthDiagnostic(diagnostic);
    assert.equal(delivered.url, "/diagnostics/auth");
    assert.equal(delivered.init.credentials, "omit");
    assert.equal(delivered.init.keepalive, true);
    assert.deepEqual(JSON.parse(delivered.init.body), diagnostic);
  } finally { globalThis.fetch = sdkFetch; }
});

test("diagnostic endpoint records v2 successful response evidence without nested private fields", async () => {
  const { POST } = await load("app/diagnostics/auth/route.ts");
  const lines = [];
  const warn = console.warn;
  console.warn = (...args) => lines.push(args.join(" "));
  try {
    const evidence = { version: 2, location: "AUTH_WAIT_BODY", checks: [{ attempt: 1, phase: "email_verification", started_ms: 0, outcome: "pending", timed_out_ms: 8000, message: "SECRET" }], requests: [{ id: "req_fixture", attempt: 1, phase: "email_verification", endpoint: "email_status", prepared_ms: 1, fetch_started_ms: 2, headers_received_ms: 60, body_started_ms: 62, http_status: 200, body: "SECRET" }], environment: { online: true, visibility: "visible", service_worker_controlled: false, cookie: "SECRET" } };
    const request = new Request("http://localhost:4321/diagnostics/auth", { method: "POST", headers: { Origin: "http://localhost:4321", "Content-Type": "application/json" }, body: JSON.stringify({ ...diagnostic, evidence }) });
    assert.equal((await POST(request)).status, 202);
    assert.equal(lines.length, 1);
    assert.ok(lines[0].includes('"http_status":200'));
    assert.ok(lines[0].includes("AUTH_WAIT_BODY"));
    assert.ok(!lines[0].includes("SECRET"));
  } finally { console.warn = warn; }
});

test("diagnostics whitelist fields and reject secret-bearing URLs or arbitrary request IDs", async () => {
  const { parseAuthDiagnostic: parse } = await load("lib/auth/diagnostic-payload.ts");
  assert.deepEqual(parse({ ...diagnostic, cookie: "private", email: "private", message: "secret" }), diagnostic);
  assert.equal(parse({ ...diagnostic, path: "/assets?token=secret" }), null);
  assert.equal(parse({ ...diagnostic, request_ids: ["token=secret"] }), null);
  assert.equal(parse({ ...diagnostic, http_status: 200 }), null);
  assert.equal(parse({ ...diagnostic, elapsed_ms: -1 }), null);
});

test("auth hooks correlate requests without inspecting their bodies", async () => {
  const { boundedAuthRequest } = await load("lib/auth/request-timeout.ts");
  const context = { url: "https://example.test/api/auth/session/refresh", requestInit: { method: "POST", credentials: "include", body: "private" } };
  const result = await boundedAuthRequest(context);
  assert.equal(result.url, context.url);
  assert.match(result.requestInit.headers.get("X-Request-Id"), /^[0-9a-f-]{36}$/);
  assert.equal(result.requestInit.credentials, "include");
  assert.equal(result.requestInit.body, "private");
  assert.ok(result.requestInit.signal);
});

test("frontend diagnostic endpoint bounds, deduplicates and sanitizes anonymous reports", async () => {
  const { POST } = await load("app/diagnostics/auth/route.ts");
  const lines = [];
  const warn = console.warn;
  console.warn = (...args) => lines.push(args.join(" "));
  const request = (data, origin = "http://localhost:4321") => new Request("http://localhost:4321/diagnostics/auth", { method: "POST", headers: { Origin: origin, "Content-Type": "application/json" }, body: JSON.stringify(data) });
  try {
    const report = { ...diagnostic, cookie: "SECRET_COOKIE" };
    assert.equal((await POST(request(report))).status, 202);
    assert.equal((await POST(request(report))).status, 202);
    assert.equal(lines.length, 1);
    assert.ok(lines[0].includes(diagnostic.id));
    assert.ok(!lines[0].includes("SECRET_COOKIE"));
    assert.equal((await POST(request(report, "https://other.test"))).status, 403);
    assert.equal((await POST(request({ ...report, path: "/?token=private" }))).status, 400);
    assert.equal((await POST(request({ ...report, padding: "x".repeat(9000) }))).status, 413);
    for (let index = 0; index < 99; index++) {
      const id = `auth_12345678-1234-1234-1234-${String(index).padStart(12, "0")}`;
      assert.equal((await POST(request({ ...diagnostic, id }))).status, 202);
    }
    const limited = await POST(request({ ...diagnostic, id: "auth_ffffffff-ffff-ffff-ffff-ffffffffffff" }));
    assert.equal(limited.status, 429);
    assert.equal(limited.headers.get("Cache-Control"), "no-store");
  } finally { console.warn = warn; }
});


test("SDK-thrown HTTP responses retain their status and server request ID", async () => {
  const oldWindow = globalThis.window;
  globalThis.window = { location: { pathname: "/assets" } };
  Object.defineProperty(globalThis.navigator, "onLine", { value: true, configurable: true });
  try {
    const { createAuthDiagnostic, beginAuthDiagnostics } = await load("lib/auth/diagnostics.ts");
    beginAuthDiagnostics();
    const error = new Response(null, { status: 503, headers: { "X-Request-Id": "req_server_failure" } });
    const report = createAuthDiagnostic(error, "email_verification", performance.now());
    assert.equal(report.reason, "server");
    assert.equal(report.http_status, 503);
    assert.deepEqual(report.request_ids, ["req_server_failure"]);
  } finally {
    if (oldWindow === undefined) delete globalThis.window; else globalThis.window = oldWindow;
    delete globalThis.navigator.onLine;
  }
});
