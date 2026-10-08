"""Summary table, statistics, errors and Telegram cards."""

import re
from datetime import datetime, timedelta
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from html import escape
from typing import Any, Dict, List, Optional

from ..constants import (
    APP_VERSION,
    PRIORITY_DESCRIPTIONS,
    STATE_PATH,
    SUMMARY_PATH,
    TELEGRAM_ERROR_EVENTS_PATH,
    TELEGRAM_STATUS_PATH,
    normalize_priority,
)
from ..storage import load_json
from ..utils import PROCESS_STARTED_AT, is_search_url, parse_bool, parse_iso_datetime, repair_mojibake, site_label
from .pages import link, render_notice, render_page

TABLE_TITLE_MAX_LENGTH = 60
GROUP_TITLE_MAX_LENGTH = 70
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


def display_tl_range(value, fallback_min="-", fallback_max="-") -> str:
    parts = [part.strip() for part in str(value or f"{fallback_min} / {fallback_max}").split("/") if part.strip()]
    return " / ".join(display_tl(part) for part in parts) if parts else "-"


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


def relative_minutes_text(value) -> str:
    parsed = parse_iso_datetime(str(value or ""))
    if not parsed:
        return "-"
    return f"{max(0, int((datetime.now().astimezone() - parsed.astimezone()).total_seconds() // 60))} dk önce"


def duration_text(seconds_value, fallback="-") -> str:
    if seconds_value in (None, ""):
        return str(fallback or "-")
    try:
        total_seconds = max(0, int(round(float(seconds_value))))
    except (TypeError, ValueError):
        return str(fallback or "-")
    minutes, seconds = divmod(total_seconds, 60)
    return f"{minutes} dk {seconds} sn" if minutes else f"{seconds} sn"


def shortened_title(value, max_length: int, ellipsis: bool = True) -> tuple[str, str]:
    """A compact label plus the full text for its tooltip."""
    full_title = repair_mojibake(value or "-").strip() or "-"
    if len(full_title) <= max_length:
        return full_title, full_title
    visible = f"{full_title[:max_length - 3].rstrip()}..." if ellipsis else full_title[:max_length].rstrip()
    return visible, full_title


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


def difference_sort_value(row: Dict[str, Any]) -> Decimal:
    cleaned = re.sub(r"[^0-9,.-]", "", repair_mojibake(str(row.get("difference") or "0"))).strip()
    if not cleaned:
        return Decimal("0")
    cleaned = cleaned.replace(".", "").replace(",", ".") if "," in cleaned else cleaned.replace(".", "")
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return Decimal("0")


def row_sort_key(row: Dict[str, Any]):
    return (repair_mojibake(str(row.get("seller") or "")).casefold(), difference_sort_value(row),
            repair_mojibake(str(row.get("product_title") or "")).casefold())


# -- price table ----------------------------------------------------------------------


def priority_dot(priority) -> str:
    """Round mark at the start of a row: red (every cycle) through orange and yellow to green (6 hours)."""
    key = normalize_priority(priority)
    text = escape(PRIORITY_DESCRIPTIONS[key], quote=True)
    return f'<i class="priority-dot priority-{key}" title="{text}" aria-label="{text}"></i>'


def render_table_row(row: Dict[str, Any]) -> str:
    seller_text = repair_mojibake(row.get("seller") or "-")
    visible_title, full_title = shortened_title(row.get("product_title"), TABLE_TITLE_MAX_LENGTH, ellipsis=False)
    is_warehouse = parse_bool(row.get("is_warehouse"), default=False)
    warehouse_tag = '<strong class="warehouse-tag">DEPO</strong>' if is_warehouse else ""
    dot = "" if is_warehouse else priority_dot(row.get("priority"))
    title_html = f'<span class="product-title">{dot}{warehouse_tag}{escape(visible_title)}</span>'
    product_url = str(row.get("product_url") or "").strip()
    if product_url:
        title_html = f'<a href="{escape(product_url, quote=True)}" target="_blank" rel="noopener noreferrer">{title_html}</a>'
    classes = [site_theme_class(seller_text)] + (["deal-row"] if is_target_hit(row) else [])
    price_range = display_tl_range(row.get("price_range"), row.get("min_price", "-"), row.get("max_price", "-"))
    return (
        f'<tr class="{" ".join(classes)}"><td data-label="Satıcı" class="seller-cell">{escape(seller_text)}</td>'
        f'<td data-label="Ürün" class="product-cell" title="{escape(full_title, quote=True)}">{title_html}</td>'
        f'<td data-label="Güncel fiyat" class="price-cell">{escape(display_tl(row.get("price", "-")))}</td>'
        f'<td data-label="Hedef fiyat" class="target-cell">{escape(display_tl(row.get("target", "-")))}</td>'
        f'<td data-label="Fark" class="diff-cell">{escape(display_tl(row.get("difference", "-"), signed=True))}</td>'
        f'<td data-label="Min / Maks" class="range-cell">{escape(price_range)}</td>'
        f'<td data-label="Son güncelleme" class="updated-cell">{escape(relative_minutes_text(row.get("price_checked_at")))}</td></tr>'
    )


def render_rows_table(rows: List[Dict[str, Any]], empty_text: str) -> str:
    body = "".join(render_table_row(row) for row in rows) if rows else f"<tr class='empty-row'><td colspan='7'>{escape(empty_text)}</td></tr>"
    return (
        "<div class='table-wrap'><table class='price-summary-table'><thead><tr><th>Satıcı</th><th>Ürün Adı</th>"
        "<th>Güncel<br>fiyat</th><th>Hedef<br>fiyat</th><th>Fark</th><th>Min / Maks</th><th>Son<br>güncelleme</th></tr>"
        f"</thead><tbody>{body}</tbody></table></div>"
    )


def split_result_groups(rows: List[Dict[str, Any]]):
    """Rows of one watch with several results collapse under the watch name."""
    grouped: Dict[str, Dict[str, Any]] = {}
    ungrouped = []
    for row in rows:
        group_key = str(row.get("search_group") or "").strip()
        if not group_key:
            ungrouped.append(row)
            continue
        group = grouped.setdefault(group_key, {"label": str(row.get("search_group_label") or "").strip(), "rows": []})
        group["rows"].append(row)
    groups = []
    for group in grouped.values():
        if len(group["rows"]) < 2:
            ungrouped.extend(group["rows"])
            continue
        group["rows"].sort(key=row_sort_key)
        groups.append((group["label"] or "Arama sonuçları", group["rows"]))
    ungrouped.sort(key=row_sort_key)
    groups.sort(key=lambda item: (row_sort_key(item[1][0]), item[0].casefold()))
    return ungrouped, groups


def render_group(label: str, rows: List[Dict[str, Any]]) -> str:
    visible, full = shortened_title(label, GROUP_TITLE_MAX_LENGTH)
    return (f"<details class='search-result-group' data-key='group:{escape(full, quote=True)}'>"
            f"<summary><strong title='{escape(full, quote=True)}'>{escape(visible)}</strong>"
            f"<span>{len(rows)} sonuç</span></summary>{render_rows_table(rows, '')}</details>")


def render_table_section(title: str, rows, empty_text: str, extra_class: str = "", collapse: bool = False) -> str:
    if not rows or not collapse:
        body = render_rows_table(rows, empty_text)
    else:
        ungrouped, groups = split_result_groups(rows)
        body = (render_rows_table(ungrouped, empty_text) if ungrouped else "") + "".join(
            render_group(label, group_rows) for label, group_rows in groups)
    return f"<div class='table-section {extra_class}'><h3>{escape(title)}</h3>{body}</div>"


def render_stock_section(rows: List[Dict[str, Any]]) -> str:
    def stock_row(row):
        seller_text = repair_mojibake(row.get("seller") or "-")
        visible, full = shortened_title(row.get("product_title"), TABLE_TITLE_MAX_LENGTH, ellipsis=False)
        product_url = str(row.get("product_url") or "").strip()
        label = (f'<a href="{escape(product_url, quote=True)}" target="_blank" rel="noopener noreferrer"><span>{escape(visible)}</span></a>'
                 if product_url else f"<span>{escape(visible)}</span>")
        return (f'<tr class="{site_theme_class(seller_text)} stock-missing-row"><td data-label="Satıcı" class="seller-cell">{escape(seller_text)}</td>'
                f'<td data-label="Ürün" class="product-cell" title="{escape(full, quote=True)}">{label}</td>'
                f'<td data-label="Hedef" class="target-cell">{escape(display_tl(row.get("target", "-")))}</td>'
                f'<td data-label="Durum" class="diff-cell">{escape(repair_mojibake(row.get("reason") or "Stokta yok"))}</td>'
                f'<td data-label="Son güncelleme" class="updated-cell">{escape(relative_time_text(row.get("checked_at")))}</td></tr>')

    def table(body_rows, empty_text=""):
        body = "".join(stock_row(row) for row in body_rows)
        if not body and empty_text:
            body = f"<tr class='empty-row'><td colspan='5'>{escape(empty_text)}</td></tr>"
        return ("<div class='table-wrap'><table class='stock-table'><thead><tr><th>Satıcı</th><th>Ürün Adı</th><th>Hedef</th><th>Durum</th><th>Son<br>güncelleme</th>"
                f"</tr></thead><tbody>{body}</tbody></table></div>")

    if not rows:
        body = table([], "Stok dışında izlenen ürün yok.")
    else:
        by_site: Dict[str, list] = {}
        for row in rows:
            by_site.setdefault(repair_mojibake(row.get("seller") or "Diğer"), []).append(row)
        body = "".join(
            f"<details class='search-result-group stock-site-group' data-key='stock:{escape(seller, quote=True)}'><summary><strong>{escape(seller)}</strong>"
            f"<span>{len(site_rows)} ürün</span></summary>{table(site_rows)}</details>"
            for seller, site_rows in sorted(by_site.items(), key=lambda item: item[0].casefold())
        )
    return f"<div class='table-section stock-section'><h3>Stokta Olmayanlar</h3>{body}</div>"


def render_summary(payload: Dict[str, Any]) -> str:
    rows = [row for row in payload.get("rows", []) if isinstance(row, dict)] if isinstance(payload.get("rows"), list) else []
    stock_rows = [row for row in payload.get("stock_rows", []) if isinstance(row, dict)] if isinstance(payload.get("stock_rows"), list) else []
    if not rows and not stock_rows:
        return ("<section class='summary-panel'><div class='summary-head'><h2>Özet Tablo</h2><span>Henüz tablo yok</span></div>"
                "<p class='empty-table'>İlk kontrol döngüsü tamamlandığında son fiyat tablosu burada görünecek.</p></section>")
    deal_rows = [row for row in rows if is_target_hit(row)]
    watch_rows = [row for row in rows if not is_target_hit(row)]
    sections = (
        render_table_section("Hedef Fiyat Altındaki Fırsatlar", deal_rows, "Şu anda hedef fiyatın altına düşen ürün yok.", "deals-section")
        + render_table_section("Hedefin Üstünde Kalan Ürünler", watch_rows, "Hedef üstünde bekleyen ürün yok.", collapse=True)
        + render_stock_section(stock_rows)
    )
    counts = f"{len(rows)} ürün · {len(deal_rows)} fırsat · {len(stock_rows)} stokta yok"
    return f"<section class='summary-panel'><div class='summary-head'><h2>Özet Tablo</h2><span>{escape(counts)}</span></div>{sections}</section>"


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


def render_error_card(errors: List[Dict[str, Any]]) -> str:
    items = []
    for detail in errors:
        links = "".join(
            "<div class='failed-link'><span>Hatalı link</span>"
            f"<a href='{escape(item['url'], quote=True)}' target='_blank' rel='noopener noreferrer'>{escape(item['url'][:93] + '...' if len(item['url']) > 96 else item['url'])}</a>"
            f"<em>{escape(item['message'])}</em></div>"
            for item in detail["failed_links"]
        )
        open_link = (f"<a href='{escape(detail['url'], quote=True)}' target='_blank' rel='noopener noreferrer'>Linki aç</a>"
                     if detail["url"] else "")
        items.append(f"<li><strong>{escape(detail['title'])}</strong><span>{escape(detail['meta'])}</span>"
                     f"<em>Hata: {escape(detail['message'])}</em>{links}{open_link}</li>")
    body = "".join(items) or "<li class='empty-error'>Son 24 saatte hata yok.</li>"
    error_class = " status-error" if errors else ""
    return (f"<section class='card error-card public-error-card{error_class}'><span>Hata sayısı (son 24 saat)</span>"
            f"<strong>{len(errors)}</strong><ul>{body}</ul></section>")


# -- Telegram -------------------------------------------------------------------------


def telegram_error_count_24h() -> int:
    payload = load_json(TELEGRAM_ERROR_EVENTS_PATH, [])
    cutoff = datetime.now().astimezone() - timedelta(hours=24)
    count = 0
    for item in payload if isinstance(payload, list) else []:
        created = _parse_local_time(item.get("created_at")) if isinstance(item, dict) else None
        if created and created.astimezone() >= cutoff:
            count += 1
    return count


def render_telegram_recent(status: Dict[str, Any]) -> str:
    items = status.get("recent_notifications") if isinstance(status.get("recent_notifications"), list) else []
    rows = []
    for item in items[:5]:
        if not isinstance(item, dict):
            continue
        keyword = escape(str(item.get("keyword") or "-"))
        url = str(item.get("url") or "").strip()
        title_html = (f"<a href='{escape(url, quote=True)}' target='_blank' rel='noopener noreferrer'>{keyword}</a>"
                      if url else f"<strong>{keyword}</strong>")
        rows.append(f"<li>{title_html}<span>{escape(str(item.get('channel') or '-'))} · {escape(str(item.get('created_at') or '-'))}</span>"
                    f"<em>{escape(str(item.get('message') or ''))}</em></li>")
    if not rows:
        return "<div class='telegram-recent'><h3>Son Telegram Bildirimleri</h3><p>Henüz Telegram bildirimi yok.</p></div>"
    return f"<div class='telegram-recent'><h3>Son Telegram Bildirimleri</h3><ul>{''.join(rows)}</ul></div>"


# -- pages ----------------------------------------------------------------------------

LIVE_REFRESH_NOTE = "Sayfa açıkken veriler kendiliğinden güncellenir."


# Seconds between in-place refreshes; the old full reload was 60 s.
LIVE_INTERVAL_SECONDS = {"live/dashboard": 15, "live/statistics": 60}


def live_region(base: str, endpoint: str, html: str) -> str:
    """A block whose contents the browser refreshes in place (see assets.LIVE_SCRIPT)."""
    return (f"<div id='live-region' data-live-url='{escape(link(base, endpoint), quote=True)}' "
            f"data-live-interval='{LIVE_INTERVAL_SECONDS[endpoint.split('?')[0]]}'>{html}</div>")


def dashboard_live_html(base: str) -> str:
    """Everything on the summary page that changes while it is open."""
    payload = load_json(SUMMARY_PATH, {})
    payload = payload if isinstance(payload, dict) else {}
    cycle = escape(duration_text(payload.get("cycle_duration_seconds"), payload.get("cycle_duration_minutes") or "-"))
    pills = (
        "<div class='public-cycle-row'>"
        f"<section class='public-cycle-pill'><span>Çevrim süresi</span><strong>{cycle}</strong></section>"
        f"<section class='public-cycle-pill'><span>Son güncelleme</span><strong>{escape(relative_time_text(payload.get('checked_at')))}</strong></section>"
        "</div>"
    )
    telegram_status = load_json(TELEGRAM_STATUS_PATH, {})
    state = load_json(STATE_PATH, {})
    return (pills + render_summary(payload)
            + render_telegram_recent(telegram_status if isinstance(telegram_status, dict) else {})
            + render_error_card(collect_errors(state if isinstance(state, dict) else {})))


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
    return render_page(base, "dashboard", "Hermes", body, refresh_seconds=60, scripts=live_script_tag(base))


def live_script_tag(base: str) -> str:
    return f"<script src='{escape(link(base, 'live.js'), quote=True)}?v={escape(APP_VERSION)}' defer></script>"
