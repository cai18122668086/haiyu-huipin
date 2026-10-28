# Observe the original intake

Inspect the supplied product originals and reference originals. Describe at least
three view-independent product geometry facts. Isolate the real product in each
product crop, retaining its complete physical identity.

Locate every full-height reference column, in reference-major, left-to-right order.
Return column bounds, not screen cuts. Include every supplied reference. Use
integer original-image coordinates. Exclude outer white gutters while retaining
all commercial content, including the top headline and bottom footer. Explain the
visible gutter or outer edge that separates each column. A later focused step
will split its internal screens; do not merge or choose those screens here.
A self-contained poster remains one column even if it contains a card grid or
several text columns. Separate parallel independent page strips, not the internal
layout of one commercial screen.

Every rect is an object with integer x, y, width, height in ORIGINAL image pixels.
x/y locate its top-left corner; width/height are positive extents, not right/bottom
edges. x+width and y+height must stay inside the supplied source dimensions. Return
named rect objects; the server converts them to its internal edge coordinates.
