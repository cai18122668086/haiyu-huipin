import { mkdir, readdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import sharp from "sharp";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const source = path.join(root, "public/images/homepage-features/native-v2");
const target = path.join(root, "public/images/homepage-features/native-v2-webp");
await mkdir(target, { recursive: true });
let originalBytes = 0;
let encodedBytes = 0;
for (const name of await readdir(source)) {
  if (!name.endsWith(".png")) continue;
  const original = await readFile(path.join(source, name));
  const encoded = await sharp(original).webp({ quality: 88, effort: 5 }).toBuffer();
  await writeFile(path.join(target, name.replace(/\.png$/, ".webp")), encoded);
  originalBytes += original.length;
  encodedBytes += encoded.length;
}
const inspirationTarget = path.join(root, "public/images/haiyu");
await mkdir(inspirationTarget, { recursive: true });
for (const [original, output] of [["focus-01-result.png", "outdoor.webp"], ["focus-03-result.jpg", "fashion.webp"]]) {
  await sharp(path.join(root, "public/images/inspiration", original)).resize({ width: 960, withoutEnlargement: true }).webp({ quality: 88 }).toFile(path.join(inspirationTarget, output));
}
await sharp(path.join(root, "public/haiyu-social.svg")).png().toFile(path.join(root, "public/og.png"));
console.log(`Homepage images: ${Math.round(originalBytes / 1024)} KiB → ${Math.round(encodedBytes / 1024)} KiB. Original artwork retained.`);
