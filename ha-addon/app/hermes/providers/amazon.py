import json
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ..errors import HermesError
from ..models import OfferResult
from ..utils import (
    canonical_amazon_product_url,
    extract_asin_from_url,
    make_amazon_absolute_url,
    normalize_offer_text,
    parse_decimal,
    repair_mojibake,
)
from .base import (
    extract_jsonld_product,
    extract_price_from_meta,
    extract_title,
    soup_from_html,
)

AMAZON_PRODUCT_SELECTORS = [
    "#corePriceDisplay_desktop_feature_div .a-price .a-offscreen",
    "#corePriceDisplay_desktop_feature_div .aok-offscreen",
    "#corePrice_feature_div .a-price .a-offscreen",
    "#corePrice_feature_div .aok-offscreen",
    "#corePriceDisplay_desktop_feature_div span[data-a-color='price'] .a-offscreen",
    "#corePrice_feature_div span[data-a-color='price'] .a-offscreen",
    "#tp_price_block_total_price_ww .a-offscreen",
    ".apexPriceToPay .a-offscreen",
    "#priceblock_ourprice",
    "#priceblock_dealprice",
]

AMAZON_PRIMARY_PRICE_CONTAINERS = [
    "#corePriceDisplay_desktop_feature_div",
    "#corePrice_feature_div",
    "#apex_desktop",
]

BUYING_OPTION_PRICE_PATTERNS = [
    re.compile(
        r"degis\s+tokus\s+olmadan\s+"
        r"(?P<whole>\d{1,3}(?:\.\d{3})+|\d+)"
        r"(?:,(?P<comma_fraction>\d{1,2})|\s*(?P<plain_fraction>\d{2}))?\s*tl"
    ),
    re.compile(
        r"takas\s+olmadan\s+"
        r"(?P<whole>\d{1,3}(?:\.\d{3})+|\d+)"
        r"(?:,(?P<comma_fraction>\d{1,2})|\s*(?P<plain_fraction>\d{2}))?\s*tl"
    ),
]

AMAZON_VARIATION_QUERY_PARAMS = {"smid", "psc", "th"}
AMAZON_COLOR_VARIATION_SELECTORS = (
    "#inline-twister-row-color_name li",
    "[id*='inline-twister-row-color'] li",
    "#variation_color_name li",
    "#variation_color li",
    "[id*='variation_color'] li",
)

AMAZON_USED_OFFER_LINK_SELECTOR = "a[href*='offer-listing']"
AMAZON_USED_OFFER_CONTAINER_SELECTORS = (
    ".aod-offer",
    "[data-cy='aod-offer']",
    "[data-cy*='offer']",
    "[id^='aod-offer']",
    "#olpOfferList .a-row",
)
AMAZON_LOW_STOCK_SELECTORS = (
    "#availability",
    "#availabilityInsideBuyBox_feature_div",
    "#availability_feature_div",
    "#outOfStock",
)
AMAZON_LOW_STOCK_PATTERN = re.compile(r"stokta\s+sadece\s+(?P<quantity>\d+)\s+adet\s+kaldi")


@dataclass(frozen=True)
class AmazonColorVariation:
    label: str
    url: str


def _normalized_variation_url(raw_url: str, asin: str = "") -> str:
    if not raw_url and not asin:
        return ""
    absolute_url = make_amazon_absolute_url(raw_url) if raw_url else ""
    canonical_url = canonical_amazon_product_url(absolute_url, fallback_asin=asin)
    parsed = urlsplit(absolute_url)
    stable_params = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key in AMAZON_VARIATION_QUERY_PARAMS
    ]
    if not stable_params:
        return canonical_url
    return urlunsplit(("https", "www.amazon.com.tr", urlsplit(canonical_url).path, urlencode(stable_params), ""))


def is_same_color_variation(first_url: str, second_url: str) -> bool:
    """Compare product variations without tracking-only Amazon URL differences."""
    first = color_variation_identity(first_url)
    second = color_variation_identity(second_url)
    if first and second:
        return first == second
    return False


def color_variation_identity(url: str) -> str:
    """Return the stable identifier used to avoid duplicate color variations."""
    return _normalized_variation_url(url, extract_asin_from_url(url) or "")


def _variation_url_from_element(element) -> str:
    link = element.select_one("a[href]")
    raw_url = str(link.get("href") or "") if link else ""
    asin = ""
    for node in (element, link):
        if not node:
            continue
        asin = str(node.get("data-asin") or node.get("data-defaultasin") or "").strip()
        if asin:
            break
    return _normalized_variation_url(raw_url, asin or extract_asin_from_url(raw_url) or "")


def _clean_color_label(value: str) -> str:
    text = repair_mojibake(value).strip()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"^(?:renk|color|colour)\s*[:\-]\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*(?:seçili|selected)\s*$", "", text, flags=re.IGNORECASE)
    if not text or len(text) > 80:
        return ""
    normalized = normalize_offer_text(text)
    if normalized in {"renk", "color", "colour", "secenek", "seçenek"}:
        return ""
    return text


def _color_label_from_element(element) -> str:
    candidates = []
    for node in (element, element.select_one("a"), element.select_one("img")):
        if not node:
            continue
        candidates.extend(
            str(node.get(attribute) or "")
            for attribute in ("title", "aria-label", "alt", "data-a-html-content")
        )
    candidates.append(element.get_text(" ", strip=True))
    for candidate in candidates:
        label = _clean_color_label(candidate)
        if label:
            return label
    return ""


def _load_json(value: str) -> dict:
    try:
        loaded = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _script_color_variations(soup, source_url: str, limit: int) -> list[AmazonColorVariation]:
    """Read Amazon's current Twister state when color swatches are rendered dynamically."""
    for script in soup.select("script[type='a-state'][data-a-state]"):
        state = _load_json(str(script.get("data-a-state") or ""))
        if "twister" not in normalize_offer_text(str(state.get("key") or "")):
            continue

        payload = _load_json(script.get_text("", strip=True))
        dimensions = payload.get("sortedDimValuesForAllDims")
        colors = dimensions.get("color_name") if isinstance(dimensions, dict) else None
        if not isinstance(colors, list):
            continue

        variations: list[AmazonColorVariation] = []
        seen_urls: set[str] = set()
        for color in colors:
            if not isinstance(color, dict) or len(variations) >= max(1, limit):
                continue
            state_name = normalize_offer_text(str(color.get("dimensionValueState") or ""))
            if state_name not in {"available", "selected"}:
                continue

            label = _clean_color_label(str(color.get("dimensionValueDisplayText") or ""))
            asin = str(color.get("defaultAsin") or "").strip()
            raw_url = str(color.get("pageLoadURL") or "")
            if not raw_url and state_name == "selected":
                raw_url = source_url
            url = _normalized_variation_url(raw_url, asin)
            if not label or not url or url in seen_urls:
                continue
            seen_urls.add(url)
            variations.append(AmazonColorVariation(label=label, url=url))

        if variations:
            return variations
    return []


def extract_color_variations(html: str, source_url: str, limit: int) -> list[AmazonColorVariation]:
    """Return concrete Amazon color URLs from the product-page twister."""
    soup = soup_from_html(html)
    variations = _script_color_variations(soup, source_url, limit)
    if variations:
        return variations[: max(1, limit)]

    variations: list[AmazonColorVariation] = []
    seen_urls: set[str] = set()
    for selector in AMAZON_COLOR_VARIATION_SELECTORS:
        elements = soup.select(selector)
        if not elements:
            continue
        for element in elements:
            if len(variations) >= max(1, limit):
                break
            classes = set(element.get("class") or [])
            if "swatchUnavailable" in classes or "a-button-unavailable" in classes:
                continue
            url = _variation_url_from_element(element)
            if not url or url in seen_urls:
                continue
            label = _color_label_from_element(element)
            if not label:
                continue
            seen_urls.add(url)
            variations.append(AmazonColorVariation(label=label, url=url))
        if variations:
            break

    if not variations:
        return []

    source_variation_url = _normalized_variation_url(source_url, extract_asin_from_url(source_url) or "")
    if source_variation_url and all(item.url != source_variation_url for item in variations):
        selected = soup.select_one("#variation_color_name .selection, #variation_color .selection")
        selected_label = _clean_color_label(selected.get_text(" ", strip=True)) if selected else ""
        variations.insert(0, AmazonColorVariation(label=selected_label, url=source_variation_url))
    return variations[: max(1, limit)]


def title_with_color(title: str, color: str) -> str:
    clean_title = repair_mojibake(title).strip() or "Amazon ürünü"
    clean_color = _clean_color_label(color)
    if not clean_color or normalize_offer_text(clean_color) in normalize_offer_text(clean_title):
        return clean_title
    return f"{clean_title} / {clean_color}"


def _parse_visible_price(text: str):
    clean = str(text or "").strip()
    if not clean:
        return None
    adjacent_fraction = re.search(r"(?P<whole>\d{1,3}(?:\.\d{3})+)(?P<fraction>\d{2})\s*TL", clean, re.I)
    if adjacent_fraction:
        clean = f"{adjacent_fraction.group('whole')},{adjacent_fraction.group('fraction')} TL"
    if "TL" in clean and "," not in clean and "." in clean:
        clean = clean.replace(".", "")
    try:
        return parse_decimal(clean)
    except HermesError:
        return None


def _ancestor_has_active_row(element) -> bool:
    current = element
    while current is not None and getattr(current, "name", None):
        classes = set(current.get("class") or [])
        if (
            current.get("data-csa-c-is-in-initial-active-row") == "true"
            or "a-accordion-active" in classes
        ):
            return True
        current = current.parent
    return False


def _extract_active_customer_visible_price(soup):
    candidates = []
    for amount_input in soup.select("input[name='items[0.base][customerVisiblePrice][amount]']"):
        price = _parse_visible_price(str(amount_input.get("value") or ""))
        if price is None:
            continue
        candidates.append((_ancestor_has_active_row(amount_input), price))
    for is_active, price in candidates:
        if is_active:
            return price
    if candidates:
        return min(price for _, price in candidates)
    return None


def _price_from_split_spans(price_element):
    whole = price_element.select_one(".a-price-whole")
    if not whole:
        return None
    whole_text = re.sub(r"[^\d.]", "", whole.get_text("", strip=True))
    if not whole_text:
        return None
    fraction = price_element.select_one(".a-price-fraction")
    fraction_text = re.sub(r"\D", "", fraction.get_text("", strip=True)) if fraction else "00"
    fraction_text = (fraction_text or "00")[:2].ljust(2, "0")
    return _parse_visible_price(f"{whole_text},{fraction_text} TL")


def _extract_split_primary_price(soup):
    for container_selector in AMAZON_PRIMARY_PRICE_CONTAINERS:
        container = soup.select_one(container_selector)
        if not container:
            continue
        for price_element in container.select(".a-price"):
            classes = set(price_element.get("class") or [])
            if "a-text-price" in classes or price_element.get("data-a-strike") == "true":
                continue
            price = _price_from_split_spans(price_element)
            if price is not None:
                return price
    return None


def _extract_buying_option_price(soup):
    normalized_text = normalize_offer_text(soup.get_text(" ", strip=True))
    for pattern in BUYING_OPTION_PRICE_PATTERNS:
        match = pattern.search(normalized_text)
        if not match:
            continue
        fraction = match.group("comma_fraction") or match.group("plain_fraction") or "00"
        price = _parse_visible_price(f"{match.group('whole')},{fraction} TL")
        if price is not None:
            return price
    return None


def _extract_visible_primary_price(soup):
    active_price = _extract_active_customer_visible_price(soup)
    if active_price is not None:
        return active_price
    split_price = _extract_split_primary_price(soup)
    if split_price is not None:
        return split_price
    buying_option_price = _extract_buying_option_price(soup)
    if buying_option_price is not None:
        return buying_option_price
    for selector in AMAZON_PRODUCT_SELECTORS:
        element = soup.select_one(selector)
        if not element:
            continue
        raw_value = str(element.get("content") or element.get("value") or "").strip()
        price = _parse_visible_price(raw_value or element.get_text(" ", strip=True))
        if price is not None:
            return price
    return None


def extract_low_stock_quantity(html: str) -> Optional[int]:
    """Return Amazon's explicit low-stock quantity, never a generic stock status."""
    soup = soup_from_html(html)
    texts = [
        element.get_text(" ", strip=True)
        for selector in AMAZON_LOW_STOCK_SELECTORS
        for element in soup.select(selector)
    ]
    # Amazon occasionally moves the availability copy outside its usual block.
    # The exact wording remains mandatory, so this fallback cannot turn a plain
    # "Stokta var" message into a numeric stock result.
    texts.append(soup.get_text(" ", strip=True))
    for text in texts:
        match = AMAZON_LOW_STOCK_PATTERN.search(normalize_offer_text(text))
        if not match:
            continue
        quantity = int(match.group("quantity"))
        if quantity > 0:
            return quantity
    return None


def is_warehouse_search_url(source_url: str) -> bool:
    """Return true only for an explicit Amazon Depo search category."""
    parsed = urlsplit(str(source_url or ""))
    if not parsed.path.rstrip("/").endswith("/s"):
        return False
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    return any(
        normalize_offer_text(query.get(key, "")).replace("-", " ") == "warehouse deals"
        for key in ("i", "s")
    )


def _is_used_offer_url(url: str) -> bool:
    parsed = urlsplit(str(url or ""))
    if "offer-listing" not in parsed.path:
        return False
    query = {key.casefold(): value.casefold() for key, value in parse_qsl(parsed.query, keep_blank_values=True)}
    return query.get("condition") in {"used", "secondhand"}


def extract_used_offer_listing_url(html: str, source_url: str = "") -> str:
    """Find Amazon's dedicated used-offer listing without guessing a seller."""
    soup = soup_from_html(html)
    for link in soup.select(AMAZON_USED_OFFER_LINK_SELECTOR):
        raw_url = str(link.get("href") or "").strip()
        absolute_url = make_amazon_absolute_url(raw_url) if raw_url else ""
        if _is_used_offer_url(absolute_url):
            return absolute_url
    # Amazon sometimes renders the used-offer text before the offer-listing
    # link is hydrated. In that case the ASIN endpoint is still the only
    # acceptable fallback, and it is tried only when the page itself says a
    # second-hand offer exists.
    page_text = normalize_offer_text(soup.get_text(" ", strip=True))
    asin = extract_asin_from_url(source_url)
    if asin and "ikinci el" in page_text:
        return f"https://www.amazon.com.tr/gp/offer-listing/{asin}?condition=used"
    return ""


def _offer_container_price(container):
    for price_element in container.select(".a-price"):
        classes = set(price_element.get("class") or [])
        if "a-text-price" in classes or price_element.get("data-a-strike") == "true":
            continue
        price = _price_from_split_spans(price_element)
        if price is not None:
            return price
        offscreen = price_element.select_one(".a-offscreen")
        if offscreen:
            price = _parse_visible_price(offscreen.get_text(" ", strip=True))
            if price is not None:
                return price
    return None


def extract_verified_warehouse_offers_from_listing(html: str, source_url: str) -> list[OfferResult]:
    """Read only explicit Amazon Depo second-hand offers from Amazon's offer list.

    The product's primary price is deliberately never reused here.  Amazon can
    show the normal price and a used offer on the same page, so the seller and
    price must come from one concrete used-offer row.
    """
    soup = soup_from_html(html)
    title, _ = extract_jsonld_product(soup)
    title = title or extract_title(soup) or "Amazon ürünü"
    offers: list[OfferResult] = []
    seen_prices: set = set()
    containers = []
    for selector in AMAZON_USED_OFFER_CONTAINER_SELECTORS:
        for container in soup.select(selector):
            if container not in containers:
                containers.append(container)

    for container in containers:
        text = normalize_offer_text(container.get_text(" ", strip=True))
        # Both conditions are mandatory. A generic used-offer panel or another
        # merchant must never receive Hermes' DEPO label.
        if "amazon depo" not in text or not any(marker in text for marker in ("ikinci el", "kullanilmis", "used")):
            continue
        price = _offer_container_price(container)
        if price is None or price in seen_prices:
            continue
        seen_prices.add(price)
        offers.append(
            OfferResult(
                title=title,
                price=price,
                seller="Amazon Depo",
                url=source_url,
                is_warehouse=True,
            )
        )
    return offers


def extract_offers(html: str, source_url: str = "") -> list[OfferResult]:
    """Extract normal and used offers separately when Amazon shows both on one page."""
    soup = soup_from_html(html)
    jsonld_title, jsonld_price = extract_jsonld_product(soup)
    title: Optional[str] = jsonld_title or extract_title(soup) or "Amazon ürünü"
    stock_quantity = extract_low_stock_quantity(html)
    offers: list[OfferResult] = []

    primary_price = _extract_visible_primary_price(soup)
    if primary_price is not None:
        offers.append(
            OfferResult(
                title=title,
                price=primary_price,
                seller=None,
                # A product page's main price always belongs to the selected
                # new offer. A separate second-hand row is added only after
                # its own price and Amazon Depo seller are verified below.
                is_warehouse=False,
                stock_quantity=stock_quantity,
            )
        )

    if offers:
        return offers

    for price in (jsonld_price, extract_price_from_meta(soup)):
        if price is not None:
            return [
                OfferResult(
                    title=title,
                    price=price,
                    seller=None,
                    is_warehouse=False,
                    stock_quantity=stock_quantity,
                )
            ]

    raise HermesError("Amazon sayfasından fiyat bulunamadı.")


def extract_offer(html: str, source_url: str = "") -> OfferResult:
    """Compatibility helper for callers that expect Amazon's primary offer only."""
    return extract_offers(html, source_url=source_url)[0]
