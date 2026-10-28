# Commercial aversion and comfort control

Use this rule across every category whenever a screen shows dirt, contamination, bodily states, pests, damage, decay, waste, failure, fear or another potentially repellent subject. The objective is not to hide the pain. It is to make the pain immediately recognizable while preserving appetite, trust, dignity and purchase desire.

## Core principle

Use the minimum unpleasant detail needed to establish the buyer problem.

`recognizable pain + controlled intensity + clear consequence > shocking close-up`

A pain screen fails commercially when disgust, fear, injury or bodily discomfort becomes more memorable than the product need. “More realistic” is not automatically more persuasive.

## Aversion-risk classification

- `LOW`: ordinary clutter, inconvenience, light staining, mild wear, glare, tangles, awkward handling or other non-repellent friction. Direct depiction is normally safe.
- `MEDIUM`: visible grease, residue, odour implication, moderate dirt, sweat context, pests at a distance, skin/oral discomfort, damaged material or other subjects that can become unpleasant when enlarged. Use controlled crop, clean lighting and limited detail.
- `HIGH`: bodily fluids, inside-mouth wet macro, lesions, pus, blood, severe decay, dense parasites, excrement, rotting food/material, severe mould, raw injury or visually graphic contamination. Do not use photoreal extreme macro as the dominant region.

Risk is determined by rendered perception, not only by topic. A mild problem can become high-aversion through excessive magnification, glossy wet texture, brown/green discoloration, dense repetition, harsh contrast or a large canvas share.

## Sanitization strategies

Choose the least distorting strategy that keeps the pain legible:

- `DIRECT_BUT_CLEAN`: show the real state with clean light, moderate distance and restrained color.
- `LOCALIZED_HOTSPOT`: keep the whole object attractive or neutral; show the problem in a small bound inset or limited area.
- `CONTROLLED_CROP`: exclude graphic anatomy, fluid, waste or damaged edges while retaining the relevant state.
- `DISTANCED_CONTEXT`: show the consequence through action, reaction or environment instead of graphic detail.
- `BEHAVIORAL_CONSEQUENCE`: replace a graphic problem close-up with a restrained everyday behavior caused by it, such as hiding, avoiding, hesitating, interrupting or compensating.
- `SYMBOLIC_SUBSTITUTION`: use a tasteful particle, stain, odour line, warning cue or abstract object in place of a repellent literal detail.
- `CONCEPTUAL_DIAGRAM`: use a clearly illustrative, non-diagnostic diagram for microorganisms, internal processes, contamination or bodily mechanisms.
- `DESATURATED_DOCUMENTARY`: use restrained color or monochrome when documentary context matters, without turning suffering into spectacle.

## Dominance rules

- High-aversion detail is `SUPPORTING_ONLY`: it may appear as a small inset, localized hotspot, secondary panel or conceptual illustration, never as the full-canvas first visual subject.
- Medium-aversion detail may be dominant only when cleanly rendered and necessary to the purchase decision. Avoid combining extreme macro, wet/glossy texture and discoloration in the same dominant region.
- A human reaction may communicate embarrassment, discomfort or avoidance, but do not exaggerate disgust, panic, pain or social rejection.
- Keep skin, mouths, food, pets and domestic environments commercially presentable. Preserve ordinary human dignity.
- The product or the desired clean/comfortable state should recover visual attention before the viewer leaves the screen.

## Category-neutral replacements

- Replace a graphic body close-up with a normal-distance person and a restrained behavioral consequence. For example, express tooth discoloration through holding back a smile rather than a dominant wet intraoral macro.
- Replace dense pests or microbes with a few stylized conceptual objects and a visible reduction/isolation relation.
- Replace rotting or mould-covered matter with a limited affected patch, a clean comparison basis or an illustrative contamination map.
- Replace bodily fluid, excrement or waste macro with the affected context, an abstract odour/contamination cue and the handling consequence.
- Replace severe product damage with one controlled failure point plus the real inconvenience it causes.

These replacements are expression changes, not permission to strengthen efficacy or invent a result.

## Compact contract

For every module with `MEDIUM` or `HIGH` risk, add inside `expression`:

```json
{
  "commercial_comfort": {
    "aversion_risk": "HIGH",
    "discomfort_target": "LOW",
    "sanitization_strategy": "LOCALIZED_HOTSPOT",
    "detail_dominance_policy": "SUPPORTING_ONLY",
    "forbidden_visual_details": [
      "photoreal extreme macro",
      "wet bodily texture as the largest region",
      "dense discoloration or contamination"
    ],
    "replacement_expression": "normal-distance use context with one restrained bound inset"
  }
}
```

The contract is machine-side and must never appear as pixel text.

## Prompt and pixel review

Prompt for visible consequences: name the permitted distance, crop, affected-area scale, texture restraint, color restraint and replacement expression. Do not merely say “不要恶心”.

Treat as a material commercial-expression failure when a generated screen:

- makes high-aversion detail the largest or first visual subject;
- uses graphic bodily, decayed, pest, waste or contamination detail beyond the approved risk level;
- combines extreme magnification, wet texture and strong discoloration so the screen feels clinical, unhygienic or frightening;
- humiliates the affected person or uses panic, shame or social rejection as spectacle;
- remains visually repellent after promotional copy is hidden.

Do not trigger repair for a harmless mild stain, a tasteful symbolic microbe, a small localized problem inset or a restrained discomfort expression that still feels commercially usable.


