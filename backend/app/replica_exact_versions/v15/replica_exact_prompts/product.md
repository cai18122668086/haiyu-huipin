# Transcribe only product pixels

Read the supplied product originals independently of reference advertising and
user marketing assertions. Return legible marks verbatim with original-pixel
bounds: product name, variant, ingredients, weight, complete numbers and visible
manufacturer marks. The server binds each product index to its actual source hash.

Use rect objects with integer x, y, width, height in the ORIGINAL product image's
pixels. x/y are the top-left; width/height are positive extents, never right/bottom
edges. Keep x+width and y+height within the supplied width and height. Do not return
an unnamed four-number array. The server converts rects to internal edge bounds.

Do not guess microprint or substitute familiar ingredients. An unmarked product
may have no readable marks. Describe exactly what remains unreadable or unknown.
