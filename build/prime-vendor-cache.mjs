import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { readFileSync, realpathSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

export function verifiedArchives(root) {
  const lock = JSON.parse(readFileSync(path.join(root, "package-lock.json")));
  const archives = new Map();
  for (const pkg of Object.values(lock.packages)) {
    if (!pkg.name?.startsWith("@haiyu/picomatch-")) continue;
    assert.match(pkg.name, /^@haiyu\/picomatch-v[24]$/);
    const filename = `${pkg.name.slice("@haiyu/".length)}.tgz`;
    const url = new URL(pkg.resolved);
    assert.equal(url.origin, "https://raw.githubusercontent.com");
    assert.match(url.pathname, new RegExp(`^/cai18122668086/haiyu-huipin/[a-f0-9]{40}/vendor/${filename.replace(".", "\\.")}$`));
    assert.equal(url.username + url.password + url.search + url.hash, "");
    const data = readFileSync(path.join(root, "vendor", filename));
    assert.equal(`sha512-${createHash("sha512").update(data).digest("base64")}`, pkg.integrity, `Vendor integrity mismatch: ${filename}`);
    archives.set(url.href, { data, integrity: pkg.integrity });
  }
  assert.equal(archives.size, 2, "Both reviewed fork archives must be present");
  return archives;
}

export async function primeVendorCache(root = process.cwd(), cacheRoot) {
  const npmEntry = process.env.npm_execpath ?? (process.platform === "win32"
    ? path.join(path.dirname(process.execPath), "node_modules/npm/bin/npm-cli.js")
    : path.resolve(path.dirname(process.execPath), "../lib/node_modules/npm/bin/npm-cli.js"));
  const npmRequire = createRequire(npmEntry);
  const cacache = npmRequire("cacache");
  const pacote = npmRequire("pacote");
  cacheRoot ??= execFileSync(process.execPath, [npmEntry, "config", "get", "cache"], { encoding: "utf8" }).trim();
  const cache = path.join(cacheRoot, "_cacache");
  const archives = verifiedArchives(root);
  for (const [url, { data, integrity }] of archives) {
    // Cache exactly the locked public bytes, never alter lockfile URLs or SRI.
    await cacache.put(cache, `make-fetch-happen:request-cache:${url}`, data, {
      metadata: { time: Date.now(), url, reqHeaders: {},
        resHeaders: { "cache-control": "public, max-age=31536000", "content-type": "application/octet-stream", date: new Date().toUTCString() },
        options: { compress: true } },
    });
    // Fail immediately if this npm version's cache format is incompatible.
    const offline = await pacote.tarball(url, { cache, integrity, offline: true });
    assert.ok(Buffer.from(offline).equals(data), "Offline archive differs from the reviewed bytes");
  }
  return { verified_archives: archives.size, offline_verified: true };
}

if (process.argv[1] && realpathSync(process.argv[1]) === realpathSync(fileURLToPath(import.meta.url))) {
  console.log(JSON.stringify(await primeVendorCache()));
}
