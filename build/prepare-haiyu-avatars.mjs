import { mkdir, readFile } from "node:fs/promises";
import path from "node:path";
import sharp from "sharp";

// Accept a local generated-image manifest; source files remain untouched.
const manifest = JSON.parse(await readFile(process.argv[2], "utf8"));
const output = path.resolve("public/images/haiyu/avatars");
await mkdir(output, { recursive: true });
for (const { name, path: source } of manifest) {
  await sharp(source).resize(160, 160, { fit: "cover" }).webp({ quality: 90 }).toFile(path.join(output, `${name}.webp`));
}
console.log(`Saved ${manifest.length} illustrated avatars to ${output}`);
