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
    validity_days: int = 0
    first_purchase_only: bool = False

    def snapshot(self) -> dict:
        return asdict(self)


# Product IDs remain stable; historical orders retain their stored terms.
LEGACY_PACK_ID = "credits_1500"
STARTER_PACK_ID = "pack_starter_160"
PRODUCTS = {
    "newcomer_160": Product(
        "newcomer_160",
        "新人首单体验包",
        990,
        240,
        "pack",
        validity_days=30,
        first_purchase_only=True,
    ),
    "credits_1500": Product(
        "credits_1500",
        "创作积分包 · 1,500 积分",
        9900,
        1500,
        "pack",
        validity_months=0,
    ),
    STARTER_PACK_ID: Product(
        STARTER_PACK_ID,
        "轻量补给",
        4900,
        800,
        "pack",
        validity_months=12,
    ),
}
for product_id, name, price, credits in (
    ("pack_small", "日常创作", 9900, 1800),
    ("pack_medium", "高频出图", 29900, 6000),
    ("pack_large", "批量创作", 79900, 18000),
):
    PRODUCTS[product_id] = Product(
        product_id, name, price, credits, "pack", validity_months=12
    )

for plan_id, name, credits, monthly, annual in (
    ("light", "轻享版", 1000, 4900, 46800),
    ("pro", "专业版", 2400, 9900, 94800),
    ("team", "团队版", 8000, 29900, 286800),
    ("flagship", "旗舰版", 24000, 79900, 766800),
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
