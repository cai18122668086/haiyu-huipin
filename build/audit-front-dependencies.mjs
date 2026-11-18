import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { realpathSync } from "node:fs";
import { fileURLToPath } from "node:url";

const patchedUrl = "https://github.com/advisories/GHSA-fc5q-39xc-vjpf";

export function checkUpstreamAdvisories(report) {
  assert.ok(report && typeof report === "object" && !Array.isArray(report));
  assert.deepEqual(Object.keys(report).filter((key) => key !== "picomatch"), []);
  const advisories = report.picomatch ?? [];
  assert.ok(Array.isArray(advisories));
  for (const advisory of advisories) {
    assert.ok(
      advisory.url === patchedUrl && advisory.severity === "high" &&
      advisory.vulnerable_versions === "<=4.0.7" &&
      advisory.title === "picomatch vulnerable to ReDoS through single-branch repeated extglobs",
      `Unreviewed upstream picomatch advisory: ${advisory.url}`,
    );
  }
  return advisories;
}

async function audit() {
  // Verify the installed fork bytes and hostile-input deadlines before accepting
  // the single backported advisory. All other npm audits keep their existing gate.
  const tests = spawnSync(process.execPath, ["--test", "build/dependency-security.test.mjs"], { stdio: "inherit" });
  assert.equal(tests.status, 0, "Installed dependency security verification failed");
  assert.ok(process.env.npm_execpath, "Run through npm run audit:security");
  const result = spawnSync(process.execPath, [process.env.npm_execpath, "audit", "--audit-level=high"], { stdio: "inherit" });
  assert.equal(result.status, 0, "npm dependency audit failed");
  // A private fork has no public registry advisories of its own. Keep auditing
  // its actual upstream versions so future disclosures cannot disappear by alias.
  const response = await fetch("https://registry.npmjs.org/-/npm/v1/security/advisories/bulk", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ picomatch: ["2.3.2", "4.0.7"] }),
    signal: AbortSignal.timeout(30_000),
  });
  assert.ok(response.ok, `Upstream advisory query failed: ${response.status}`);
  const patched = checkUpstreamAdvisories(await response.json());
  console.log(JSON.stringify({ registry_audit: "passed", upstream: "picomatch@2.3.2,4.0.7", locally_patched: patched.map((entry) => entry.url), unreviewed_upstream_advisories: 0 }));
}

if (process.argv[1] && realpathSync(process.argv[1]) === realpathSync(fileURLToPath(import.meta.url))) {
  await audit();
}
