"""All supported sites. A new site gets its own provider module and one line here."""

import contextlib
from typing import Dict, Iterable

from ..errors import HermesError
from .amazon import AmazonProvider
from .base import Provider
from .beymenclub import BeymenClubProvider
from .hepsiburada import HepsiburadaProvider
from .hm import HMProvider
from .ikea import IkeaProvider
from .network import NetworkProvider
from .togg import ToggProvider
from .trendyol import TrendyolProvider
from .zara import ZaraProvider

PROVIDER_TYPES = (
    AmazonProvider,
    HepsiburadaProvider,
    TrendyolProvider,
    NetworkProvider,
    BeymenClubProvider,
    ZaraProvider,
    HMProvider,
    ToggProvider,
    IkeaProvider,
)


class ProviderSet:
    """One provider instance per site, owned by the monitor."""

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
            with contextlib.suppress(Exception):  # shutdown continues for the other sites
                provider.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
