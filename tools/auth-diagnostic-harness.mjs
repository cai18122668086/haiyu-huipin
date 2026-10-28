// Synthetic local fixture; does not connect to production or collect credentials.
import { createServer } from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";

const root = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const result = await build({
  absWorkingDir: root, entryPoints: ["tests/fixtures/auth-diagnostics-browser.ts"],
  bundle: true, platform: "browser", format: "esm", write: false,
  define: { "process.env.NODE_ENV": '"production"' }, logLevel: "silent",
});
const html = `<!doctype html><html lang="en"><meta charset="UTF-8"><title>Auth diagnostic browser fixture</title>
<style>body{font:16px system-ui;background:#f5f4ff;color:#24213e;margin:40px;max-width:1050px}button{padding:12px 18px;margin:0 10px 10px 0;border:1px solid #c5bfe8;border-radius:8px;background:white;cursor:pointer}output{display:block;padding:18px;background:#e4ddff;border-radius:8px;font-weight:600}pre{background:white;border-radius:10px;padding:20px;white-space:pre-wrap;font:13px monospace}p{color:#645d7c}</style>
<h1>Authentication diagnostic v2</h1><p>Local synthetic test • real SuperTokens SDK • no production requests or credentials</p>
<div><button data-mode="normal">Normal response</button><button data-mode="headers">No response headers</button><button data-mode="body">200 / body stalls</button><button data-mode="sdk">Body complete / check stalls</button></div>
<output id="status">Ready</output><pre id="report"></pre><script type="module" src="/bundle.js"></script></html>`;
const server = createServer((request, response) => {
  if (request.url === "/bundle.js") { response.writeHead(200, { "Content-Type": "text/javascript" }); response.end(result.outputFiles[0].contents); return; }
  if (!request.url?.startsWith("/fixture")) { response.writeHead(404); response.end(); return; }
  const frontToken = Buffer.from(JSON.stringify({ uid: "synthetic-fixture", ate: Date.now() + 3600_000, up: { "st-ev": { v: true, t: Date.now() } } })).toString("base64");
  response.writeHead(200, { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store", "Set-Cookie": [`sFrontToken=${frontToken}; Path=/fixture; SameSite=Lax`, `st-last-access-token-update=${Date.now()}; Path=/fixture; SameSite=Lax`] });
  response.end(html);
});
server.listen(0, "127.0.0.1", () => console.log(`Auth fixture: http://127.0.0.1:${server.address().port}/fixture`));
