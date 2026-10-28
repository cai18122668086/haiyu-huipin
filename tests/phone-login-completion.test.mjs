import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";
import test from "node:test";
import { build } from "esbuild";

test("phone authentication closes the dialog only on success, including storage failure", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "huipin-login-completion-"));
  const output = path.join(directory, "auth.mjs");
  const state = { status: "OK", failCleanup: false, cleared: 0, events: [], bodies: [] };
  globalThis.__phoneLoginTest = state;
  try {
    await build({
      entryPoints: ["features/auth/api/auth.ts"], bundle: true, platform: "node", format: "esm", outfile: output, logLevel: "silent",
      plugins: [{ name: "phone-sdk-test", setup(builder) {
        builder.onResolve({ filter: /^(supertokens-web-js\/recipe\/|@\/lib\/auth\/)/ }, ({ path }) => ({ path, namespace: "mock" }));
        builder.onLoad({ filter: /.*/, namespace: "mock" }, ({ path }) => {
          if (path.endsWith("/passwordless")) return { contents: `export default {
            async consumeCode(input) { const request = await input.options.preAPIHook({url:'/consume',requestInit:{body:JSON.stringify({userInputCode:input.userInputCode})}}); globalThis.__phoneLoginTest.bodies.push(JSON.parse(request.requestInit.body)); return {status:globalThis.__phoneLoginTest.status}; },
            async clearLoginAttemptInfo() { globalThis.__phoneLoginTest.cleared++; if(globalThis.__phoneLoginTest.failCleanup) throw new Error('storage unavailable'); }
          }` };
          if (path.endsWith("/client")) return { contents: "export function initializeAuthClient() {}" };
          if (path.endsWith("/local-dev")) return { contents: "export const isLocalDevelopmentAuthEnabled=()=>false; export const LOCAL_DEV_AUTH_USER_ID='test';" };
          if (path.endsWith("/session-events")) return { contents: "export function emitAuthSessionChanged(value) { globalThis.__phoneLoginTest.events.push(value); }" };
          return { contents: "export default {};" };
        });
      } }],
    });
    const auth = await import(pathToFileURL(output).href);
    assert.equal((await auth.signInWithPhoneCode("synthetic", "login")).status, "OK");
    assert.deepEqual(state.events, [true]);
    assert.equal(state.cleared, 1);
    assert.equal(state.bodies[0].acceptedAgreements, true);
    state.status = "RESTART_FLOW_ERROR";
    await auth.signInWithPhoneCode("synthetic", "login");
    assert.deepEqual(state.events, [true]);
    assert.equal(state.cleared, 1);
    state.status = "OK"; state.failCleanup = true;
    assert.equal((await auth.signInWithPhoneCode("synthetic", "login")).status, "OK");
    assert.deepEqual(state.events, [true, true]);
    const originalFetch = globalThis.fetch;
    try {
      globalThis.fetch = async () => new Response(JSON.stringify({status:"OK"}), {status:200,headers:{"Content-Type":"application/json"}});
      await auth.signInWithPhonePassword("13800000000", "synthetic");
      assert.deepEqual(state.events, [true, true, true]);
      globalThis.fetch = async () => new Response(JSON.stringify({error:{message:"invalid"}}), {status:401,headers:{"Content-Type":"application/json"}});
      await assert.rejects(auth.signInWithPhonePassword("13800000000", "synthetic"), /invalid/);
      assert.deepEqual(state.events, [true, true, true]);
    } finally { globalThis.fetch = originalFetch; }
  } finally {
    delete globalThis.__phoneLoginTest;
    await rm(directory, {recursive:true,force:true});
  }
});
