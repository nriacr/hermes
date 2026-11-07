"""Temporary, read-only MediaMarkt source check through the Hermes runtime."""

import json
import re
from urllib.parse import urljoin, urlsplit

import requests

from ..utils import build_headers
from .base import as_type_list, iter_json_objects, soup_from_html
from .http import cleaned_html, has_generic_challenge

HOME = "https://www.mediamarkt.com.tr/"
HOSTS = {"www.mediamarkt.com.tr", "mediamarkt.com.tr"}


def product_url(url):
    parsed = urlsplit(url)
    return parsed.scheme == "https" and parsed.netloc in HOSTS and parsed.path.startswith("/tr/product/")


PRICE_LABEL = re.compile(r"sepette.{0,20}indirim|adrese.{0,20}teslimat|sepete ekle|66[.,]499|3[.,]499", re.I)


def price_markup(soup):
    matches = []
    for text in soup.find_all(string=PRICE_LABEL):
        if text.parent.name in {"script", "style", "noscript"}:
            continue
        lineage = []
        for node in [text.parent, *text.parent.parents][:5]:
            description = node.get_text(" ", strip=True)
            if len(description) > 600:
                break
            attributes = {key: node.get(key) for key in ("data-testid", "data-test", "itemprop", "class") if node.has_attr(key)}
            lineage.append({"tag": node.name, "attrs": attributes, "text": description})
        if lineage and lineage not in matches:
            matches.append(lineage)
        if len(matches) == 18:
            break
    return matches


def inspect_source(url):
    if url and not product_url(url):
        return {"error": "MediaMarkt Türkiye ürün adresi gerekiyor."}
    url = url or HOME
    try:
        with requests.Session() as session:
            response = session.get(url, headers=build_headers(url), timeout=12, allow_redirects=False)
    except requests.RequestException as exc:
        return {"error": type(exc).__name__}
    result = {"status": response.status_code}
    if response.status_code != 200:
        location = urljoin(url, response.headers.get("location", ""))
        if product_url(location):
            result["product_redirect"] = urlsplit(location).path
        return result
    html = cleaned_html(response)
    soup = soup_from_html(html)
    result["challenge"] = has_generic_challenge(html)
    if soup.title:
        result["title"] = soup.title.get_text(" ", strip=True)[:240]
    if url == HOME:
        links = (urljoin(HOME, str(anchor["href"])) for anchor in soup.select("a[href]"))
        result["products"] = list(dict.fromkeys(urlsplit(link).path for link in links if product_url(link)))[:8]
        return result
    result["products"] = []
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(script.string or "")
        except json.JSONDecodeError:
            continue
        for item in iter_json_objects(payload):
            if "product" not in as_type_list(item.get("@type")):
                continue
            offers = item.get("offers")
            offers = offers if isinstance(offers, list) else [offers]
            product = {key: str(item.get(key, ""))[:240] for key in ("name", "sku")}
            product["offers"] = [
                {key: str(offer.get(key, ""))[:160] for key in ("@type", "price", "priceCurrency", "availability")}
                for offer in offers[:4] if isinstance(offer, dict)
            ]
            result["products"].append(product)
    result["products"] = result["products"][:8]
    result["price_markup"] = price_markup(soup)
    result["scripts"] = [str(script.get("id"))[:80] for script in soup.select("script[id]")][:15]
    return result
