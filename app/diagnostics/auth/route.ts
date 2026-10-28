import { parseAuthDiagnostic } from "@/lib/auth/diagnostic-payload";

export const dynamic = "force-dynamic";
let windowStarted = 0;
const received = new Set<string>();

export async function POST(request: Request): Promise<Response> {
  const headers = { "Cache-Control": "no-store" };
  const origin = request.headers.get("Origin");
  const host = (request.headers.get("X-Forwarded-Host") ?? request.headers.get("Host") ?? new URL(request.url).host).split(",")[0].trim();
  try {
    if (!origin || new URL(origin).host !== host || !["http:", "https:"].includes(new URL(origin).protocol)) return new Response(null, { status: 403, headers });
  } catch {
    return new Response(null, { status: 403, headers });
  }
  if (!request.headers.get("Content-Type")?.startsWith("application/json")) return new Response(null, { status: 415, headers });
  if (Date.now() - windowStarted >= 60_000) {
    windowStarted = Date.now();
    received.clear();
  }
  if (received.size >= 100) return new Response(null, { status: 429, headers: { ...headers, "Retry-After": "60" } });
  const reader = request.body?.getReader();
  if (!reader) return new Response(null, { status: 400, headers });
  const chunks: Uint8Array[] = [];
  let length = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      length += value.byteLength;
      if (length > 8192) {
        await reader.cancel();
        return new Response(null, { status: 413, headers });
      }
      chunks.push(value);
    }
    const body = new Uint8Array(length);
    let offset = 0;
    for (const chunk of chunks) { body.set(chunk, offset); offset += chunk.byteLength; }
    const diagnostic = parseAuthDiagnostic(JSON.parse(new TextDecoder().decode(body)));
    if (!diagnostic) return new Response(null, { status: 400, headers });
    if (!received.has(diagnostic.id)) {
      received.add(diagnostic.id);
      console.warn("polaris_auth_diagnostic", JSON.stringify({ ...diagnostic, received_at: new Date().toISOString() }));
    }
    return new Response(null, { status: 202, headers });
  } catch {
    return new Response(null, { status: 400, headers });
  } finally {
    reader.releaseLock();
  }
}
