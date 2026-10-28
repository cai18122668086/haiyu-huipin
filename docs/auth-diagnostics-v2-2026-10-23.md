# Authentication diagnostic v2

This change adds evidence for recurring authentication failures. It does not
claim to fix the underlying intermittent timeout. Authentication checks,
verification decisions, redirects, 8-second limits, and the single retry with a
750-ms delay retain their existing behavior.

## Incident evidence

For diagnostic `auth_395ed195-ee94-478e-9b03-c619b6579827` on 2026-10-23,
production Caddy logs recorded both supplied request IDs at
`GET /api/auth/user/email/verify`, HTTP 200, in 63.36 and 63.76 ms. The frontend
reported an email-verification timeout after 16760 ms. This establishes that
backend processing completed promptly. It does not establish when the browser
received the response or which client stage stalled. A later browser retry
succeeded, including `/admin`; that recovery is not evidence of a permanent fix.

## Evidence and location codes

The existing copyable diagnostic ID and request IDs are retained. `evidence`
version 2 adds separate records for each check and request, including attempt,
phase, relative millisecond timestamps, successful HTTP status, known error
names, and whether the page was hidden, offline, or service-worker controlled.
The legacy top-level `http_status` stays error-only for retry compatibility;
successful codes such as 200 appear in `evidence.requests[].http_status`.

| Code | Observation |
| --- | --- |
| `AUTH_BEFORE_REQUEST` | Check started, no correlated request was prepared |
| `AUTH_BEFORE_FETCH` | Request prepared, transport not yet entered |
| `AUTH_WAIT_HEADERS` | Transport entered, response headers not yet observed |
| `AUTH_WAIT_BODY` | Headers received and body read started, no completed read |
| `AUTH_AFTER_HEADERS` | Headers received, check incomplete; no completed body read observed |
| `AUTH_AFTER_BODY` | Body read completed, check still incomplete |
| `AUTH_REFRESH_PENDING` | Refresh prepared, no response observed; its transport is not instrumented |
| `AUTH_REQUEST_ERROR` | Transport or body read rejected |
| `AUTH_HTTP_ERROR` | Correlated authentication response was HTTP 400 or higher |
| `AUTH_UNCLASSIFIED` | Insufficient evidence |

These codes describe the last observed stage, not an asserted root cause. An
absent response header timestamp cannot distinguish server/network/browser
delay without correlating the request ID with the production access log.

## Observer boundaries

The SDK's public `addFetchInterceptorsAndReturnModifiedFetch` override observes
the transport and session-interceptor return. Response objects retain their
identity. Only body reads/clones already requested by the SDK are observed;
the observer does not read, copy, log, or buffer response contents itself.
Non-correlated requests pass through. Frozen or non-extensible responses
retain normal behavior and mark body observation unavailable.

The pinned SDK's private session-refresh fetch bypasses that public override.
Refresh preparation and post-hook observations remain visible, but a missing
refresh response is explicitly labelled as unobserved rather than classified
as a proven network or lock failure. When omitted userContext would make a
refresh ambiguous between multiple pending checks, no new attribution is made.
Explicit email-check context and per-request references isolate late retries.

Reports use the original transport captured by the SDK's public hook, with
`credentials: omit`, so a stalled session interceptor does not itself block the
diagnostic POST. Delivery can still fail when the network is unavailable;
the error page's copy button includes the same evidence as a fallback.

The receiving endpoint reconstructs nested fields from strict allowlists,
accepts legacy reports, caps reports at 8 KiB, and retains origin checks,
deduplication and rate limiting. No cookies, tokens, accounts, full URLs,
request/response bodies, arbitrary exception messages or SDK objects are saved.

## Reproduction and verification

Run the auth tests with Node 22.13 or newer:

```text
node --experimental-strip-types --test tests/auth-diagnostics.test.mjs tests/auth-route-policy.test.mjs
```

For a local browser fixture using the real pinned SDK and synthetic data:

```text
node tools/auth-diagnostic-harness.mjs
```

Open its printed loopback URL. Verify the normal response passes, and the
three failure buttons produce `AUTH_WAIT_HEADERS`, `AUTH_WAIT_BODY` and
`AUTH_AFTER_BODY`. This fixture does not connect to production, require a real
account, or submit generation jobs. Its 500-ms test timeout is fixture-only.

When a new production report arrives, first use `evidence.location` and the
per-attempt timestamps to choose the stage to investigate. Correlate each
request ID with frontend and Caddy/API logs before changing authentication
behavior. A local or PR implementation must not be described as active in
production until the corresponding frontend release is verified.
