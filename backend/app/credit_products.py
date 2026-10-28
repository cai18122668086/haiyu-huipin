"""Server-owned commercial catalog approved in the account-center review."""

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Product:
    id: str
    name: str
    amount_fen: int
    credits: int
    kind: str
    plan_id: str | None = None
    months: int = 0
    validity_months: int = 0

    def snapshot(self) -> dict:
        return asdict(self)


# The original single-pack offer remains compatible with existing clients and
# pending orders. The account center uses the three approved prototype packs.
LEGACY_PACK_ID = "credits_1500"
PRODUCTS = {
    "credits_1500": Product(
        "credits_1500",
        "创作积分包 · 1,500 积分",
        9900,
        1500,
        "pack",
        validity_months=0,
    ),
}
for product_id, name, price, credits in (
    ("pack_small", "灵感补给", 2990, 300),
    ("pack_medium", "日常创作", 9900, 1100),
    ("pack_large", "高频出图", 29900, 3600),
):
    PRODUCTS[product_id] = Product(
        product_id, name, price, credits, "pack", validity_months=12
    )

for plan_id, name, credits, monthly, annual in (
    ("light", "轻享版", 600, 4900, 46800),
    ("pro", "专业版", 1500, 9900, 94800),
    ("team", "团队版", 5000, 29900, 286800),
):
    for period, months, price in (("monthly", 1, monthly), ("annual", 12, annual)):
        product_id = f"{plan_id}_{period}"
        PRODUCTS[product_id] = Product(
            product_id,
            f"{name} · {'年付' if months == 12 else '月付'}",
            price,
            credits,
            "membership",
            plan_id,
            months,
        )
