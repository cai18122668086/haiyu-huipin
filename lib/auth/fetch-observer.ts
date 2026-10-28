import { authErrorName, authRequestObservation, authTraceTime, rememberAuthRequest, type AuthRequestObservation } from "./diagnostics";

const observed = new WeakSet<Response>();
function observeBody(response: Response, observation: AuthRequestObservation): Response {
  if (observed.has(response)) return response;
  observed.add(response);
  const { context, trace } = observation;
  // Observe ONLY reads performed by the SDK itself. No clone is created here,
  // no extra stream consumer is attached, and no body content is recorded.
  try {
    const clone = response.clone;
    Object.defineProperty(response, "clone", { configurable: true, writable: true, value(this: Response) { return observeBody(clone.call(this), observation); } });
    for (const method of ["json", "text", "arrayBuffer", "blob", "formData"] as const) {
      const read: (this: Response) => Promise<unknown> = response[method];
      Object.defineProperty(response, method, { configurable: true, writable: true, value(this: Response) {
        if (!context.run.closed) trace.body_started_ms ??= authTraceTime(context.run);
        return read.call(this).then((result: unknown) => {
          if (!context.run.closed) trace.body_finished_ms = authTraceTime(context.run);
          observation.dispose?.();
          return result;
        }, (error: unknown) => {
          if (!context.run.closed) { trace.failed_ms = authTraceTime(context.run); trace.error_name = authErrorName(error); }
          observation.dispose?.();
          throw error;
        });
      } });
    }
  } catch { trace.body_observer_unavailable = true; }
  return response;
}

// Installed through the SDK's public addFetchInterceptors override. Untracked
// requests pass through without touching their responses.
export function observeAuthFetch(originalFetch: typeof fetch, layer: "transport" | "interceptor"): typeof fetch {
  return async function (this: unknown, input: RequestInfo | URL, init?: RequestInit) {
    const observation = authRequestObservation(input, init);
    if (!observation || observation.context.run.closed) return originalFetch.call(this, input, init);
    const { context, trace } = observation;
    if (layer === "transport") trace.fetch_started_ms = authTraceTime(context.run);
    try {
      const response = await originalFetch.call(this, input, init);
      if (!context.run.closed) {
        trace.http_status = response.status;
        if (layer === "transport") {
          trace.headers_received_ms = authTraceTime(context.run);
          const serverId = response.headers.get("X-Request-Id");
          if (serverId && /^[a-zA-Z0-9_-]{1,80}$/.test(serverId)) { trace.server_id = serverId; rememberAuthRequest(serverId, context.run); }
          observeBody(response, observation);
        } else trace.interceptor_finished_ms = authTraceTime(context.run);
      }
      return response;
    } catch (error) {
      if (!context.run.closed) { trace.failed_ms = authTraceTime(context.run); trace.error_name = authErrorName(error); }
      observation.dispose?.();
      throw error;
    }
  };
}
