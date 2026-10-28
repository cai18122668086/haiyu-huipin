import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { parseEnv } from "node:util";

export function localApiProxyTarget(mode, root, env) {
  // Vinext retains loaded VITE_* values in process.env across a Vite restart.
  // The operator's local preview target must not stay stuck on the old process.
  if (mode === "development") {
    for (const name of [`.env.${mode}.local`, ".env.local"]) {
      const path = resolve(root, name);
      if (!existsSync(path)) continue;
      const target = parseEnv(readFileSync(path, "utf8")).VITE_API_PROXY_TARGET?.trim();
      if (target) return target;
    }
  }
  return env.VITE_API_PROXY_TARGET?.trim() || "http://127.0.0.1:8000";
}
