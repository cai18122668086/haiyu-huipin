"""Shared submission and adapter contract for Amazon visual-suite sites."""

from typing import Literal

AmazonMarketplace = Literal["amazon-us"]
AMAZON_DEFAULT_MARKETPLACE: AmazonMarketplace = "amazon-us"
AMAZON_MARKETPLACES = frozenset({AMAZON_DEFAULT_MARKETPLACE})
