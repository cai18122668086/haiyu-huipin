import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const css = await readFile(new URL("../app/haiyu-readability.css", import.meta.url), "utf8");
const tokens = Object.fromEntries([...css.matchAll(/(--haiyu-[a-z-]+):\s*(#[\da-f]{6})\s*;/gi)].map(([, name, color]) => [name, color]));
function luminance(hex) {
  const channels = hex.slice(1).match(/../g).map(value => parseInt(value, 16) / 255).map(value => value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4);
  return channels[0] * .2126 + channels[1] * .7152 + channels[2] * .0722;
}

test("shared text pairs remain readable on normal, selected and disabled surfaces", () => {
  for (const [foreground, background] of [
    ["--haiyu-text-on-light", "--haiyu-surface"],
    ["--haiyu-text-muted", "--haiyu-surface"],
    ["--haiyu-text-muted", "--haiyu-surface-soft"],
    ["--haiyu-text-on-dark", "--haiyu-control-dark"],
    ["--haiyu-control-selected-text", "--haiyu-control-selected-bg"],
    ["--haiyu-control-disabled-text", "--haiyu-control-disabled-bg"],
  ]) {
    assert.ok(tokens[foreground] && tokens[background], `Missing text/surface pair: ${foreground}, ${background}`);
    const values = [luminance(tokens[foreground]), luminance(tokens[background])].sort((a, b) => a - b);
    const ratio = (values[1] + .05) / (values[0] + .05);
    assert.ok(ratio >= 4.5, `${foreground} on ${background}: ${ratio.toFixed(2)}:1; expected at least 4.5:1`);
  }
});
