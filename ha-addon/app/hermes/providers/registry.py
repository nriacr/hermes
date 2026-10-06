"""All supported sites. A new site gets its own provider module and one line here."""

from typing import Dict, Iterable

from ..errors import HermesError
from .amazon import AmazonProvider
from .base import Provider
from .bengurme import BenGurmeProvider
from .beymenclub import BeymenClubProvider
from .hepsiburada import HepsiburadaProvider
from .hm import HMProvider
from .network import NetworkProvider
from .nordbron import NordbronProvider
from .togg import ToggProvider
from .trendyol import TrendyolProvider
from .zara import ZaraProvider

PROVIDER_TYPES = (
    AmazonProvider,
    HepsiburadaProvider,
    TrendyolProvider,
    NetworkProvider,
    BeymenClubProvider,
    BenGurmeProvider,
    NordbronProvider,
    ZaraProvider,
    HMProvider,
    ToggProvider,
)


class ProviderSet:
    """One provider instance per site, owned by the monitor or a link test."""

    def __init__(self, overrides: Dict[str, Provider] | None = None) -> None:
        self.providers: Dict[str, Provider] = {}
        for provider_type in PROVIDER_TYPES:
            self.providers[provider_type.site] = (overrides or {}).get(provider_type.site) or provider_type()

    def __getitem__(self, site: str) -> Provider:
        provider = self.providers.get(str(site or "").strip().lower())
        if provider is None:
            raise HermesError(f"Desteklenmeyen site okuyucusu: {site}")
        return provider

    def __iter__(self) -> Iterable[Provider]:
        return iter(self.providers.values())

    def begin_cycle(self) -> None:
        for provider in self:
            provider.begin_cycle()

    def close(self) -> None:
        for provider in self:
            try:
                provider.close()
            except Exception:  # noqa: BLE001 - shutdown continues for the other sites
                pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
