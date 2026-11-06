import assert from "node:assert/strict";
import test from "node:test";
import { captureInvitation, clearInvitationCode, parseInvitation, readInvitationCode } from "../lib/auth/invitation.ts";

test("invitation attribution accepts only a bounded valid first-party code and timestamp", () => {
  const now = 1_800_000_000_000;
  const valid = { code: "HY123456ABCD", capturedAt: now-1000 };
  assert.equal(parseInvitation(JSON.stringify(valid), now), valid.code);
  for (const value of [null, "broken", "null", "[]", JSON.stringify({ ...valid, code: "https://evil.test" }), JSON.stringify({ ...valid, capturedAt: now+1 }), JSON.stringify({ ...valid, capturedAt: now-30*86400000 })]) {
    assert.equal(parseInvitation(value, now), undefined);
  }
});

test("the first valid invitation survives later links and clears after registration", () => {
  const oldWindow = globalThis.window;
  const values = new Map();
  globalThis.window = { localStorage: { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) } };
  try {
    captureInvitation("?invite=hy123456abcd");
    captureInvitation("?invite=HY9999999999");
    assert.equal(readInvitationCode(), "HY123456ABCD");
    clearInvitationCode();
    assert.equal(readInvitationCode(), undefined);
  } finally { globalThis.window = oldWindow; }
});

test("restricted browser storage never blocks a verified registration", () => {
  const oldWindow = globalThis.window;
  globalThis.window = { get localStorage() { throw new Error("blocked"); } };
  try {
    assert.doesNotThrow(() => captureInvitation("?invite=HY123456ABCD"));
    assert.equal(readInvitationCode(), undefined);
    assert.doesNotThrow(clearInvitationCode);
  } finally { globalThis.window = oldWindow; }
});
