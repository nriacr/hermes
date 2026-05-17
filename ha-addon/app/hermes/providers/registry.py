from ..constants import SITE_AMAZON, SITE_HEPSIBURADA, SITE_NETWORK, SITE_TRENDYOL
from ..errors import HermesError
from ..models import OfferResult
from . import amazon, hepsiburada, network, trendyol


def extract_offer(site: str, html: str) -> OfferResult:
    site_key = str(site or SITE_AMAZON).strip().lower()
    if site_key == SITE_AMAZON:
        return amazon.extract_offer(html)
    if site_key == SITE_HEPSIBURADA:
        return hepsiburada.extract_offer(html)
    if site_key == SITE_TRENDYOL:
        return trendyol.extract_offer(html)
    if site_key == SITE_NETWORK:
        return network.extract_offer(html)
    raise HermesError(f"Desteklenmeyen site parseri: {site}")
