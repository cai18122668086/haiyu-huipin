// Local-only browser fixture: real pinned SDK, synthetic session, fake transport.
import { initializeAuthClient } from "../../lib/auth/client";
import { isEmailVerified, sessionExists } from "../../features/auth/api/auth";
import { beginAuthDiagnostics, createAuthDiagnostic, finishAuthDiagnostics, observeAuthCheck } from "../../lib/auth/diagnostics";
import { parseAuthDiagnostic } from "../../lib/auth/diagnostic-payload";
import { withAuthTimeout } from "../../lib/auth/request-timeout";
import type { AuthPhase } from "../../lib/auth/diagnostic-payload";

let mode = "normal";
const nativeFetch = window.fetch.bind(window);
window.fetch = async (input, init) => {
  const url = new URL(input instanceof Request ? input.url : String(input), window.location.origin);
  if (url.pathname !== "/api/auth/user/email/verify") return nativeFetch(input, init);
  const requestId = new Headers(init?.headers).get("X-Request-Id") ?? "fixture";
  const headers = { "Content-Type": "application/json", "X-Request-Id": requestId };
  if (mode === "headers") return new Promise(() => {});
  if (mode === "body") return new Response(new ReadableStream({ start(controller) { controller.enqueue(new TextEncoder().encode('{"status":"OK","isVerified":true}')); } }), { status: 200, headers });
  return Response.json({ status: "OK", isVerified: true }, { headers });
};
initializeAuthClient();

const status = document.querySelector<HTMLOutputElement>("#status")!;
const output = document.querySelector<HTMLPreElement>("#report")!;
for (const button of document.querySelectorAll<HTMLButtonElement>("button[data-mode]")) {
  button.addEventListener("click", async () => {
    mode = button.dataset.mode!;
    const run = beginAuthDiagnostics();
    let phase: AuthPhase = "session";
    status.textContent = "Checking…";
    output.textContent = "";
    try {
      if (!await withAuthTimeout(observeAuthCheck(run, 1, phase, sessionExists), 500)) throw new Error("FIXTURE_SESSION_MISSING");
      phase = "email_verification";
      const verified = await withAuthTimeout(observeAuthCheck(run, 1, phase, async (context) => {
        const result = await isEmailVerified(context);
        if (mode === "sdk") await new Promise(() => {});
        return result;
      }), 500);
      status.textContent = verified ? "PASS — normal SDK authentication unchanged" : "FAIL — unexpected verification result";
      output.textContent = JSON.stringify({ checks: run.checks, requests: run.requests }, null, 2);
    } catch (error) {
      const report = createAuthDiagnostic(error, phase, run.started, run);
      status.textContent = parseAuthDiagnostic(report) ? report.evidence!.location : "FAIL — diagnostic rejected by parser";
      output.textContent = JSON.stringify(report, null, 2);
    } finally { finishAuthDiagnostics(run); }
  });
}
