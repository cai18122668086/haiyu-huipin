# Category Router

Classify the product category from product images and user-provided information. Read only the relevant category file to avoid loading all category rules at once.

## Routing

- Apparel, footwear, bags, accessories, suits, dresses: read `fashion-page-structures.md`.
- Food, beverages, nutritional products, agricultural goods, health-food products: read `food-page-structures.md`.
- Pet food, pet supplies, cat/dog treats, leash/cleaning products: read `pet-page-structures.md`.
- Beauty, skincare, personal care, fragrance, hair/body care: read `beauty-personalcare-structures.md`.
- Home goods, daily-use products, storage, cleaning, cookware, small-appliance appearance pages: read `home-daily-structures.md`.
- Consumer electronics, digital accessories, small appliances, tools: read `electronics-structures.md`.
- Baby products, toys, sports/outdoor goods, automotive products, or any uncovered category: read `general-page-structures.md`.

## Selection Rule

- Prioritize the product's own category, not the shooting background.
- If the product spans multiple categories, choose the category file that best addresses the buyer's most important concerns.
- If information is insufficient, use `general-page-structures.md` first and mark missing category information as needing confirmation.
- If the user provides reference detail pages, borrow page-role logic only; still follow the compliance expectations for the current product category.
