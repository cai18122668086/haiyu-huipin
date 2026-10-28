# Source observation projection for native identity

The model wire protocol now supplies one ordered, source-bound observation list instead of independently generating a second identity invariant list. The adapter copies those exact texts into the native identity after validating the wire schema, then applies all existing native source, geometry, user-identity and brand checks.

The explicit `ORDERED_SOURCE_OBSERVATIONS_V1` marker authorizes this representation. Wire identity objects cannot contain an independently authored `invariant_features` list. The adapter preserves original payloads and raw model replies in the existing immutable intake journal, and binds the projection receipt to the original payload, observation list and resulting invariants by SHA-256.

Legacy replies remain unchanged and still fail on invariant/observation disagreement. Missing observations, generic preservation rules, wrong product sources, duplicate observation roles, unsupported projection markers and independently supplied invariant lists remain errors. User-supplied product name/category and explicit brandless confirmation are literal output-schema values.

Validation: 45 intake/projection/receipt cases passed, including the public planning flow from an original wire reply through the exact native approval. Mypy passed for 113 application sources after adding the schema result type annotation. No real visual acceptance is claimed by these offline checks.

For the explicit wire protocol, zero uploaded style sources canonically imply an empty reference-analysis list. An omitted/null list is represented as `[]` only in that case. Existing analyses are never removed, and a supplied style source still requires its original analysis. Original wire bytes remain in the journal; both original and canonical payload digests are retained in the projection receipt. The model output schema also declares this list explicitly.

The final intake/projection set passed 49 cases. The exact original V10 model reply passed every current intake guard during a separate offline replay with this zero-reference conversion; that replay made no supplier request and did not update its failed job.

The model schema now mirrors the adapter's remaining concrete-observation gates: every observed fact requires its source region; observed text uses the adapter's four-character floor; the wire protocol requires atomic identifiers; brand questions carry source-specific evidence; photo-only input requires authored operating analysis. No factual or identity gate was relaxed. The final intake/projection/receipt set passed 54 cases, including the public planning path.

The configured supplier explicitly rejected top-level `allOf` in `response_format`. The transport declaration now removes that single root keyword while preserving all object properties, required source fields and nested schemas. The complete conditional schema remains in the model prompt and the local validator. A regression verifies that this is the only transport projection difference. Rejection diagnostics now retain a bounded, credential-redacted native provider cause. The latest related provider/intake set passed 95 cases, and mypy passed for 113 sources.
