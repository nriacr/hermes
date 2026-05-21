from dataclasses import dataclass, field
from decimal import Decimal
from typing import List, Optional


@dataclass
class ProductRule:
    name: str
    site: str
    url: str
    target_price: Decimal
    notify_once_in_24h: bool = True
    active: bool = True


@dataclass
class AmazonSearchTarget:
    name: str
    search_name: str
    product_name: str
    target_price: Decimal
    notify_once_in_24h: bool = True
    active: bool = True


@dataclass
class AmazonSearchPage:
    name: str
    search_urls: List[str]
    max_items_to_scan: int = 24
    targets: List[AmazonSearchTarget] = field(default_factory=list)


@dataclass
class SearchResultItem:
    title: str
    url: str
    price: Decimal


@dataclass
class PriceSummaryRow:
    seller: str
    product_title: str
    product_url: str
    price: Decimal
    target_price: Decimal
    min_price: Decimal
    max_price: Decimal

    @property
    def difference(self) -> Decimal:
        return self.price - self.target_price


@dataclass
class OfferResult:
    title: str
    price: Decimal
    seller: Optional[str] = None
    url: Optional[str] = None


@dataclass
class HermesConfig:
    interval_minutes: int
    request_timeout_seconds: int
    pushover_user_key: str
    pushover_api_token: str
    products: List[ProductRule]
    amazon_search_pages: List[AmazonSearchPage]
