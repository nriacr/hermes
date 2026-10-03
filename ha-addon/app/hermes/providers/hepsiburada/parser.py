"""Public Hepsiburada parsing interface.

The parsing code lives in topic modules (common, prices, variants, search,
detail); this module keeps the names the provider and the tests import.
"""

from .common import is_product_url, product_id_from_url
from .detail import _embedded_detail_candidates, extract_embedded_variant_offer, extract_offer
from .search import extract_search_offers
from .variants import (
    clean_display_title,
    extract_embedded_variant_label,
    extract_selected_variant_label,
    extract_selected_variant_labels,
    extract_variant_urls,
    title_with_variant_label,
)

__all__ = [
    "_embedded_detail_candidates",
    "clean_display_title",
    "extract_embedded_variant_label",
    "extract_embedded_variant_offer",
    "extract_offer",
    "extract_search_offers",
    "extract_selected_variant_label",
    "extract_selected_variant_labels",
    "extract_variant_urls",
    "is_product_url",
    "product_id_from_url",
    "title_with_variant_label",
]
