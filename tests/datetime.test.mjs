import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { beijingDateInputToIso, formatBeijingDate, parseApiDate, toBeijingDateInput } from "../lib/datetime.mjs";

test("offsetless database UTC and offset-aware API timestamps identify the same instant", () => {
  const expected = Date.parse("2026-10-27T03:06:21.760Z");
  for (const value of ["2026-10-27T03:06:21.760102", "2026-10-27 03:06:21.760102", "2026-10-27T03:06:21.760Z", "2026-10-27T11:06:21.760+08:00"]) {
    assert.equal(parseApiDate(value).getTime(), expected);
  }
});

test("Beijing scheduling input crosses UTC dates and round-trips without adding eight hours twice", () => {
  assert.equal(toBeijingDateInput("2026-10-26T17:15:00Z"), "2026-10-27T01:15");
  assert.equal(toBeijingDateInput("2026-10-27T01:15:00+08:00"), "2026-10-27T01:15");
  assert.equal(beijingDateInputToIso("2026-10-27T01:15"), "2026-10-26T17:15:00.000Z");
  assert.equal(beijingDateInputToIso(""), null);
  assert.equal(toBeijingDateInput("invalid"), "");
  assert.equal(formatBeijingDate("invalid"), "—");
});

test("display and announcement inputs stay in Beijing on computers in UTC, Shanghai and New York", () => {
  const moduleUrl = new URL("../lib/datetime.mjs", import.meta.url).href;
  for (const TZ of ["UTC", "Asia/Shanghai", "America/New_York"]) {
    const script = `import {formatBeijingDate,toBeijingDateInput,beijingDateInputToIso} from ${JSON.stringify(moduleUrl)}; process.stdout.write(JSON.stringify([formatBeijingDate('2026-10-27T03:06:21',{year:'numeric',month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit',second:'2-digit'}),toBeijingDateInput('2026-10-26T17:15:00Z'),beijingDateInputToIso('2026-10-27T01:15')]));`;
    const result = spawnSync(process.execPath, ["--experimental-strip-types", "--input-type=module", "-e", script], { env: { ...process.env, TZ }, encoding: "utf8" });
    assert.equal(result.status, 0, result.stderr);
    assert.deepEqual(JSON.parse(result.stdout), ["2026/10/27 11:06:21", "2026-10-27T01:15", "2026-10-26T17:15:00.000Z"], TZ);
  }
});
