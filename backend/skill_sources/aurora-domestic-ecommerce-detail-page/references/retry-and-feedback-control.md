# Retry and feedback control

## Revision-cycle budget

A module revision cycle has exactly two possible render slots:

1. initial render;
2. one automatic repair that edits the initial render.

After the second failed audit, do not call image generation again. Bind attempt two as `COMPLETE_WITH_RESIDUALS`, keep every material failure visible in the module record and final manifest, and automatically advance to the next unfinished module. This is a continuity rule, not an audit relaxation: the screen remains explicitly non-passing.

Do not disguise a third automatic attempt as a user adjustment, prompt rewrite, fresh generation, alternate tool call, context restart, or audit relaxation. Only an explicit user instruction naming a change may open a new revision cycle. The new cycle keeps the old cycle ID, attempts, diagnoses, and residuals in revision history and receives its own two-slot ceiling.

## Feedback scope

Store only true campaign invariants globally:

- canonical product identity;
- approved factual claims and forbidden inventions;
- series visual DNA and independent-logo policy;
- approved page story and reference-use policy.

Store screen-specific requests under the named module:

- move this screen's title beside the product;
- reduce this screen's numeric block;
- keep this screen's successful texture treatment;
- do not repeat this screen's comparison frame.

The prompt compiler includes global constraints plus only the current module's local constraints. It must never import another module's placement, crop, wording, or repair diagnosis.

## Audit integrity

The second audit uses the same material-failure standard as the first. Do not relabel a still-failing screen as `PASS`; close it as `COMPLETE_WITH_RESIDUALS`, report the failed contract fields, and continue sequential generation. A later explicit local correction may reopen only that named module in a fresh two-slot revision cycle.

Do not open the repair slot for a short, non-claim pain-scene caption or for harmless variation in a clearly conceptual microorganism/stain symbol. Record it as advisory unless it changes a fact, strengthens efficacy, impersonates laboratory evidence, damages comprehension, or violates another material gate. This is a proportional threshold, not blanket permission for extra copy or fabricated proof.


