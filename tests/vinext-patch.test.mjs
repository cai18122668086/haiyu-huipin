import assert from "node:assert/strict";
import { cp, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { patchVinextProduction } from "../build/patch-vinext-production.mjs";

const installedVinext = path.resolve("node_modules", "vinext");

test("production commands apply the vinext patch without install scripts", async () => {
  const packageJson = JSON.parse(await readFile("package.json", "utf8"));
  for (const command of ["build", "start"]) {
    assert.match(
      packageJson.scripts[command],
      /^node build\/patch-vinext-production\.mjs && vinext /,
    );
  }
});

test("vinext production patch is fresh-install safe and idempotent", async () => {
  const temporaryRoot = await mkdtemp(
    path.join(os.tmpdir(), "polaris-vinext-patch-"),
  );
  const fixture = path.join(temporaryRoot, "vinext");

  try {
    await cp(installedVinext, fixture, {
      recursive: true,
      filter(source) {
        const relative = path.relative(installedVinext, source);
        return (
          relative === "" ||
          relative === "package.json" ||
          relative === "dist" ||
          relative === path.join("dist", "server") ||
          relative === path.join("dist", "server", "prod-server.js") ||
          relative === path.join("dist", "server", "static-file-cache.js")
        );
      },
    });
    await restorePristineFixture(fixture);

    await patchVinextProduction(fixture);
    const first = await snapshot(fixture);
    assert.match(first.staticCache, /relativePath\.split\(path\.sep\)/);
    assert.match(first.prodServer, /pathname\.startsWith\("\/_polaris-static\/"\)/);

    await patchVinextProduction(fixture);
    assert.deepEqual(await snapshot(fixture), first);

    const packageJsonPath = path.join(fixture, "package.json");
    const packageJson = JSON.parse(await readFile(packageJsonPath, "utf8"));
    packageJson.version = "0.0.51";
    await writeFile(packageJsonPath, `${JSON.stringify(packageJson)}\n`, "utf8");
    await assert.rejects(
      patchVinextProduction(fixture),
      /Review the production static compatibility patch/,
    );
  } finally {
    await rm(temporaryRoot, { recursive: true, force: true });
  }
});

async function restorePristineFixture(vinextRoot) {
  const staticCachePath = path.join(
    vinextRoot,
    "dist",
    "server",
    "static-file-cache.js",
  );
  const prodServerPath = path.join(
    vinextRoot,
    "dist",
    "server",
    "prod-server.js",
  );
  const staticCache = (await readFile(staticCachePath, "utf8"))
    .replace(
      'const isHashed = relativePath.startsWith("assets/") || relativePath.startsWith("_polaris-static/");',
      'const isHashed = relativePath.startsWith("assets/");',
    )
    .replace(
      'const pathname = "/" + relativePath.split(path.sep).join("/");',
      'const pathname = "/" + relativePath;',
    );
  const prodServer = (await readFile(prodServerPath, "utf8")).replace(
    'if ((pathname.startsWith("/assets/") || pathname.startsWith("/_polaris-static/")) && await tryServeStatic(req, res, clientDir, pathname, compress, staticCache)) return;',
    'if (pathname.startsWith("/assets/") && await tryServeStatic(req, res, clientDir, pathname, compress, staticCache)) return;',
  );
  await writeFile(staticCachePath, staticCache, "utf8");
  await writeFile(prodServerPath, prodServer, "utf8");
}

async function snapshot(vinextRoot) {
  return {
    staticCache: await readFile(
      path.join(vinextRoot, "dist", "server", "static-file-cache.js"),
      "utf8",
    ),
    prodServer: await readFile(
      path.join(vinextRoot, "dist", "server", "prod-server.js"),
      "utf8",
    ),
  };
}
