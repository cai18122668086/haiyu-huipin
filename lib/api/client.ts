import createClient, { type Middleware } from "openapi-fetch";

import { AUTH_API_BASE_PATH } from "@/lib/auth/config";
import { isLocalDevelopmentAuthEnabled, LOCAL_DEV_AUTH_USER_ID } from "@/lib/auth/local-dev";
import { emitAuthUnauthorised } from "@/lib/auth/session-events";

import type { paths } from "./generated/openapi";

// Browser requests default to the current origin. Internal and production deployments
// route /api through the edge proxy; local development may opt into an explicit URL.
const viteEnv = (import.meta as ImportMeta & { env?: Record<string, string | undefined> }).env;
const baseUrl =
  (typeof process !== "undefined" ? process.env.NEXT_PUBLIC_API_BASE_URL?.trim() || "" : "") ||
  viteEnv?.VITE_API_BASE_URL?.trim() ||
  "";

// openapi-fetch captures its fetch implementation when the client is created.
// Resolve the global implementation at request time so SuperTokens can install
// its session-refresh and anti-CSRF interceptor before protected API calls run.
const runtimeFetch: typeof globalThis.fetch = (request) => globalThis.fetch(request);

const requestMetadata: Middleware = {
  async onRequest({ request }) {
    const headers = new Headers(request.headers);
    headers.set("X-Request-Id", crypto.randomUUID());
    if (isLocalDevelopmentAuthEnabled()) {
      headers.set("X-User-Id", LOCAL_DEV_AUTH_USER_ID);
    }
    return new Request(request, {
      credentials: isLocalDevelopmentAuthEnabled() ? "omit" : "include",
      headers,
    });
  },
};

const authenticationFailures: Middleware = {
  onResponse({ request, response }) {
    if (response.status !== 401) {
      return response;
    }

    const requestUrl = new URL(request.url, "http://localhost");
    const isAuthEndpoint =
      requestUrl.pathname === AUTH_API_BASE_PATH ||
      requestUrl.pathname.startsWith(`${AUTH_API_BASE_PATH}/`);

    // SuperTokens' fetch interception first gets a chance to refresh the session.
    // A 401 reaching this middleware is therefore the final unauthorised response.
    if (!isAuthEndpoint) {
      emitAuthUnauthorised("api-401");
    }

    return response;
  },
};

export const apiClient = createClient<paths>({
  baseUrl,
  credentials: "include",
  fetch: runtimeFetch,
});
apiClient.use(requestMetadata, authenticationFailures);
