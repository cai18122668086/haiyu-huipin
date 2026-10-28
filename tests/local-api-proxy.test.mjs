import assert from "node:assert/strict";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import test from "node:test";
import { localApiProxyTarget } from "../build/local-api-proxy.mjs";

test("editing the development proxy replaces a target cached in the long-running frontend process", (t) => {
  const prefix = join(tmpdir(), "haiyu-proxy-test-");
  const root = mkdtempSync(prefix);
  t.after(() => {
    assert.ok(resolve(root).startsWith(resolve(prefix)));
    rmSync(root, { recursive: true });
  });
  const cached = { VITE_API_PROXY_TARGET: "http://127.0.0.1:18822" };
  writeFileSync(join(root, ".env.local"), 'VITE_API_PROXY_TARGET="http://127.0.0.1:18825"\n');
  assert.equal(localApiProxyTarget("development", root, cached), "http://127.0.0.1:18825");
  writeFileSync(join(root, ".env.local"), "VITE_API_PROXY_TARGET=http://127.0.0.1:18826\n");
  assert.equal(localApiProxyTarget("development", root, cached), "http://127.0.0.1:18826");
  writeFileSync(join(root, ".env.development.local"), "VITE_API_PROXY_TARGET=http://127.0.0.1:18827\n");
  assert.equal(localApiProxyTarget("development", root, cached), "http://127.0.0.1:18827");
  assert.equal(localApiProxyTarget("production", root, cached), "http://127.0.0.1:18822");
  assert.equal(localApiProxyTarget("production", root, {}), "http://127.0.0.1:8000");
});
