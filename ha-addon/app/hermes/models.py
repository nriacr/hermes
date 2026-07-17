from dataclasses import dataclass, field
from decimal import Decimal
from typing import List, Optional


@dataclass
class WatchRule:
    name: str
    site: str
    url: str
    target_price: Decimal
    minimum_price: Optional[Decimal] = None
    excluded_terms: List[str] = field(default_factory=list)
    group: str = ""
    size: str = ""
    max_items_to_scan: int = 60
    check_interval_minutes: Optional[int] = None
    notify_once_in_24h: bool = True
    active: bool = True


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
    search_group: str = ""
    search_group_label: str = ""

    @property
    def difference(self) -> Decimal:
        return self.price - self.target_price


@dataclass
class StockSummaryRow:
    seller: str
    product_title: str
    product_url: str
    target_price: Decimal
    reason: str


@dataclass
class OfferResult:
    title: str
    price: Decimal
    seller: Optional[str] = None
    url: Optional[str] = None


@dataclass
class HermesConfig:
    interval_seconds: int
    request_timeout_seconds: int
    request_delay_min_seconds: int
    request_delay_max_seconds: int
    pushover_user_key: str
    pushover_api_token: str
    watches: List[WatchRule]
    telegram: "TelegramConfig"


@dataclass
class TelegramConfig:
    enabled: bool
    api_id: Optional[int]
    api_hash: str
    phone_number: str
    verification_code: str
    session_name: str
    channels: List[str]
    keywords: List[str]
    exclude_keywords: List[str]
