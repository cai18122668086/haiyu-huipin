import SuperTokens from "supertokens-web-js";
import EmailVerification from "supertokens-web-js/recipe/emailverification";
import Session from "supertokens-web-js/recipe/session";
import Passwordless from "supertokens-web-js/recipe/passwordless";

import { emitAuthSessionChanged, emitAuthUnauthorised } from "./session-events";
import { isLocalDevelopmentAuthEnabled } from "./local-dev";
import { getAuthRuntimeConfig } from "./config";
import { authRequestInit, boundedAuthRequest } from "./request-timeout";
import { configureAuthDiagnosticTransport, observeAuthResponse } from "./diagnostics";
import { observeAuthFetch } from "./fetch-observer";

let authClientInitialized = false;

export function initializeAuthClient(): void {
  if (authClientInitialized || typeof window === "undefined") return;
  // The local development backend intentionally uses a fixed development
  // identity instead of SuperTokens. Never enable this flag in production.
  if (isLocalDevelopmentAuthEnabled()) {
    authClientInitialized = true;
    return;
  }
  const config = getAuthRuntimeConfig();
  SuperTokens.init({
    appInfo: { appName: config.appName, apiDomain: config.apiDomain, apiBasePath: config.apiBasePath },
    recipeList: [
      Passwordless.init({
        preAPIHook: async ({ url, requestInit }) => ({ url, requestInit: authRequestInit(requestInit, 20_000) }),
        postAPIHook: observeAuthResponse,
      }),
      EmailVerification.init({ preAPIHook: boundedAuthRequest, postAPIHook: observeAuthResponse }),
      Session.init({
        preAPIHook: boundedAuthRequest, postAPIHook: observeAuthResponse,
        override: {
          functions: (original) => ({
            ...original,
            addFetchInterceptorsAndReturnModifiedFetch: (input) => {
              configureAuthDiagnosticTransport(input.originalFetch);
              return observeAuthFetch(
                original.addFetchInterceptorsAndReturnModifiedFetch({ ...input, originalFetch: observeAuthFetch(input.originalFetch, "transport") }),
                "interceptor",
              );
            },
          }),
        },
        onHandleEvent(event) {
          if (event.action === "UNAUTHORISED") emitAuthUnauthorised("session");
          else if (event.action === "SIGN_OUT") emitAuthUnauthorised("sign-out");
          if (event.action === "SESSION_CREATED") emitAuthSessionChanged(true);
          else if (event.action === "SIGN_OUT" || event.action === "UNAUTHORISED") emitAuthSessionChanged(false);
        },
      }),
    ],
  });
  authClientInitialized = true;
}

export function isAuthClientInitialized(): boolean {
  return authClientInitialized;
}
