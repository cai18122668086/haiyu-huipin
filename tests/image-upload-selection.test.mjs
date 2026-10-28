import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";
import test from "node:test";
import { build } from "esbuild";

async function loadContract() {
  const directory = await mkdtemp(path.join(os.tmpdir(), "polaris-image-drop-"));
  try {
    const output = path.join(directory, "contract.mjs");
    await build({ entryPoints: ["lib/image-upload.ts"], bundle: true, format: "esm", platform: "node", outfile: output, logLevel: "silent" });
    return await import(pathToFileURL(output).href);
  } finally { await rm(directory, { recursive: true, force: true }); }
}

const contract = await loadContract();
const picture = (name = "product.png", type = "image/png") => new File(["unchanged input bytes"], name, { type, lastModified: 1234 });

test("picker and drop validation preserve File identities, order, bytes and names", async () => {
  const files = [picture("商品.JPG", "image/jpeg"), picture("参考.webp", "image/webp")];
  const result = contract.validateImageUpload(files, { multiple: true, maxFiles: 8 });
  assert.equal(result.error, undefined);
  assert.equal(result.files.length, 2);
  assert.equal(result.files[0], files[0]);
  assert.equal(result.files[1], files[1]);
  assert.equal(result.files[0].lastModified, 1234);
  assert.equal(await result.files[0].text(), "unchanged input bytes");
});

test("single-image roles reject multiple files before accepting any input", () => {
  const result = contract.validateImageUpload([picture(), picture("second.png")], { multiple: false, maxFiles: 8 });
  assert.deepEqual(result.files, []);
  assert.match(result.error, /1/);
  assert.equal(contract.imageUploadLimit(false, 8), 1);
});

test("product roles enforce remaining capacity without silently truncating a batch", () => {
  const files = Array.from({ length: 4 }, (_, index) => picture(`${index}.png`));
  assert.equal(contract.validateImageUpload(files.slice(0, 3), { multiple: true, maxFiles: 3 }).files.length, 3);
  assert.equal(contract.validateImageUpload(files, { multiple: true, maxFiles: 3 }).files.length, 0);
  assert.ok(contract.validateImageUpload(files, { multiple: true, maxFiles: 3 }).error);
  assert.equal(contract.validateImageUpload([picture()], { multiple: true, maxFiles: 0 }).files.length, 0);
});

test("mixed unsupported batches are rejected before uploading or resetting an existing source", () => {
  for (const invalid of [picture("vector.svg", "image/svg+xml"), picture("manual.pdf", "application/pdf"), picture("fake.jpg", "text/plain"), picture("code.exe", "image/png")]) {
    const result = contract.validateImageUpload([picture(), invalid], { multiple: true });
    assert.deepEqual(result.files, []);
    assert.ok(result.error.includes(invalid.name));
  }
});

test("OS drops with missing MIME metadata retain supported raster extensions", () => {
  assert.equal(contract.validateImageUpload([picture("PHOTO.JPEG", "")]).files.length, 1);
  assert.equal(contract.validateImageUpload([picture("photo.png", "application/octet-stream")]).files.length, 1);
  assert.equal(contract.validateImageUpload([picture("clipboard-image", "image/png")]).files.length, 1);
  assert.equal(contract.validateImageUpload([picture("unknown", "")]).files.length, 0);
});

test("text and URL drags do not become file uploads", () => {
  assert.equal(contract.isImageFileDrag({ types: ["text/plain", "text/uri-list"], items: [{ kind: "string" }] }), false);
  assert.equal(contract.isImageFileDrag({ types: ["Files"], items: [] }), true);
  assert.equal(contract.isImageFileDrag({ types: [], items: [{ kind: "file" }] }), true);
  assert.deepEqual(contract.validateImageUpload([]), { files: [] });
});
