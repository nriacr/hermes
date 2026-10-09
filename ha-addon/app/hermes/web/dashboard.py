"""The Özet Tablo home screen: deal cards, price tiles with history, stock list, Telegram and errors."""

import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from html import escape
from typing import Any, Dict, List, Optional

from ..constants import (
    APP_VERSION,
    DATABASE_PATH,
    PRIORITY_DESCRIPTIONS,
    PRIORITY_LABELS,
    STATE_PATH,
    SUMMARY_PATH,
    TELEGRAM_ERROR_EVENTS_PATH,
    TELEGRAM_STATUS_PATH,
    normalize_priority,
)
from ..storage import load_json
from ..diagnostics import Diagnostics
from ..utils import PROCESS_STARTED_AT, format_tl, is_search_url, parse_bool, parse_iso_datetime, repair_mojibake, site_label
from .pages import link, render_notice, render_page
from .pricechart import SPOT_SIZE, detail_chart, load_histories, offer_index, sparkline, with_current

SITE_THEME_CLASSES = (
    ("amazon", "site-amazon"),
    ("hepsiburada", "site-hepsiburada"),
    ("network", "site-network"),
    ("beymen club", "site-beymenclub"),
    ("beymenclub", "site-beymenclub"),
    ("trendyol", "site-trendyol"),
    ("nordbron", "site-nordbron"),
    ("zara", "site-zara"),
    ("h&m", "site-hm"),
    ("togg", "site-togg"),
)


# -- formatting ---------------------------------------------------------------------


def parse_turkish_money(value) -> Optional[Decimal]:
    text = str(value or "").strip().replace("TL", "").replace(" ", "")
    text = text.replace("+", "").replace(".", "").replace(",", ".")
    if not text or text == "-":
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def display_tl(value, signed: bool = False) -> str:
    """Whole Turkish lira, e.g. `1.500 TL`; kuruş are never displayed."""
    text = str(value or "").strip()
    amount = parse_turkish_money(text)
    if amount is None:
        return text or "-"
    sign = ("-" if amount < 0 or text.startswith("-") else "+") if signed else ""
    whole_lira = abs(amount).quantize(Decimal("1"), rounding=ROUND_DOWN)
    return f"{sign}{whole_lira:,} TL".replace(",", ".")


def _parse_local_time(value):
    raw = str(value or "").strip()
    try:
        return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").astimezone()
    except ValueError:
        return parse_iso_datetime(raw)


def relative_time_text(value) -> str:
    parsed = _parse_local_time(value)
    if not parsed:
        return "-"
    elapsed = max(0, int((datetime.now().astimezone() - parsed.astimezone()).total_seconds()))
    if elapsed < 60:
        return "az önce" if elapsed < 10 else f"{elapsed} sn önce"
    minutes = elapsed // 60
    if minutes < 60:
        return f"{minutes} dk önce"
    hours = minutes // 60
    return f"{hours} sa önce" if hours < 24 else f"{hours // 24} gün önce"


def duration_text(seconds_value, fallback="-") -> str:
    if seconds_value in (None, ""):
        return str(fallback or "-")
    try:
        total_seconds = max(0, int(round(float(seconds_value))))
    except (TypeError, ValueError):
        return str(fallback or "-")
    minutes, seconds = divmod(total_seconds, 60)
    return f"{minutes} dk {seconds} sn" if minutes else f"{seconds} sn"


def site_theme_class(seller: str) -> str:
    normalized = repair_mojibake(seller).casefold()
    return next((css for marker, css in SITE_THEME_CLASSES if marker in normalized), "site-other")


def is_target_hit(row: Dict[str, Any]) -> bool:
    explicit = row.get("is_target_hit")
    if isinstance(explicit, bool):
        return explicit
    price, target = parse_turkish_money(row.get("price")), parse_turkish_money(row.get("target"))
    if price is not None and target is not None:
        return price <= target
    difference = parse_turkish_money(row.get("difference"))
    return difference is not None and difference <= 0


# -- offers ---------------------------------------------------------------------------

STOCK_SUFFIX = re.compile(r"\s*\(Stok\s+(\d+)\)\s*$", re.IGNORECASE)
VARIANT_SPLIT = re.compile(r"\s*[,/;]\s*")
MAX_VARIANT_TAGS = 3


def priority_dot(priority) -> str:
    """Round mark for a card's scan interval: red (every cycle) through orange and yellow to green (6 hours)."""
    key = normalize_priority(priority)
    text = escape(PRIORITY_DESCRIPTIONS[key], quote=True)
    return f'<i class="priority-dot priority-{key}" title="{text}" aria-label="{text}"></i>'


@dataclass
class Offer:
    """One published price row with what the home screen shows about it."""
    row: Dict[str, Any]
    seller: str
    title: str
    stock: str
    site: str
    price: Decimal
    target: Decimal
    low: Decimal
    high: Decimal
    hit: bool
    warehouse: bool
    checked_at: str
    points: list = field(default_factory=list)

    @property
    def gap_share(self) -> Decimal:
        """How far above (or below, negative) the target the price is, as a share of the target."""
        return (self.price - self.target) / self.target if self.target else Decimal("0")


def build_offers(rows: List[Dict[str, Any]], state: Dict[str, Any]) -> List[Offer]:
    histories = load_histories(DATABASE_PATH, offer_index(state), rows)
    offers = []
    for row, points in zip(rows, histories):
        price = parse_turkish_money(row.get("price"))
        if price is None:
            continue
        target = parse_turkish_money(row.get("target")) or Decimal("0")
        seller = repair_mojibake(row.get("seller") or "-")
        full_title = repair_mojibake(row.get("product_title") or "-").strip() or "-"
        stock = STOCK_SUFFIX.search(full_title)
        checked_at = str(row.get("price_checked_at") or "")
        moment = _parse_local_time(checked_at) or datetime.now().astimezone()
        offers.append(Offer(
            row=row, seller=seller, title=STOCK_SUFFIX.sub("", full_title) or full_title, stock=stock.group(1) if stock else "",
            site=site_theme_class(seller), price=price, target=target,
            low=parse_turkish_money(row.get("min_price")) or price, high=parse_turkish_money(row.get("max_price")) or price,
            hit=is_target_hit(row), warehouse=parse_bool(row.get("is_warehouse"), default=False), checked_at=checked_at,
            points=with_current(points, price, moment),
        ))
    return offers


def variant_labels(titles: List[str]) -> List[List[str]]:
    """What differs between the results of one watch (storage, color), so equal-looking tiles can be told apart."""
    segments = [[part for part in VARIANT_SPLIT.split(title) if part] for title in titles]
    common = [part for part in segments[0] if all(part in other for other in segments)] if segments else []
    if not common:  # nothing in common: a tag would only repeat the whole title
        return [[] for _ in titles]
    return [[part for part in parts if part not in common][:MAX_VARIANT_TAGS] for parts in segments]


def lira(value: Decimal) -> str:
    return format_tl(value, with_currency=True)


def price_parts(value: Decimal) -> str:
    return f"{format_tl(value)}<small>TL</small>"


def percent_text(value: float) -> str:
    return f"{abs(value):.1f}".replace(".", ",")


def change_chip(offer: Offer) -> str:
    """Price movement since the first recorded point: lower is good (green), higher is pink."""
    if len(offer.points) < 2:
        return "<span class='ov-chg flat'>yeni</span>"
    first = offer.points[0][1]
    change = (float(offer.price) - first) / first * 100 if first else 0.0
    if abs(change) < 0.05:
        return "<span class='ov-chg flat'>sabit</span>"
    direction, symbol = ("up", "▲") if change > 0 else ("down", "▼")
    since = escape(offer.points[0][0].strftime("%d.%m.%Y"), quote=True)
    return f"<span class='ov-chg {direction}' title='İlk kayıtlı fiyata göre ({since})'>{symbol} %{percent_text(change)}</span>"


def ago_html(value: str) -> str:
    return f"<span class='ov-ago'>{escape(relative_time_text(value))}</span>"


def site_dot(offer: Offer) -> str:
    depo = "<span class='ov-depo'>DEPO</span>" if offer.warehouse else ""
    return f"<span class='ov-site'>{escape(offer.seller)}</span>{depo}"


def detail_html(offer: Offer) -> str:
    """Everything known about one offer; shown in the sheet that opens when its card is tapped."""
    proximity = min(100, int(offer.target / offer.price * 100)) if offer.price else 0
    gap = abs(offer.price - offer.target)
    gap_chip = (f"<span class='ov-chg down'>hedefin {escape(lira(gap))} altında</span>" if offer.hit
                else f"<span class='ov-chg flat'>hedefe {escape(lira(gap))} var</span>")
    facts = [("En düşük", lira(offer.low)), ("En yüksek", lira(offer.high)), ("Satıcı", offer.seller)]
    if offer.stock:
        facts.append(("Stok", f"{offer.stock} adet"))
    cells = "".join(f"<div class='ov-fact'><span>{escape(label)}</span><b>{escape(text)}</b></div>" for label, text in facts)
    if not offer.warehouse:
        interval = PRIORITY_LABELS[normalize_priority(offer.row.get("priority"))]
        cells += f"<div class='ov-fact'><span>Tarama sıklığı</span><b>{priority_dot(offer.row.get('priority'))}{escape(interval)}</b></div>"
    cells += f"<div class='ov-fact'><span>Son güncelleme</span><b>{escape(relative_time_text(offer.checked_at))}</b></div>"
    if len(offer.points) > 1:
        cells += (f"<div class='ov-fact'><span>İlk kayıtlı fiyat</span><b>{lira(Decimal(str(offer.points[0][1])))}</b></div>"
                  f"<div class='ov-fact'><span>Takip başlangıcı</span><b>{offer.points[0][0].strftime('%d.%m.%Y')}</b></div>")
    chart = (detail_chart(offer.points, float(offer.target)) if len(offer.points) > 1
             else "<p class='ov-note'>Fiyat geçmişi ilk değişiklikten sonra çizilir.</p>")
    product_url = str(offer.row.get("product_url") or "").strip()
    link_html = (f"<a class='ov-d-link' href='{escape(product_url, quote=True)}' target='_blank' rel='noopener noreferrer'>Ürüne git →</a>"
                 if product_url else "")
    depo = "<span class='ov-depo'>DEPO</span>" if offer.warehouse else ""
    return (
        f"<div class='ov-d-top'><div class='ov-av'>{escape(offer.seller[:1].upper())}</div><h3>{escape(offer.title)}{depo}</h3>{link_html}"
        "<button type='button' class='ov-x' data-close aria-label='Kapat'>×</button></div>"
        f"<div class='ov-d-price'><b>{price_parts(offer.price)}</b>{change_chip(offer)}{gap_chip}</div>"
        f"<div class='ov-meter'><div class='ov-meter-track'><div class='ov-meter-fill' style='width:{proximity}%'></div></div>"
        f"<p><span>Hedef {lira(offer.target)}</span><span>%{proximity} yakınlık</span></p></div>"
        f"<div class='ov-facts'>{cells}</div><div class='ov-chartbox'>{chart}</div>"
    )


def card_attributes(offer: Offer) -> str:
    return f"data-open tabindex='0' role='button' aria-label='{escape(offer.title, quote=True)}'"


def render_deal(offer: Offer) -> str:
    share = float(-offer.gap_share * 100)
    return (
        f"<article class='ov-deal {offer.site}{' ov-deal-depo' if offer.warehouse else ''}' {card_attributes(offer)}>"
        f"<div class='ov-deal-top'><span class='ov-deal-who'>{site_dot(offer)}</span>{ago_html(offer.checked_at)}</div>"
        f"<h3 title='{escape(offer.title, quote=True)}'>{escape(offer.title)}</h3>"
        f"<div class='ov-deal-foot'><div class='ov-price'>{price_parts(offer.price)}</div>"
        f"<div class='ov-deal-side'>{sparkline(offer.points, SPOT_SIZE)}"
        f"<div class='ov-off'>−%{percent_text(share)}<small>hedefin altında</small></div></div></div>"
        f"<div class='ov-detail' hidden>{detail_html(offer)}</div></article>"
    )


def render_tile(offer: Offer, tags: Optional[List[str]] = None, index: int = 0) -> str:
    depo = "<span class='ov-depo'>DEPO</span>" if offer.warehouse else ""
    tag_html = f"<div class='ov-vars'>{depo}{''.join(f'<em>{escape(tag)}</em>' for tag in tags or [])}</div>" if tags or depo else ""
    return (
        f"<article class='ov-tile {offer.site}{' ov-tile-depo' if offer.warehouse else ''}' data-site='{offer.site}' style='--n:{index}' {card_attributes(offer)}>"
        f"<div class='ov-av'>{escape(offer.seller[:1].upper())}</div>"
        f"<div class='ov-tx'><h4 title='{escape(offer.title, quote=True)}'>{escape(offer.title)}</h4>{tag_html}"
        f"<div class='ov-price'>{price_parts(offer.price)}</div></div>"
        f"<div class='ov-side'>{sparkline(offer.points)}{change_chip(offer)}{ago_html(offer.checked_at)}</div>"
        f"<div class='ov-detail' hidden>{detail_html(offer)}</div></article>"
    )


def group_offers(offers: List[Offer]):
    """Single results first (closest to target first); a watch with several results gets its own section."""
    grouped: Dict[str, Dict[str, Any]] = {}
    singles = []
    for offer in offers:
        key = str(offer.row.get("search_group") or "").strip()
        if not key:
            singles.append(offer)
            continue
        group = grouped.setdefault(key, {"label": str(offer.row.get("search_group_label") or "").strip(), "offers": []})
        group["offers"].append(offer)
    sections = []
    for group in grouped.values():
        if len(group["offers"]) < 2:
            singles.extend(group["offers"])
            continue
        group["offers"].sort(key=lambda item: item.gap_share)
        sections.append((group["label"] or "Arama sonuçları", group["offers"]))
    singles.sort(key=lambda item: item.gap_share)
    sections.sort(key=lambda item: item[1][0].gap_share)
    return singles, sections


def render_watch_pane(offers: List[Offer]) -> str:
    sites = sorted({(offer.site, offer.seller) for offer in offers}, key=lambda item: item[1].casefold())
    chips = ("<div class='ov-filters'><button type='button' class='ov-chip' data-filter='all' aria-pressed='true'>Tümü</button>"
             + "".join(f"<button type='button' class='ov-chip {site}' data-filter='{site}' aria-pressed='false'><i></i>{escape(name)}</button>"
                       for site, name in sites) + "</div>")
    if not offers:
        return chips + "<p class='ov-empty'>Hedef üstünde bekleyen ürün yok.</p>"
    singles, sections = group_offers(offers)
    body = f"<div class='ov-tiles'>{''.join(render_tile(offer, None, number) for number, offer in enumerate(singles))}</div>" if singles else ""
    for label, members in sections:
        tags = variant_labels([member.title for member in members])
        body += (f"<section class='ov-group'><h3 class='ov-group-head {members[0].site}'><i></i><b title='{escape(label, quote=True)}'>{escape(label)}</b>"
                 f"<span>{len(members)} sonuç</span></h3><div class='ov-tiles'>"
                 f"{''.join(render_tile(member, tag, number) for number, (member, tag) in enumerate(zip(members, tags)))}</div></section>")
    return chips + body


def render_stock_pane(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return "<p class='ov-empty'>Stok dışında izlenen ürün yok.</p>"
    items = []
    for row in sorted(rows, key=lambda item: (repair_mojibake(str(item.get("seller") or "")).casefold(),
                                              repair_mojibake(str(item.get("product_title") or "")).casefold())):
        seller = repair_mojibake(row.get("seller") or "-")
        title = repair_mojibake(row.get("product_title") or "-").strip() or "-"
        url = str(row.get("product_url") or "").strip()
        name = (f"<a href='{escape(url, quote=True)}' target='_blank' rel='noopener noreferrer'>{escape(title)}</a>" if url else escape(title))
        items.append(
            f"<li class='ov-row {site_theme_class(seller)}'><div class='ov-av'>{escape(seller[:1].upper())}</div>"
            f"<div class='ov-tx'><h4>{name}</h4><span>{escape(seller)} · {escape(repair_mojibake(row.get('reason') or 'Stokta yok'))} · hedef "
            f"{escape(display_tl(row.get('target', '-')))}</span></div>{ago_html(row.get('checked_at'))}</li>")
    return f"<ul class='ov-rows'>{''.join(items)}</ul>"


def render_telegram_pane(status: Dict[str, Any]) -> str:
    items = status.get("recent_notifications") if isinstance(status.get("recent_notifications"), list) else []
    rows = []
    for item in items[:5]:
        if not isinstance(item, dict):
            continue
        keyword = escape(str(item.get("keyword") or "-"))
        url = str(item.get("url") or "").strip()
        title = (f"<a href='{escape(url, quote=True)}' target='_blank' rel='noopener noreferrer'>{keyword}</a>" if url else keyword)
        rows.append(f"<li class='ov-row'><div class='ov-tx'><h4>{title}</h4>"
                    f"<span>{escape(str(item.get('channel') or '-'))} · {escape(str(item.get('created_at') or '-'))}</span>"
                    f"<p>{escape(str(item.get('message') or ''))}</p></div></li>")
    return f"<ul class='ov-rows'>{''.join(rows)}</ul>" if rows else "<p class='ov-empty'>Henüz Telegram bildirimi yok.</p>"


def render_errors(errors: List[Dict[str, Any]]) -> str:
    if not errors:
        return "<div class='ov-ok'>✓ Son 24 saatte hata yok</div>"
    items = []
    for detail in errors:
        links = "".join(
            "<div class='ov-failed'><span>Hatalı link</span>"
            f"<a href='{escape(item['url'], quote=True)}' target='_blank' rel='noopener noreferrer'>{escape(item['url'][:93] + '...' if len(item['url']) > 96 else item['url'])}</a>"
            f"<em>{escape(item['message'])}</em></div>"
            for item in detail["failed_links"]
        )
        open_link = (f"<a href='{escape(detail['url'], quote=True)}' target='_blank' rel='noopener noreferrer'>Linki aç</a>"
                     if detail["url"] else "")
        items.append(f"<li><strong>{escape(detail['title'])}</strong><span>{escape(detail['meta'])}</span>"
                     f"<em>Hata: {escape(detail['message'])}</em>{links}{open_link}</li>")
    return (f"<section class='ov-errors'><h2 class='ov-sec'>Hatalar <small>son 24 saat · {len(errors)}</small></h2>"
            f"<ul>{''.join(items)}</ul></section>")


def render_summary(payload: Dict[str, Any], state: Dict[str, Any], telegram_status: Dict[str, Any], errors: List[Dict[str, Any]]) -> str:
    rows = [row for row in payload.get("rows", []) if isinstance(row, dict)] if isinstance(payload.get("rows"), list) else []
    stock_rows = [row for row in payload.get("stock_rows", []) if isinstance(row, dict)] if isinstance(payload.get("stock_rows"), list) else []
    if not rows and not stock_rows:
        recent = render_telegram_pane(telegram_status) if telegram_status.get("recent_notifications") else ""
        return ("<section class='ov-hero'><div><h1>Özet Tablo</h1><p>İlk kontrol döngüsü tamamlandığında son fiyat tablosu burada görünecek.</p></div></section>"
                + (f"<h2 class='ov-sec'>Telegram</h2>{recent}" if recent else "") + render_errors(errors))
    offers = build_offers(rows, state)
    deals = sorted((offer for offer in offers if offer.hit), key=lambda offer: offer.gap_share)
    watching = [offer for offer in offers if not offer.hit]
    cycle = escape(duration_text(payload.get("cycle_duration_seconds"), payload.get("cycle_duration_minutes") or "-"))
    status = (f"<div class='ov-status'><span class='ov-pulse'></span>Çevrim <b>{cycle}</b> · Son güncelleme "
              f"<b>{escape(relative_time_text(payload.get('checked_at')))}</b></div>")
    numbers = (f"<div class='ov-nums'><div class='ov-num'><b>{len(offers)}</b><span>takipte ürün</span></div>"
               f"<div class='ov-num'><b>{len(stock_rows)}</b><span>stokta yok</span></div>"
               f"<div class='ov-num deal'><b>{len(deals)}</b><span>fırsat</span></div></div>")
    spot = (f"<div class='ov-spot'>{''.join(render_deal(offer) for offer in deals)}</div>" if deals
            else "<p class='ov-empty'>Şu anda hedef fiyatın altına düşen ürün yok.</p>")
    telegram_count = min(5, len(telegram_status.get("recent_notifications") or [])) if isinstance(telegram_status.get("recent_notifications"), list) else 0
    tabs = (
        "<div class='ov-tabs' role='tablist'>"
        f"<button type='button' class='ov-tab' role='tab' data-tab='watch' aria-selected='true'>Takipte <small>{len(watching)}</small></button>"
        f"<button type='button' class='ov-tab' role='tab' data-tab='stock' aria-selected='false'>Stokta yok <small>{len(stock_rows)}</small></button>"
        f"<button type='button' class='ov-tab' role='tab' data-tab='tg' aria-selected='false'>Telegram <small>{telegram_count}</small></button></div>"
    )
    panes = (f"<div class='ov-pane' data-pane='watch'>{render_watch_pane(watching)}</div>"
             f"<div class='ov-pane' data-pane='stock' hidden>{render_stock_pane(stock_rows)}</div>"
             f"<div class='ov-pane' data-pane='tg' hidden>{render_telegram_pane(telegram_status)}</div>")
    return (f"{status}<section class='ov-hero'><div><h1>Özet Tablo</h1>"
            "<p>Hedef fiyatın altına inen ürünler en önde. Detay için karta dokun.</p></div>"
            f"{numbers}</section><h2 class='ov-sec'>Fırsatlar</h2>{spot}{tabs}{panes}{render_errors(errors)}")

# -- errors -------------------------------------------------------------------------


def clean_error_message(error_text) -> str:
    text = repair_mojibake(error_text or "").strip()
    if not text:
        return "Hata ayrıntısı kaydedilmemiş."
    parts = [part.strip() for part in text.split("|") if part.strip()]
    non_url_parts = [part for part in parts if not part.startswith(("http://", "https://"))]
    if non_url_parts:
        text = " | ".join(non_url_parts)
    text = re.sub(r"\s+", " ", re.sub(r"https?://\S+", "[link]", text)).strip()
    return text or "Hata ayrıntısı kaydedilmemiş."


def _first_url(text) -> str:
    match = re.search(r"https?://\S+", str(text or ""))
    return match.group(0).rstrip(".,;)]}") if match else ""


def error_link_details(error_text) -> List[Dict[str, str]]:
    """Variant errors name their own failing link ("url | reason; url | reason")."""
    details, seen = [], set()
    for segment in [item.strip() for item in repair_mojibake(error_text or "").split(";") if item.strip()]:
        url = _first_url(segment)
        if url and (url, clean_error_message(segment)) not in seen:
            seen.add((url, clean_error_message(segment)))
            details.append({"url": url, "message": clean_error_message(segment)})
    return details


def collect_errors(state: Dict[str, Any], hours: int = 24) -> List[Dict[str, Any]]:
    """Watches whose last read failed within the last day and since Hermes started.

    A failure from before a restart is not shown: it is history, and the watch is
    read again when its turn comes (low-priority watches only once an hour or less).
    """
    cutoff = max(datetime.now().astimezone() - timedelta(hours=hours), PROCESS_STARTED_AT)
    errors, seen = [], set()
    for key, entry in state.items() if isinstance(state, dict) else []:
        if key == "_meta" or not isinstance(entry, dict) or not entry.get("last_error"):
            continue
        checked_at = parse_iso_datetime(entry.get("last_checked_at"))
        if not checked_at or checked_at.astimezone() < cutoff:
            continue
        name = str(entry.get("watch_name") or "").strip()
        url = str(entry.get("configured_url") or entry.get("url") or "").strip()
        display_name = name or url or "Takip"
        site = str(entry.get("site") or "").strip()
        meta = f"Takip edilen: {display_name}"
        if name and is_search_url(url):
            meta += f" · Aranan keyword: {name}"
        detail = {
            "title": f"{site_label(site) if site else 'Ürün kontrolü'}: {display_name}",
            "meta": meta,
            "message": clean_error_message(entry.get("last_error")),
            "url": url or _first_url(entry.get("last_error")),
            "failed_links": error_link_details(entry.get("last_error"))[:4],
            "checked_at": checked_at,
        }
        identity = (detail["title"], detail["message"], detail["url"])
        if identity not in seen:
            seen.add(identity)
            errors.append(detail)
    return sorted(errors, key=lambda item: item["checked_at"], reverse=True)


def telegram_error_count_24h() -> int:
    payload = load_json(TELEGRAM_ERROR_EVENTS_PATH, [])
    cutoff = datetime.now().astimezone() - timedelta(hours=24)
    count = 0
    for item in payload if isinstance(payload, list) else []:
        created = _parse_local_time(item.get("created_at")) if isinstance(item, dict) else None
        if created and created.astimezone() >= cutoff:
            count += 1
    return count


# -- pages ----------------------------------------------------------------------------

LIVE_REFRESH_NOTE = "Sayfa açıkken veriler kendiliğinden güncellenir."


# Seconds between in-place refreshes; the old full reload was 60 s.
LIVE_INTERVAL_SECONDS = {"live/dashboard": 15, "live/statistics": 60}

SHEET = ("<div class='ov-modal' id='ov-modal' hidden><div class='ov-sheet' id='ov-sheet' role='dialog' "
         "aria-modal='true' aria-label='Ürün ayrıntısı'></div></div>")


def live_region(base: str, endpoint: str, html: str) -> str:
    """A block whose contents the browser refreshes in place (see assets.LIVE_SCRIPT)."""
    return (f"<div id='live-region' data-live-url='{escape(link(base, endpoint), quote=True)}' "
            f"data-live-interval='{LIVE_INTERVAL_SECONDS[endpoint.split('?')[0]]}'>{html}</div>")


def dashboard_live_html(base: str) -> str:
    """Everything on the summary page that changes while it is open."""
    try:
        payload = load_json(SUMMARY_PATH, {})
        state = load_json(STATE_PATH, {})
    except (RuntimeError, sqlite3.Error):
        error = {"title": "Takip hafızası okunamadı", "meta": "Mevcut kayıtlar korunuyor",
                 "message": "Takip hafızasının incelenmesi gerekiyor. Ayarlar sayfası açık.",
                 "url": "", "failed_links": [], "checked_at": datetime.now().astimezone()}
        return render_summary({}, {}, {}, [error])
    telegram_status = load_json(TELEGRAM_STATUS_PATH, {})
    state = state if isinstance(state, dict) else {}
    errors = collect_errors(state)
    for item in Diagnostics(DATABASE_PATH).active():
        errors.append({"title": "Hermes çalışma uyarısı", "meta": item["recovery"],
            "message": clean_error_message(item["detail"]), "url": "", "failed_links": [],
            "checked_at": datetime.fromtimestamp(item["updated"]).astimezone()})
    return render_summary(payload if isinstance(payload, dict) else {}, state,
                          telegram_status if isinstance(telegram_status, dict) else {}, errors)


def render_dashboard_page(base: str, params: Dict[str, List[str]], config_error: str = "") -> bytes:
    notice = ""
    for key in ("settings",):
        status = params.get(key, [""])[0]
        if status in {"ok", "fail"}:
            notice = render_notice(status, params.get("msg", [""])[0])
            break
    if config_error:
        notice += (f"<p class='notice notice-fail config-error'>Ayarlarda hata var, izleme durdu: {escape(config_error)} "
                   f"<a href='{escape(link(base, 'settings'), quote=True)}'>Ayarları düzelt</a></p>")
    body = notice + live_region(base, "live/dashboard", dashboard_live_html(base))
    return render_page(base, "dashboard", "Hermes", body, body_class="public ov ov-first", refresh_seconds=60,
                       scripts=live_script_tag(base) + overview_script_tag(base), after_main=SHEET)


def live_script_tag(base: str) -> str:
    return f"<script src='{escape(link(base, 'live.js'), quote=True)}?v={escape(APP_VERSION)}' defer></script>"


def overview_script_tag(base: str) -> str:
    return f"<script src='{escape(link(base, 'overview.js'), quote=True)}?v={escape(APP_VERSION)}' defer></script>"
