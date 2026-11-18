import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import fs from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import test from "node:test";
import { checkUpstreamAdvisories } from "./audit-front-dependencies.mjs";

const require = createRequire(import.meta.url);
const root = path.resolve(import.meta.dirname, "..");
const hashes = {
  v2: "1461c22e818bf5f0b93536c082edbdb7c2550d94bed60aab1f924ff1029c73b8",
  v4: "e1399c0e8e7ef715ee6d4a29af978ccd307825623407daac6a865698fb621586",
};
const known = { url: "https://github.com/advisories/GHSA-fc5q-39xc-vjpf", severity: "high", vulnerable_versions: "<=4.0.7", title: "picomatch vulnerable to ReDoS through single-branch repeated extglobs" };

test("installed React server and Next.js versions match the security update", () => {
  for (const [name, version] of [["react", "19.2.9"], ["react-dom", "19.2.9"],
    ["react-server-dom-webpack", "19.2.9"], ["next", "16.4.6"]]) {
    assert.equal(require(`${name}/package.json`).version, version, name);
  }
});

for (const major of ["v2", "v4"]) {
  const location = path.join(root, "vendor", `picomatch-${major}`);
  const pm = require(location);
  test(`${major}: patched source matches reviewed upstream provenance`, () => {
    const pkg = JSON.parse(fs.readFileSync(path.join(location, "package.json")));
    const source = fs.readFileSync(path.join(location, "lib", "parse.js"));
    assert.equal(pkg.name, `@haiyu/picomatch-${major}`);
    assert.equal(createHash("sha256").update(source).digest("hex"), hashes[major]);
    assert.equal(pkg.haiyuSecurityPatch.patchedParseSha256, hashes[major]);
  });

  test(`${major}: hostile wildcard repeats finish in a bounded subprocess`, () => {
    const script = `const p=require(${JSON.stringify(location)});
      for(const pattern of ['+(*)a','+(*a)','*(a*)b','+(a*b)c','+(*[ab])','+(@(*a))','+(!(a))','+(a*|b)']) {
        for(const options of [{},{windows:true},{bash:true},{capture:true}])
          p.isMatch('a'.repeat(10000)+'!',pattern,options);
      }`;
    const child = spawnSync(process.execPath, ["--max-old-space-size=64", "-e", script], { timeout: 2500, encoding: "utf8" });
    assert.equal(child.error, undefined, child.error?.message);
    assert.equal(child.status, 0, child.stderr);
  });

  test(`${major}: normal file discovery and fixed extglobs retain behavior`, () => {
    for (const [input, pattern, expected] of [
      ["app/account/page.tsx", "**/*.{ts,tsx}", true],
      ["node_modules/a/index.js", "**/*.tsx", false],
      ["photo.jpg", "*.@(png|jpg)", true],
      ["foobar", "+(foo|bar)", true],
      ["aaa", "+(a)", true],
      ["***", "+(\\*)", true],
      ["***", "+([*])", true],
      ["assets/a.js", "!**/*.css", true],
    ]) assert.equal(pm.isMatch(input, pattern), expected, `${pattern}: ${input}`);
    assert.equal(pm.isMatch("aaaa!", "+(*a)"), false);
    assert.equal(pm.isMatch("+(*a)", "+(*a)"), true);
  });
}

test("every installed picomatch consumer resolves a reviewed compatible fork", () => {
  for (const [consumer, major] of [["micromatch", "v2"], ["tinyglobby", "v4"], ["vite", "v4"]]) {
    const resolve = createRequire(require.resolve(consumer));
    const entry = resolve.resolve("picomatch");
    const directory = path.dirname(entry);
    const pkg = JSON.parse(fs.readFileSync(path.join(directory, "package.json")));
    assert.equal(pkg.name, `@haiyu/picomatch-${major}`, consumer);
    assert.equal(createHash("sha256").update(fs.readFileSync(path.join(directory, "lib", "parse.js"))).digest("hex"), hashes[major]);
  }
  const lock = JSON.parse(fs.readFileSync(path.join(root, "package-lock.json")));
  for (const [name, pkg] of Object.entries(lock.packages)) {
    if (name.endsWith("/picomatch")) {
      assert.ok(pkg.name?.startsWith("@haiyu/picomatch-"), `Unpatched dependency: ${name}`);
      assert.ok(pkg.integrity, `Unpinned dependency: ${name}`);
    }
  }
});

test("upstream auditing accepts only the verified backport and rejects new disclosures", () => {
  assert.equal(checkUpstreamAdvisories({ picomatch: [known] }).length, 1);
  assert.deepEqual(checkUpstreamAdvisories({}), []);
  for (const report of [
    { picomatch: [{ ...known, url: "https://github.com/advisories/new-advisory" }] },
    { picomatch: [{ ...known, vulnerable_versions: "<=4.0.8" }] },
    { picomatch: [{ ...known, severity: "critical" }] },
    { picomatch: [known, { ...known, url: "new-advisory" }] },
    { other: [] }, null, [], { picomatch: "invalid" },
  ]) assert.throws(() => checkUpstreamAdvisories(report));
});
