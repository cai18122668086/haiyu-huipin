import assert from "node:assert/strict";
import { readFile, writeFile } from "node:fs/promises";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";

const root = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const defaultVinextRoot = path.join(root, "node_modules", "vinext");

export async function patchVinextProduction(vinextRoot = defaultVinextRoot) {
  const packageJsonPath = path.join(vinextRoot, "package.json");
  const packageJson = JSON.parse(await readFile(packageJsonPath, "utf8"));

  assert.equal(
    packageJson.version,
    "0.0.50",
    `Review the production static compatibility patch before using vinext ${packageJson.version}.`,
  );

  await patchFile(
    path.join(vinextRoot, "dist", "server", "static-file-cache.js"),
    [
      {
        before: 'const isHashed = relativePath.startsWith("assets/");',
        after:
          'const isHashed = relativePath.startsWith("assets/") || relativePath.startsWith("_polaris-static/");',
      },
      {
        before: 'const pathname = "/" + relativePath;',
        after:
          'const pathname = "/" + relativePath.split(path.sep).join("/");',
      },
    ],
  );

  await patchFile(
    path.join(vinextRoot, "dist", "server", "prod-server.js"),
    [
      {
        before:
          'if (pathname.startsWith("/assets/") && await tryServeStatic(req, res, clientDir, pathname, compress, staticCache)) return;',
        after:
          'if ((pathname.startsWith("/assets/") || pathname.startsWith("/_polaris-static/")) && await tryServeStatic(req, res, clientDir, pathname, compress, staticCache)) return;',
      },
    ],
  );
}

async function patchFile(filePath, replacements) {
  let source = await readFile(filePath, "utf8");
  let changed = false;

  for (const { before, after } of replacements) {
    if (source.includes(after)) continue;
    assert.ok(
      source.includes(before),
      `Expected vinext source was not found in ${path.relative(root, filePath)}.`,
    );
    source = source.replace(before, after);
    changed = true;
  }

  if (changed) await writeFile(filePath, source, "utf8");
}

if (
  process.argv[1] &&
  pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url
) {
  await patchVinextProduction();
}
