import assert from "node:assert/strict";
import test from "node:test";
import { creditUnitPrice } from "../lib/credit-unit-price.ts";
import { beijingPromotionDay, welcomePromptEligible } from "../lib/welcome-prompt-policy.ts";

test("unit prices use five credits and all twelve annual grants", () => {
  const price = (amount_fen, credits, months = 1, kind = "membership") => creditUnitPrice({ amount_fen, credits, months, kind });
  assert.equal(price(990, 240, 0, "pack"), "0.21");
  assert.equal(price(4900, 1000), "0.25");
  assert.equal(price(9900, 2400), "0.21");
  assert.equal(price(29900, 8000), "0.19");
  assert.equal(price(46800, 1000, 12), "0.20");
  assert.equal(price(94800, 2400, 12), "0.16");
  assert.equal(price(286800, 8000, 12), "0.15");
  assert.equal(price(79900, 24000), "0.17");
  assert.equal(price(766800, 24000, 12), "0.13");
  assert.equal(price(4900, 800, 0, "pack"), "0.31");
  assert.equal(price(9900, 1800, 0, "pack"), "0.28");
  assert.equal(price(29900, 6000, 0, "pack"), "0.25");
  assert.equal(price(79900, 18000, 0, "pack"), "0.22");
  assert.equal(price(990, 0), "—");
});

test("free welcome only targets guests or unused trials on the homepage", () => {
  const base = { home: true, enabled: true, authenticated: true, status: "available" };
  assert.equal(welcomePromptEligible(base), true);
  assert.equal(welcomePromptEligible({ ...base, authenticated: false, status: undefined }), true);
  for (const change of [
    { home: false }, { enabled: false }, { authenticated: undefined },
    { status: "in_progress" }, { status: "recoverable" }, { status: "completed" }, { status: "expired" },
  ]) assert.equal(welcomePromptEligible({ ...base, ...change }), false);
});

test("daily popup frequency follows Beijing midnight", () => {
  assert.equal(beijingPromotionDay(new Date("2026-11-09T15:59:59Z")), "2026-11-09");
  assert.equal(beijingPromotionDay(new Date("2026-11-09T16:00:00Z")), "2026-11-10");
});
