# Image content refusal handling

A relay can wrap an actual content-policy refusal as `upstream_text_reply`.
Explicit guardrail or third-party-content similarity refusals are terminal
content decisions, rather than technical route failures. They must not dispatch
the same request to the configured backup or enter automatic retry. Ordinary
non-image replies and supported route failures retain their existing fallback.

Image and masked-edit gateways record `IMAGE_CONTENT_REJECTED`, retain the
redacted supplier receipt and return a fixed user-facing explanation. The single
image adapter preserves this reason, and terminal job summaries carry the first
failed item receipt unless a more specific job error already exists.

The Inspiration composer reads the same safe failure summary as task history.
Historical generic `IMAGE_REQUEST_FAILED` records with a persisted content-policy
receipt display their actual category without rewriting the original history.
An uncertain generation remains uncertain and cannot be presented as a confirmed
refusal without an explicit receipt.

This change does not modify product images or user instructions, bypass supplier
content decisions, or claim that a refused request has delivered an image. A
future edit should retain the user's actual scope; a background-only request is
not authorization to replace the product or invent a different campaign.
