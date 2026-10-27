from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, List, Optional

from .constants import DEFAULT_PRIORITY


@dataclass
class WatchRule:
    """One configured URL of a tracking card."""

    name: str
    site: str
    url: str
    target_price: Decimal
    minimum_price: Optional[Decimal] = None
    excluded_terms: List[str] = field(default_factory=list)
    size: str = ""
    # Chosen variant values per dimension ({"Renk": ["Siyah"]}); empty keeps every variant.
    variant_filter: Dict[str, List[str]] = field(default_factory=dict)
    include_variations: bool = False
    priority: str = DEFAULT_PRIORITY
    official_seller_only: bool = False
    auto_add_to_cart: bool = False
    check_now_token: str = ""
    max_items_to_scan: int = 60
    notify_once_in_24h: bool = True
    # Also notify when the price is the lowest of the last 90 days, even above the target.
    notify_on_90_day_low: bool = False
    # One of constants.NOTIFY_WINDOWS: when this card may send alerts.
    notify_window: str = "always"
    active: bool = True
    # Shared by every URL in one configured tracking card.
    tracking_id: str = ""


@dataclass
class SearchResultItem:
    title: str
    url: str
    price: Decimal
    is_warehouse: bool = False
    stock_quantity: Optional[int] = None
    seller: Optional[str] = None
    offer_id: str = ""


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
    is_warehouse: bool = False
    tracking_id: str = ""
    priority: str = DEFAULT_PRIORITY
    price_checked_at: str = ""

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
    checked_at: str = ""


@dataclass
class OfferResult:
    title: str
    price: Decimal
    seller: Optional[str] = None
    url: Optional[str] = None
    is_warehouse: bool = False
    stock_quantity: Optional[int] = None
    # Set only on an offer a provider replays from memory: when it was really read (ISO, UTC).
    checked_at: Optional[str] = None
    source: str = ""
    currency: str = "TRY"
    conditions: str = ""
    # Amazon offerListingId of this exact offer, used only by the opt-in cart action.
    offer_id: str = ""


@dataclass
class TelegramConfig:
    enabled: bool
    api_id: Optional[int]
    api_hash: str
    phone_number: str
    verification_code: str
    channels: List[str]
    keywords: List[str]
    exclude_keywords: List[str]
    saved_messages_enabled: bool = True


@dataclass
class HermesConfig:
    interval_seconds: int
    request_timeout_seconds: int
    request_delay_min_seconds: int
    request_delay_max_seconds: int
    pushover_user_key: str
    pushover_api_token: str
    watches: List[WatchRule]
    telegram: TelegramConfig
    config_errors: List[str] = field(default_factory=list)
    # One summary message a day at 09:00 (reads, errors, best deals).


@dataclass
class VariantPreview:
    """One variant read live for the settings page's link check."""

    label: str
    price: Optional[Decimal] = None
    in_stock: bool = True
    # Dimension values of this variant, e.g. {"Renk": "Siyah", "Beden": "M"}.
    attributes: Dict[str, str] = field(default_factory=dict)


@dataclass
class ProductPreview:
    """What a link check shows before a card is saved."""

    site: str
    title: str
    variants: List[VariantPreview] = field(default_factory=list)
    # Choice boxes: dimension name -> its values in page order.
    dimensions: Dict[str, List[str]] = field(default_factory=dict)
    # Values ticked before the user chooses (what the card watches when nothing is chosen).
    defaults: Dict[str, List[str]] = field(default_factory=dict)
