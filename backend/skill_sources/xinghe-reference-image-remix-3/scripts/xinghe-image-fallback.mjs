#!/usr/bin/env node

import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { basename, join, parse, resolve } from "node:path";

function readArg(name, fallback = undefined) {
  const prefix = `--${name}=`;
  const hit = process.argv.find((arg) => arg.startsWith(prefix));
  return hit ? hit.slice(prefix.length) : fallback;
}

function readRepeatedArg(name) {
  const prefix = `--${name}=`;
  const values = [];
  for (let i = 0; i < process.argv.length; i += 1) {
    const arg = process.argv[i];
    if (arg.startsWith(prefix)) values.push(arg.slice(prefix.length));
    else if (arg === `--${name}` && process.argv[i + 1]) {
      values.push(process.argv[i + 1]);
      i += 1;
    }
  }
  return values;
}

function usage() {
  console.error(`Usage: node scripts/xinghe-image-fallback.mjs --prompt="..." [--image="C:/path/product.png"] [--size=1024x1024] [--out=./outputs] [--file=image-01.png]`);
}

const prompt = readArg("prompt");
const size = readArg("size", "1024x1024");
const outputDir = resolve(readArg("out", join(process.cwd(), "outputs", "xinghe-reference-clone")));
const fileName = readArg("file", readArg("filename", "image-01.png"));
const images = [...readRepeatedArg("image"), ...readRepeatedArg("reference-image"), ...readRepeatedArg("reference")].filter(Boolean);
const helper = process.env.XINGHE_IMAGE_GENERATOR || join(process.env.LOCALAPPDATA || "", "ApiCodexOneClick", "tools", "generate-image.ps1");

if (!prompt) {
  usage();
  process.exit(2);
}
if (!helper || !existsSync(helper)) {
  console.error(`Missing deployment helper: ${helper}`);
  process.exit(1);
}

mkdirSync(outputDir, { recursive: true });
const promptBase = parse(basename(fileName)).name || "image";
const promptFile = join(outputDir, `${promptBase}.prompt.txt`);
writeFileSync(promptFile, prompt, "utf8");

const args = ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", helper, "-PromptFile", promptFile, "-OutputDir", outputDir, "-Size", size, "-FileName", fileName];
if (images.length > 0) {
  args.push("-ReferenceImage", ...images);
}

const result = spawnSync("powershell.exe", args, { stdio: "inherit", windowsHide: true });
process.exit(result.status ?? 1);
