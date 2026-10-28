# Automatic benchmark screen segmentation

The Agent performs classification, semantic boundary selection, cropping and crop inspection.

## Columns are containers, screens are units

- Exclude presentation headers, watermarks, account IDs, copyright and decorative outer backgrounds.
- Detect independent columns from gutters, background changes and reading flows, but do not emit a column as a unit merely because it is visually isolated.
- Count the screens inside each column before cropping. A screen is one viewport-level composition or bounded sales panel with its own dominant headline/subject and commercial duty.
- Split within every column at genuine screen transitions: a new dominant headline, background or carrier reset, new hero subject, full-width divider, numbered chapter start, or change in buyer question. Keep a headline with the proof/action/scene it governs.
- Preserve complete evidence bands, comparisons, exploded assemblies, reports and their dependent copy.
- Slight crop overlap is acceptable when needed to keep one commercial relationship intact.
- A tall column containing multiple headline-led compositions is multiple screens. “One visible column” is not evidence for “one screen.”

## Screen-count gate

Before rectangles are finalized, write `column_screen_counts` for every detected column and explain each unit's `screen_boundary_reason`. Re-open the source and recount without reading the proposed rectangles. If the fresh count differs, correct the manifest before cropping.

The following is a hard failure: a column visibly contains several chapter starts, hero subjects or carrier resets, but the manifest gives the column one unit whose rectangle spans all of them.

## Physical crop gate

Every resolved screen must exist as its own image file. Record its actual SHA-256, width, height, `column_index`, zero-based `screen_index`, `screen_count_in_column` and `screen_boundary_reason`. Open every crop after writing it and correct material cuts.

Each unit receives a fresh nine-field AI observation from its own pixels. Do not populate observations from replacement copy or a desired product message.

## Immutable order

Sort by column left-to-right, then by `screen_index` top-to-bottom inside each column. Finish every screen in the first column before moving to the second.

If column 0 contains U01/U02/U03 and column 1 begins with U04, the only valid opening is `U01 → U02 → U03 → U04`. Sampling one screen per column is forbidden.

Each screen creates exactly one module. Each module uses only that screen crop and preserves its crop aspect ratio. Never send a whole column or the original multi-column montage to the image model after screen crops exist.
