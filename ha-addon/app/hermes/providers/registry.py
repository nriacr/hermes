from ..constants import SITE_AMAZON, SITE_HEPSIBURADA, SITE_NETWORK, SITE_TRENDYOL
from ..errors import HermesError
from ..models import OfferResult
from . import amazon, hepsiburada, network, trendyol

PROVIDERS = {
    SITE_AMAZON: amazon.extract_offer,
    SITE_HEPSIBURADA: hepsiburada.extract_offer,
    SITE_TRENDYOL: trendyol.extract_offer,
    SITE_NETWORK: network.extract_offer,
}


def extract_offer(site: str, html: str, source_url: str = "") -> OfferResult:
    site_key = str(site or "").strip().lower()
    parser = PROVIDERS.get(site_key)
    if parser is None:
        raise HermesError(f"Desteklenmeyen site parserı: {site}")
    if site_key == SITE_HEPSIBURADA:
        return parser(html, source_url=source_url)
    return parser(html)
