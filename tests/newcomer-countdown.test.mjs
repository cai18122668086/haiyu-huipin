import assert from "node:assert/strict";
import test from "node:test";
import { newcomerCountdown } from "../lib/newcomer-countdown.ts";

test("newcomer countdown uses server deadline and ticks each second without resetting",()=>{
  const end="2026-11-07T06:00:00Z",now="2026-11-06T06:00:00Z";
  assert.deepEqual(newcomerCountdown(end,now,0).parts,["24","00","00"]);
  assert.deepEqual(newcomerCountdown(end,now,1000).parts,["23","59","59"]);
  assert.deepEqual(newcomerCountdown(end,now,61000).parts,["23","58","59"]);
  assert.equal(newcomerCountdown(end,now,86_400_001).seconds,0);
  assert.equal(newcomerCountdown(null,now,0).seconds,0);
  assert.equal(newcomerCountdown("invalid",now,0).seconds,0);
});
