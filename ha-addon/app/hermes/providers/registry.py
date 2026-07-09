from ..constants import SITE_AMAZON, SITE_HEPSIBURADA, SITE_HM, SITE_NETWORK, SITE_NORDBRON, SITE_TRENDYOL, SITE_ZARA
from ..errors import HermesError
from ..models import OfferResult
from . import amazon, hepsiburada, hm, network, nordbron, trendyol, zara

PROVIDERS = {
    SITE_AMAZON: amazon.extract_offer,
    SITE_HEPSIBURADA: hepsiburada.extract_offer,
    SITE_TRENDYOL: trendyol.extract_offer,
    SITE_NETWORK: network.extract_offer,
    SITE_NORDBRON: nordbron.extract_offer,
    SITE_ZARA: zara.extract_offer,
    SITE_HM: hm.extract_offer,
}


def extract_offer(site: str, html: str, source_url: str = "") -> OfferResult:
    site_key = str(site or "").strip().lower()
    parser = PROVIDERS.get(site_key)
    if parser is None:
        raise HermesError(f"Desteklenmeyen site parserı: {site}")
    if site_key in {SITE_HEPSIBURADA, SITE_ZARA, SITE_HM}:
        return parser(html, source_url=source_url)
    return parser(html)
