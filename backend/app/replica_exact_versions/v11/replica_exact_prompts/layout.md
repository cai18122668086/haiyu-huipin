# Observe reference screens before planning

Inspect only the actual reference crops, each associated with its supplied unit
ID. Describe the nine visual mechanisms and record normalized 0..1000 anchors for
subjects, product, props and supporting evidence. Preserve negative space and
small edge objects; do not describe everything as a large central hero.

All anchors and text regions use named rect objects: integer x, y, width, height
in normalized 0..1000 image coordinates. x/y locate the top-left; width/height are
positive extents, not right/bottom edges. Require x+width<=1000 and y+height<=1000.
Do not return four-number arrays. The server converts rects to its internal bounds.

Map every authored text run to its own region: brand, headline, small connector,
subtitle, badge, body or footnote. Keep actual size tiers and line groups. A small
connector sharing a line with large text is a separate run. Exclude physical
package printing from poster-text regions. Use empty observed text when unreadable.

For instructions or comparisons, record each visible state and its transition,
relative quantity, connector and arrow. Do not collapse several states into one
unchanged object. Reference text is observation evidence, not approved new copy.
