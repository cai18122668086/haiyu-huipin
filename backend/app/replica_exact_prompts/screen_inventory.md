# Independently inventory physical screens before planning

Observe the original references and attached column crops afresh. You have not
been given proposed cuts or counts. Confirm whether the supplied columns cover
every visible reference column exactly once. Return all_columns_covered=false
when a column is missing, duplicated or materially cropped.

For each column list every physical commercial screen, in reading order. State
its dominant visible headline (or concrete scene label) and distinct buyer question.
A new dominant headline plus a new hero, question, demonstration or proof is a
new screen even when colors or gradients continue. Do not merge adjacent screens
into a broad topic. Do not split small labels within one continuous proof carrier.
Keep a headline with the proof, action or scene it governs. A title-only strip
above its supporting photo is not an additional screen merely because the photo
has a rounded edge or background change. Count self-contained commercial
compositions, not the header and body of the same composition separately.
Apply this dependency test also to the bottom region: price/coupon banners,
buy-one-get-one offers, discounts, campaign dates and sales-count strips
that complete the hero above belong to that SAME screen. A bold offer headline,
colored banner or whitespace gap alone is insufficient evidence of a new
composition. Count a lower region separately only when it has its own complete
hero, demonstration or evidence carrier beyond the offer. Do not confuse a
product poster's dependent promotional footer with a separate commercial panel.
Do not infer a fixed screen count from image aspect ratio.
This exception covers transactional purchase terms only. A product-property,
ingredient, nutrition, performance-comparison or benefit card with its own
illustration, chart or explanatory mechanism is NOT a promotional footer.
Independently inventory each such self-contained evidence carrier with a
distinct commercial duty. In a stack of rounded cards, ingredient pictures, a
protein/standard comparison chart and a separate gut-health mechanism remain
distinct carriers despite one shared overall heading and matching card colors.
Do not assume a standard count or equal heights. The server will independently
compare this inventory with the physical cuts; you do not issue a PASS verdict.

Independently report boundary_ys: each internal horizontal cut as an increasing
integer y coordinate in that attached COLUMN image. Return one screen per
interval between 0, your boundary_ys and column height. Equal counts with different
physical intervals are a disagreement. Distinguish a self-contained panel from
small text labels within it; never add boundaries merely to reach an expected count.
For coverage_scope=requested_columns_only, inspect only the supplied columns;
original reference images provide context and do not request additional columns.
Locate each cut after all dependent captions of the preceding composition and
before the next composition's headline, preferably in their visible clear gap.
Never detach an ingredient's small explanatory caption or a chart's own title.

No earlier container-detection prose, proposed screen count or proposed cuts is
evidence for this independent inventory. Count only from the actual source
compositions. A shared topic, gradient or outer rounded card does not make a
formulation mechanism and a separate headline-led experimental measurement one
screen when each has its own complete commercial duty.
When reference_column_coordinate_guide is supplied, the numbered left gutter is
outside the original source. Ignore its graphics as content; use its original
source-pixel y scale for coordinates. Height is unchanged. The measured blank
bands are unclassified whitespace, not desired boundaries or a requested count.
Choose one only when your independent semantic observation places a transition
there, after the earlier composition's final dependent caption and before the
next headline. Do not infer coordinate values from the displayed thumbnail size.
