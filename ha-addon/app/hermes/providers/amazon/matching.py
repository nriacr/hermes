"""Amazon search-result matching: which results a configured card owns."""

import re
from typing import List, Optional

from ...errors import EmptySearchResultsHermesError
from ...models import OfferResult, SearchResultItem
from ...utils import normalize_offer_text
from .search import filter_matching_results, title_matches_keyword


def is_platform_seller(seller: Optional[str]) -> bool:
    return re.sub(r"[^a-z0-9]", "", normalize_offer_text(str(seller or ""))) == "amazoncomtr"


def _name_specificity(name: str) -> int:
    """Use model-name token count to prefer a more specific configured card."""
    return len(normalize_offer_text(name).split())


def most_specific_matches(results: List[SearchResultItem], product_name: str,
                          configured_names: Optional[List[str]] = None) -> List[SearchResultItem]:
    """Keep search results assigned to the most specific matching watch name.

    This is intentionally model-agnostic: when e.g. "Ultra" and "Ultra Max"
    cards both match a title, the longer configured model name owns that result.
    """
    matches = filter_matching_results(results, product_name) if product_name else results
    if not product_name or not configured_names:
        return matches
    names = {str(name).strip() for name in configured_names if str(name or "").strip()}
    if normalize_offer_text(product_name) not in {normalize_offer_text(name) for name in names}:
        names.add(product_name)
    current_specificity = _name_specificity(product_name)
    filtered: List[SearchResultItem] = []
    for item in matches:
        specificities = [_name_specificity(name) for name in names if title_matches_keyword(item.title, name)]
        if not specificities or current_specificity >= max(specificities):
            filtered.append(item)
    return filtered


def offers_from_search_results(results: List[SearchResultItem], product_name: str,
                               configured_names: Optional[List[str]] = None) -> List[OfferResult]:
    matches = most_specific_matches(results, product_name, configured_names)
    if not matches:
        raise EmptySearchResultsHermesError("Amazon arama sayfasında ürün adına uyan ürün bulunamadı.")
    # A search result does not prove the offer is sold by Amazon. Leave unknown
    # sellers unknown so the own-seller filter never trusts a marketplace card.
    return [
        OfferResult(title=item.title, price=item.price, seller=item.seller, url=item.url,
                    is_warehouse=item.is_warehouse, stock_quantity=item.stock_quantity)
        for item in matches
    ]
