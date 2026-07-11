import os
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .constants import OPTIONS_PATH, PUSHOVER_URL, STATE_PATH, SUMMARY_PATH, TELEGRAM_ERROR_EVENTS_PATH, TELEGRAM_STATUS_PATH
from .logging_utils import log
from .providers import hepsiburada as hepsiburada_provider
from .storage import load_json
from .utils import (
    detect_site_from_url,
    is_amazon_search_url,
    is_hepsiburada_search_url,
    normalize_item_key,
    parse_bool,
    parse_iso_datetime,
    repair_mojibake,
    site_label,
)

WEB_PORT = 8099
ADDON_SLUG = "hermes"
RESET_NOTIFICATIONS_LOCK = threading.Lock()
PRICE_HISTORY_RESET_LOCK = threading.Lock()

DASHBOARD_CSS = """
:root { color-scheme: dark; --bg:#0f1222; --panel:#171a30; --card:#1e2139; --line:#313658; --text:#e8eaf8; --muted:#a6abd1; --accent:#c7a6ff; --accent2:#8ed6d2; --ok:#7fdcb8; --warn:#ffd18a; --bad:#ff9cb5; --blue:#8fb9ff; --blue2:#6f93ff; --head:#262a45; }
* { box-sizing:border-box; } body { margin:0; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; background:radial-gradient(circle at top left,#1f2240,var(--bg) 56%); color:var(--text); font-size:14px; }
main { max-width:1060px; margin:0 auto; padding:28px 18px 44px; } .hero { border:1px solid var(--line); border-radius:22px; padding:22px; background:var(--panel); box-shadow:0 18px 42px rgba(0,0,0,.35); }
p { margin:0; color:var(--muted); line-height:1.5; font-size:13px; }
.badge { display:inline-flex; margin-bottom:12px; color:#16192b; background:linear-gradient(135deg,var(--accent),var(--accent2)); border-radius:18px; padding:9px 15px; font-size:clamp(24px,5vw,42px); line-height:1; letter-spacing:-.04em; font-weight:900; }
.actions { display:flex; flex-wrap:wrap; gap:10px; margin-top:18px; align-items:center; } .inline-form { margin:0; } .button { display:inline-flex; align-items:center; justify-content:center; min-height:40px; padding:0 14px; border-radius:13px; border:1px solid transparent; text-decoration:none; font-weight:800; font-size:13px; cursor:pointer; }
.button.primary { color:#14172a; background:linear-gradient(135deg,var(--accent),var(--accent2)); } .button.secondary { color:var(--text); background:#2a2f4d; border-color:var(--line); } .button.test { color:#eaf0ff; background:linear-gradient(135deg,var(--blue),var(--blue2)); }
.notice { margin-top:14px; padding:11px 13px; border-radius:12px; font-weight:700; font-size:13px; } .notice-ok { color:#c6f7e6; background:rgba(127,220,184,.14); border:1px solid rgba(127,220,184,.38); } .notice-fail { color:#ffd8e3; background:rgba(255,156,181,.14); border:1px solid rgba(255,156,181,.38); }
.grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:11px; margin-top:16px; } .card { border:1px solid var(--line); border-radius:15px; padding:14px; background:var(--card); min-height:82px; } .card span { display:block; color:var(--muted); font-size:12px; margin-bottom:8px; } .card strong { display:block; font-size:19px; line-height:1.18; overflow-wrap:anywhere; }
.card.status-ok { border-color:rgba(127,220,184,.38); background:linear-gradient(135deg,rgba(127,220,184,.12),var(--card) 62%); } .card.status-ok strong { color:var(--ok); } .card.status-warn strong { color:var(--warn); } .card.status-error strong { color:var(--bad); }
.error-card { grid-column:1 / -1; } .error-card ul { display:grid; gap:9px; margin:10px 0 0; padding:0; list-style:none; color:var(--text); } .error-card li { display:grid; gap:6px; padding:10px 12px; border:1px solid rgba(255,156,181,.28); border-radius:12px; background:rgba(255,156,181,.08); font-size:12px; line-height:1.35; overflow-wrap:anywhere; } .error-card li.empty-error { border-color:rgba(127,220,184,.26); background:rgba(127,220,184,.08); color:var(--muted); } .error-card li strong { font-size:13px; color:var(--text); } .error-card li span { margin:0; color:var(--muted); } .error-card li em { color:#ffd8e3; font-style:normal; } .error-card li a { color:#9ec0ff; font-weight:800; text-decoration:none; width:max-content; } .error-card li a:hover { text-decoration:underline; } .failed-link { display:grid; gap:3px; margin-top:4px; padding:8px 10px; border-radius:10px; background:rgba(143,185,255,.10); border:1px solid rgba(143,185,255,.22); } .failed-link span { color:#c7d7ff; font-weight:800; font-size:11px; } .failed-link strong { color:#e8eaf8; font-size:12px; } .failed-link em { color:#cfd6f6; font-size:11px; }
.summary-panel { margin-top:18px; border:1px solid var(--line); border-radius:18px; padding:16px; background:var(--card); } .summary-head { display:flex; align-items:flex-end; justify-content:space-between; gap:12px; margin-bottom:12px; } .summary-head h2 { font-size:18px; margin:0; } .summary-head span { color:var(--muted); font-size:12px; white-space:nowrap; } .table-section + .table-section { margin-top:18px; } .table-section h3 { margin:0 0 9px; font-size:14px; color:#d8dcff; } .deals-section h3 { color:#b7f0dc; }
.telegram-recent { margin-top:14px; border:1px solid rgba(199,166,255,.22); border-radius:14px; padding:13px; background:rgba(15,18,34,.36); } .telegram-recent h3 { margin:0 0 10px; font-size:13px; color:#d8dcff; } .telegram-recent p { color:var(--muted); } .telegram-recent ul { display:grid; gap:8px; margin:0; padding:0; list-style:none; } .telegram-recent li { display:grid; gap:4px; padding:10px 11px; border:1px solid rgba(142,214,210,.20); border-radius:12px; background:rgba(142,214,210,.07); } .telegram-recent li a,.telegram-recent li strong { color:#bfe3ff; font-size:13px; font-weight:850; text-decoration:none; overflow-wrap:anywhere; } .telegram-recent li a:hover { color:#d8c3ff; text-decoration:underline; } .telegram-recent li span { color:var(--muted); font-size:11px; } .telegram-recent li em { color:#dce0f8; font-size:12px; font-style:normal; line-height:1.35; overflow-wrap:anywhere; }
.table-wrap { overflow-x:auto; border:1px solid var(--line); border-radius:14px; } table { width:100%; border-collapse:collapse; min-width:860px; } th,td { padding:8px 8px; border-bottom:1px solid var(--line); text-align:right; white-space:nowrap; } th { color:#c8d0ff; background:var(--head); font-size:10px; text-transform:uppercase; letter-spacing:.035em; } td { color:var(--text); font-size:12px; font-variant-numeric:tabular-nums; } tr:last-child td { border-bottom:none; } th:nth-child(1),td:nth-child(1) { width:104px; } th:nth-child(1),td:nth-child(1),th:nth-child(2),td:nth-child(2) { text-align:left; } th:not(:nth-child(2)),td:not(:nth-child(2)) { width:100px; } th:nth-child(6),td:nth-child(6) { width:148px; } .empty-row td { color:var(--muted); text-align:left; background:rgba(255,255,255,.025); }
.search-result-group { margin:10px 0; overflow:hidden; border:1px solid rgba(143,185,255,.38); border-radius:14px; background:rgba(143,185,255,.07); } .search-result-group summary { display:flex; align-items:center; justify-content:space-between; gap:10px; padding:12px 14px; color:#dce7ff; font-size:13px; font-weight:850; cursor:pointer; list-style:none; } .search-result-group summary::-webkit-details-marker { display:none; } .search-result-group summary::before { content:'▸'; display:inline-block; margin-right:8px; color:#9ec0ff; font-size:16px; transition:transform .16s ease; } .search-result-group[open] summary::before { transform:rotate(90deg); } .search-result-group summary strong { margin-right:auto; } .search-result-group summary span { color:var(--muted); font-size:11px; font-weight:750; white-space:nowrap; } .search-result-group[open] summary { border-bottom:1px solid rgba(143,185,255,.25); background:rgba(143,185,255,.10); } .search-result-group .table-wrap { border:0; border-radius:0; }
tbody tr.site-amazon { --site-bg:rgba(247,197,109,.13); --site-bg-strong:rgba(247,197,109,.24); --site-line:rgba(247,197,109,.84); --site-link:#ffd482; }
tbody tr.site-hepsiburada { --site-bg:rgba(255,154,111,.13); --site-bg-strong:rgba(255,154,111,.25); --site-line:rgba(255,154,111,.86); --site-link:#ffad82; }
tbody tr.site-trendyol { --site-bg:rgba(246,163,199,.13); --site-bg-strong:rgba(246,163,199,.25); --site-line:rgba(246,163,199,.84); --site-link:#f8b4d0; }
tbody tr.site-network { --site-bg:rgba(133,220,207,.13); --site-bg-strong:rgba(133,220,207,.25); --site-line:rgba(133,220,207,.84); --site-link:#a6ebe0; }
tbody tr.site-nordbron { --site-bg:rgba(143,190,255,.13); --site-bg-strong:rgba(143,190,255,.25); --site-line:rgba(143,190,255,.84); --site-link:#b8d4ff; }
tbody tr.site-zara { --site-bg:rgba(176,218,139,.13); --site-bg-strong:rgba(176,218,139,.25); --site-line:rgba(176,218,139,.84); --site-link:#c9ec9f; }
tbody tr.site-hm { --site-bg:rgba(214,178,255,.13); --site-bg-strong:rgba(214,178,255,.25); --site-line:rgba(214,178,255,.84); --site-link:#dec4ff; }
tbody tr.site-other { --site-bg:rgba(183,177,222,.13); --site-bg-strong:rgba(183,177,222,.22); --site-line:rgba(183,177,222,.72); --site-link:#d1caff; } tbody tr[class*='site-'] td { background:linear-gradient(90deg,var(--site-bg),rgba(30,33,57,.28)); } tbody tr[class*='site-'] td:first-child { border-left:4px solid var(--site-line); color:var(--site-link); font-weight:800; } tbody tr[class*='site-'] .product-cell a { color:var(--site-link); } tbody tr[class*='site-']:hover td { background:linear-gradient(90deg,rgba(255,255,255,.055),var(--site-bg)); }
.product-cell { max-width:360px; white-space:normal; line-height:1.22; } .product-cell a { color:#9ec0ff; text-decoration:none; } .product-cell a:hover { color:#d1b3ff; text-decoration:underline; } .product-cell span { display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; text-overflow:ellipsis; } .deal-row td { color:#b7f0dc; } .deal-row td:first-child { color:var(--site-link); } .deal-row .product-cell a { color:#b7f0dc; } .note { margin-top:18px; border-left:4px solid #b79ad6; padding:12px 14px; background:rgba(183,154,214,.15); border-radius:10px; font-size:13px; } .footer { margin-top:18px; font-size:12px; color:var(--muted); }
.public main { max-width:1180px; } .public .hero { padding:18px; } .public .badge { font-size:clamp(22px,4vw,36px); }
.public-actions { margin:16px 0 6px; } .public-actions .button { min-width:132px; }
.public-cycle-row { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; margin:10px 0 4px; }
.public-cycle-pill { min-width:0; min-height:40px; padding:7px 10px; border:1px solid var(--line); border-radius:13px; background:#2a2f4d; }
.public-cycle-pill span { display:block; color:var(--muted); font-size:10px; font-weight:800; letter-spacing:.035em; text-transform:uppercase; }
.public-cycle-pill strong { display:block; margin-top:2px; font-size:14px; line-height:1.1; color:var(--text); }
@media (max-width:720px) {
  body { font-size:13px; background:#0f1222; }
  main { padding:10px 8px 26px; }
  .hero { border-radius:18px; padding:14px; }
  .public main { padding:0; }
  .public .hero { min-height:100vh; border-width:0; border-radius:0; padding:14px 10px 24px; box-shadow:none; }
  .badge { margin-bottom:8px; padding:8px 13px; font-size:28px; }
  p { font-size:12px; }
  .actions { gap:8px; }
  .public-actions { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); }
  .public-actions .button, .public-actions .inline-form { width:100%; min-width:0; }
  .public-actions .button { width:100%; min-width:0; min-height:44px; padding:0 10px; font-size:12px; }
  .public-cycle-row { grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; margin:12px 0 4px; }
  .public-cycle-pill { min-height:66px; padding:12px 13px; border-radius:15px; }
  .public-cycle-pill span { font-size:11px; }
  .public-cycle-pill strong { margin-top:5px; font-size:20px; }
  .grid { grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; }
  .card { min-height:70px; padding:11px; border-radius:13px; }
  .card span { font-size:11px; margin-bottom:6px; }
  .card strong { font-size:15px; }
  .summary-panel { margin-top:12px; padding:11px; border-radius:15px; }
  .summary-head { align-items:flex-start; flex-direction:column; gap:4px; margin-bottom:10px; }
  .summary-head h2 { font-size:16px; }
  .summary-head span { white-space:normal; font-size:11px; }
  .public .summary-head span { font-size:16px; line-height:1.3; color:#9ed8ff; font-weight:800; }
  .table-section h3 { font-size:13px; }
  .table-wrap { overflow:visible; border:0; border-radius:0; }
  .search-result-group { margin:8px 0; border-radius:13px; }
  .search-result-group summary { min-height:48px; padding:12px; font-size:13px; }
  .search-result-group summary span { font-size:11px; }
  table { min-width:0; }
  thead { display:none; }
  table, tbody, td { display:block; width:100%; }
  tbody tr[class*='site-'] { position:relative; display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:6px 8px; margin:0 0 8px; border:1px solid var(--site-line); border-left:7px solid var(--site-line); border-radius:15px; padding:9px 10px 9px 12px; background:linear-gradient(135deg,var(--site-bg-strong),rgba(30,33,57,.88) 58%),rgba(30,33,57,.86); box-shadow:0 8px 20px rgba(0,0,0,.18); overflow:hidden; }
  tbody tr[class*='site-'] td { display:flex; justify-content:flex-start; gap:4px; padding:0; border-bottom:0; background:transparent; text-align:left; white-space:normal; font-size:12.5px; line-height:1.18; }
  tbody tr[class*='site-'] td:first-child { border-left:0; color:var(--site-link); }
  tbody tr[class*='site-'] td::before { content:attr(data-label); flex:0 0 auto; color:var(--muted); text-align:left; font-size:9px; font-weight:850; letter-spacing:.045em; text-transform:uppercase; }
  tbody tr[class*='site-'] .seller-cell { grid-column:1 / -1; align-items:center; gap:0; padding-bottom:0; color:var(--site-link); font-size:14px; font-weight:900; }
  tbody tr[class*='site-'] .seller-cell::before, tbody tr[class*='site-'] .product-cell::before { display:none; }
  tbody tr[class*='site-'] .product-cell { grid-column:1 / -1; max-width:none; display:block; padding-bottom:0; text-align:left; line-height:1.22; font-size:13px; }
  tbody tr[class*='site-'] .price-cell, tbody tr[class*='site-'] .target-cell, tbody tr[class*='site-'] .diff-cell, tbody tr[class*='site-'] .range-cell { min-height:33px; border:1px solid rgba(255,255,255,.06); border-radius:10px; padding:5px 7px; background:rgba(12,15,30,.25); flex-direction:column; justify-content:center; font-size:13.5px; }
  tbody tr[class*='site-'] .price-cell, tbody tr[class*='site-'] .target-cell, tbody tr[class*='site-'] .diff-cell { min-width:0; }
  tbody tr[class*='site-'] .range-cell { grid-column:1 / -1; min-height:31px; flex-direction:row; align-items:center; justify-content:flex-start; gap:8px; white-space:nowrap; }
  tbody tr[class*='site-'] .range-cell::before { margin-right:3px; }
  .product-cell span { -webkit-line-clamp:2; }
  .empty-row td { padding:10px; border:1px solid var(--line); border-radius:12px; }
  .note, .footer { font-size:11px; }
}
"""


def _parse_turkish_money(value):
    text = str(value or "").strip().replace("TL", "").replace(" ", "")
    text = text.replace("+", "").replace(".", "").replace(",", ".")
    if not text or text == "-":
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _relative_time_text(value) -> str:
    raw_value = str(value or "").strip()
    parsed = None
    for fmt in ("%Y-%m-%d %H:%M:%S",):
        try:
            parsed = datetime.strptime(raw_value, fmt).astimezone()
            break
        except ValueError:
            pass
    if parsed is None:
        parsed = parse_iso_datetime(raw_value)
    if not parsed:
        return "-"
    elapsed_seconds = max(0, int((datetime.now().astimezone() - parsed.astimezone()).total_seconds()))
    if elapsed_seconds < 60:
        return "az önce" if elapsed_seconds < 10 else f"{elapsed_seconds} sn önce"
    minutes = elapsed_seconds // 60
    if minutes < 60:
        return f"{minutes} dk önce"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} sa önce"
    days = hours // 24
    return f"{days} gün önce"


def _duration_text(seconds_value, fallback="-") -> str:
    if seconds_value in (None, ""):
        return str(fallback or "-")
    try:
        total_seconds = max(0, int(round(float(seconds_value))))
    except (TypeError, ValueError):
        return str(fallback or "-")
    minutes, seconds = divmod(total_seconds, 60)
    if minutes:
        return f"{minutes} dk {seconds} sn"
    return f"{seconds} sn"


def _is_target_hit(row):
    explicit = row.get("is_target_hit")
    if isinstance(explicit, bool):
        return explicit
    price = _parse_turkish_money(row.get("price"))
    target = _parse_turkish_money(row.get("target"))
    if price is not None and target is not None:
        return price <= target
    diff = _parse_turkish_money(row.get("difference"))
    return diff is not None and diff <= 0


def _current_addon_slug():
    hostname = os.getenv("HOSTNAME", "").strip()
    hyphen_slug = ADDON_SLUG.replace("_", "-")
    if hostname.endswith(f"-{hyphen_slug}"):
        repository_id = hostname[: -(len(hyphen_slug) + 1)]
        if repository_id:
            return f"{repository_id}_{ADDON_SLUG}"
    return hostname.replace("-", "_") if hostname else f"local_{ADDON_SLUG}"


def _addon_urls():
    slug = urllib.parse.quote(_current_addon_slug(), safe="")
    return f"/config/app/{slug}/logs", f"/config/app/{slug}/config"


def _extract_first_url(text):
    if not text:
        return ""
    match = re.search(r"https?://\S+", str(text))
    if not match:
        return ""
    return match.group(0).rstrip(".,;)]}")


def _short_link(value, max_length=74):
    text = str(value or "").strip()
    if not text:
        return "-"
    if len(text) <= max_length:
        return text
    return text[: max_length - 3].rstrip() + "..."


def _site_name(raw_site, is_search):
    if is_search:
        return "Amazon arama"
    labels = {
        "amazon": "Amazon",
        "hepsiburada": "Hepsiburada",
        "trendyol": "Trendyol",
        "network": "Network",
        "nordbron": "Nordbron",
        "zara": "Zara",
        "hm": "H&M",
    }
    return labels.get(str(raw_site or "").strip().lower(), "Ürün kontrolü")


def _site_theme_class(seller):
    normalized = repair_mojibake(seller).casefold()
    if "amazon" in normalized:
        return "site-amazon"
    if "hepsiburada" in normalized:
        return "site-hepsiburada"
    if "network" in normalized:
        return "site-network"
    if "trendyol" in normalized:
        return "site-trendyol"
    if "nordbron" in normalized:
        return "site-nordbron"
    if "zara" in normalized:
        return "site-zara"
    if "h&m" in normalized or "hm" in normalized:
        return "site-hm"
    return "site-other"


def _clean_error_message(error_text):
    text = repair_mojibake(error_text or "").strip()
    if not text:
        return "Hata ayrıntısı kaydedilmemiş."
    parts = [part.strip() for part in text.split("|") if part.strip()]
    non_url_parts = [part for part in parts if not part.startswith(("http://", "https://"))]
    if non_url_parts:
        text = " | ".join(non_url_parts)
    text = re.sub(r"https?://\S+", "[link]", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or "Hata ayrıntısı kaydedilmemiş."


def _error_link_details(error_text):
    text = repair_mojibake(error_text or "").strip()
    if not text:
        return []
    details = []
    seen = set()
    for segment in [item.strip() for item in text.split(";") if item.strip()]:
        url = _extract_first_url(segment)
        if not url:
            continue
        message = _clean_error_message(segment)
        key = (url, message)
        if key in seen:
            continue
        seen.add(key)
        details.append({"url": url, "message": message})
    return details


def _unique_text(values):
    unique = []
    seen = set()
    for value in values:
        text = repair_mojibake(value).strip()
        if not text or text.casefold() in seen:
            continue
        seen.add(text.casefold())
        unique.append(text)
    return unique


def _target_text(labels):
    if not labels:
        return "Aranan keyword belirtilmemiş"
    prefix = "Aranan keyword" if len(labels) == 1 else "Aranan keywordler"
    return f"{prefix}: {', '.join(labels)}"


WATCH_URL_FIELDS = ("url_1", "url_2", "url_3", "url_4", "url_5")


def _watch_urls_from_options(item):
    urls = []
    if not isinstance(item, dict):
        return urls
    for field_name in WATCH_URL_FIELDS:
        url = str(item.get(field_name) or "").strip()
        if url and url not in urls:
            urls.append(url)
    return urls


def _context_for_watch_url(item, url):
    try:
        site = detect_site_from_url(url)
        seller = site_label(site)
    except Exception:  # noqa: BLE001
        site = str(item.get("site") or "urun").strip().lower()
        seller = _site_name(site, False)
    name = str(item.get("name") or "").strip()
    display_name = name or url
    key = normalize_item_key("watch", site, name, url)
    return key, {
        "title": f"{seller}: {display_name}",
        "meta": f"Takip edilen: {display_name}",
        "url": url,
        "urls": [url],
        "keywords": [name] if name else [],
    }


def _contexts_for_watch(item):
    return [
        context
        for url in _watch_urls_from_options(item)
        for context in [_context_for_watch_url(item, url)]
        if context
    ]


def _error_contexts(options):
    watches = options.get("takip_edilenler") if isinstance(options.get("takip_edilenler"), list) else []
    contexts = {}
    for item in watches:
        if isinstance(item, dict):
            for context in _contexts_for_watch(item):
                key, value = context
                contexts[key] = value
    return contexts


def _urls_from_error_and_state(raw_error, state_entry):
    urls = [item["url"] for item in _error_link_details(raw_error)]
    for field in ("url", "configured_url"):
        url = str(state_entry.get(field) or "").strip()
        if url:
            urls.append(url)
    return _unique_text(urls)


def _find_error_context(state_key, state_entry, raw_error, contexts):
    if state_key in contexts:
        return contexts[state_key]
    failed_urls = _urls_from_error_and_state(raw_error, state_entry)
    for context in contexts.values():
        context_urls = context.get("urls") or [context.get("url")]
        context_urls = [str(url or "").strip() for url in context_urls]
        if any(url and url in context_urls for url in failed_urls):
            return context
    return {}


def _error_detail(state_key, state_entry, contexts):
    raw_error = state_entry.get("last_error")
    context = _find_error_context(state_key, state_entry, raw_error, contexts)
    failed_links = _error_link_details(raw_error)
    keywords = context.get("keywords") or []
    keyword_text = _target_text(keywords) if keywords else ""
    for failed_link in failed_links:
        if keyword_text:
            failed_link["keywords"] = keyword_text
    url_text = (
        (failed_links[0]["url"] if failed_links else "")
        or str(context.get("url") or state_entry.get("url") or "").strip()
        or _extract_first_url(raw_error)
    )
    title = context.get("title") or _site_name(state_entry.get("site"), False)
    meta = context.get("meta") or keyword_text
    if not meta:
        meta = "Takip edilen link kontrol edilirken hata oluştu."
    elif keyword_text and "keyword" not in meta.casefold():
        meta = f"{meta} · {keyword_text}" if keyword_text else meta
    return {
        "title": repair_mojibake(title),
        "meta": repair_mojibake(meta),
        "message": _clean_error_message(raw_error),
        "url": url_text,
        "failed_links": failed_links[:4],
    }


def _error_detail_key(detail):
    fields = ("title", "meta", "message", "url")
    link_key = ";".join(item.get("url", "") for item in detail.get("failed_links", []))
    return "|".join(str(detail.get(field) or "") for field in fields) + "|" + link_key


def _collect_summary():
    options = load_json(OPTIONS_PATH, {})
    state = load_json(STATE_PATH, {})
    latest_summary = load_json(SUMMARY_PATH, {})
    if not isinstance(latest_summary, dict):
        latest_summary = {}
    watches = options.get("takip_edilenler") if isinstance(options.get("takip_edilenler"), list) else []
    contexts = _error_contexts(options if isinstance(options, dict) else {})

    error_cutoff = timedelta(hours=24)
    now = datetime.now().astimezone()
    last_checks = []
    error_count = 0
    error_details = []
    seen_details = set()

    if isinstance(state, dict):
        for key, value in state.items():
            if key == "_meta" or not isinstance(value, dict):
                continue
            checked_at = parse_iso_datetime(value.get("last_checked_at"))
            if checked_at:
                checked_local = checked_at.astimezone()
                last_checks.append(checked_local)
                if value.get("last_error") and now - checked_local <= error_cutoff:
                    error_count += 1
                    detail = _error_detail(key, value, contexts)
                    detail_key = _error_detail_key(detail)
                    if detail_key not in seen_details:
                        seen_details.add(detail_key)
                        error_details.append(detail)
    interval_seconds = int(options.get("interval_seconds") or 60)
    last_check = max(last_checks) if last_checks else None
    return {
        "interval": interval_seconds,
        "watches": len(watches),
        "last_check": last_check.strftime("%Y-%m-%d %H:%M:%S") if last_check else "-",
        "next_check": (last_check + timedelta(seconds=interval_seconds)).strftime("%Y-%m-%d %H:%M:%S") if last_check else "-",
        "cycle_duration": _duration_text(
            latest_summary.get("cycle_duration_seconds"),
            latest_summary.get("cycle_duration_minutes") or "-",
        ),
        "last_update": _relative_time_text(latest_summary.get("checked_at")),
        "errors": error_count,
        "error_details": error_details[:4],
        "configured": bool(options),
        "telegram": _collect_telegram_summary(options if isinstance(options, dict) else {}),
    }


def _telegram_error_count_24h():
    payload = load_json(TELEGRAM_ERROR_EVENTS_PATH, [])
    if not isinstance(payload, list):
        return 0
    cutoff = datetime.now().astimezone() - timedelta(hours=24)
    count = 0
    for item in payload:
        if not isinstance(item, dict):
            continue
        try:
            created_at = datetime.fromisoformat(str(item.get("created_at")))
            if created_at.tzinfo is None:
                created_at = created_at.astimezone()
        except ValueError:
            continue
        if created_at.astimezone() >= cutoff:
            count += 1
    return count


def _collect_telegram_summary(options):
    status = load_json(TELEGRAM_STATUS_PATH, {})
    if not isinstance(status, dict):
        status = {}
    channels = options.get("channels") if isinstance(options.get("channels"), list) else []
    keywords = options.get("keywords") if isinstance(options.get("keywords"), list) else []
    enabled = parse_bool(options.get("telegram_enabled"), default=False)
    return {
        "enabled": enabled,
        "state": status.get("telegram_state") or ("Pasif" if not enabled else "Bekleniyor"),
        "channels": status.get("telegram_channels") or len(channels),
        "keywords": status.get("telegram_keywords") or len(keywords),
        "notifications": status.get("notifications_sent", 0),
        "last_check": status.get("last_check") or "-",
        "last_notification": status.get("last_notification") or "-",
        "errors": _telegram_error_count_24h(),
        "recent_notifications": status.get("recent_notifications") if isinstance(status.get("recent_notifications"), list) else [],
    }


def _render_table_row(row):
    seller_text = repair_mojibake(row.get("seller") or "-")
    seller = escape(seller_text)
    raw_title = repair_mojibake(row.get("product_title") or "-")
    if seller_text == "Hepsiburada":
        raw_title = hepsiburada_provider.clean_display_title(raw_title)
    product_title = escape(raw_title)
    product_url = str(row.get("product_url") or "").strip()
    if product_url:
        label = (
            f'<a href="{escape(product_url, quote=True)}" target="_blank" rel="noopener noreferrer">'
            f"<span>{product_title}</span></a>"
        )
    else:
        label = f"<span>{product_title}</span>"
    price = escape(str(row.get("price", "-")))
    target = escape(str(row.get("target", "-")))
    difference = escape(str(row.get("difference", "-")))
    price_range = escape(str(row.get("price_range") or f"{row.get('min_price', '-')} / {row.get('max_price', '-') }"))
    row_classes = [_site_theme_class(seller_text)]
    if _is_target_hit(row):
        row_classes.append("deal-row")
    row_class = f' class="{" ".join(row_classes)}"'
    return (
        f'<tr{row_class}><td data-label="Satıcı" class="seller-cell">{seller}</td>'
        f'<td data-label="Ürün" class="product-cell" title="{product_title}">{label}</td>'
        f'<td data-label="Güncel" class="price-cell">{price}</td>'
        f'<td data-label="Hedef" class="target-cell">{target}</td>'
        f'<td data-label="Fark" class="diff-cell">{difference}</td>'
        f'<td data-label="Min / Maks" class="range-cell">{price_range}</td></tr>'
    )


def _render_rows_table(rows, empty_text):
    if rows:
        body = "".join(_render_table_row(row) for row in rows)
    else:
        body = f"<tr class='empty-row'><td colspan='6'>{escape(empty_text)}</td></tr>"
    return f"""
        <div class="table-wrap">
          <table>
            <thead><tr><th>Satıcı</th><th>Ürün Adı</th><th>Güncel</th><th>Hedef</th><th>Fark</th><th>Min / Maks</th></tr></thead>
            <tbody>{body}</tbody>
          </table>
        </div>
    """


def _split_search_result_groups(rows):
    grouped = {}
    ungrouped = []
    for row in rows:
        group_key = str(row.get("search_group") or "").strip()
        if not group_key:
            ungrouped.append(row)
            continue
        grouped.setdefault(group_key, []).append(row)

    collapsible_groups = []
    for group_rows in grouped.values():
        if len(group_rows) < 2:
            ungrouped.extend(group_rows)
            continue
        label = str(group_rows[0].get("search_group_label") or "Arama sonuçları").strip()
        collapsible_groups.append((label, group_rows))
    return ungrouped, collapsible_groups


def _render_collapsible_search_group(label, rows):
    count = len(rows)
    return f"""
      <details class="search-result-group">
        <summary><strong>{escape(label)}</strong><span>{count} sonuç</span></summary>
        {_render_rows_table(rows, "")}
      </details>
    """


def _render_table_section(title, rows, empty_text, extra_class="", collapse_search_results=False):
    if not rows:
        body = _render_rows_table([], empty_text)
    elif not collapse_search_results:
        body = _render_rows_table(rows, empty_text)
    else:
        ungrouped, collapsible_groups = _split_search_result_groups(rows)
        pieces = []
        if ungrouped:
            pieces.append(_render_rows_table(ungrouped, empty_text))
        pieces.extend(_render_collapsible_search_group(label, group_rows) for label, group_rows in collapsible_groups)
        body = "".join(pieces)
    return f"""
      <div class="table-section {extra_class}">
        <h3>{escape(title)}</h3>
        {body}
      </div>
    """


def _render_stock_row(row):
    seller_text = repair_mojibake(row.get("seller") or "-")
    seller = escape(seller_text)
    product_title = escape(repair_mojibake(row.get("product_title") or "-"))
    product_url = str(row.get("product_url") or "").strip()
    if product_url:
        label = (
            f'<a href="{escape(product_url, quote=True)}" target="_blank" rel="noopener noreferrer">'
            f"<span>{product_title}</span></a>"
        )
    else:
        label = f"<span>{product_title}</span>"
    target = escape(str(row.get("target", "-")))
    reason = escape(repair_mojibake(row.get("reason") or "Stokta yok"))
    row_class = f' class="{_site_theme_class(seller_text)} stock-missing-row"'
    return (
        f'<tr{row_class}><td data-label="Satıcı" class="seller-cell">{seller}</td>'
        f'<td data-label="Ürün" class="product-cell" title="{product_title}">{label}</td>'
        f'<td data-label="Hedef" class="target-cell">{target}</td>'
        f'<td data-label="Durum" class="diff-cell">{reason}</td></tr>'
    )


def _render_stock_section(rows):
    if rows:
        body = "".join(_render_stock_row(row) for row in rows)
    else:
        body = "<tr class='empty-row'><td colspan='4'>Stok dışında izlenen ürün yok.</td></tr>"
    return f"""
      <div class="table-section stock-section">
        <h3>Stokta Olmayanlar</h3>
        <div class="table-wrap">
          <table>
            <thead><tr><th>Satıcı</th><th>Ürün Adı</th><th>Hedef</th><th>Durum</th></tr></thead>
            <tbody>{body}</tbody>
          </table>
        </div>
      </div>
    """


def _is_search_result_source(site: str, configured_url: str) -> bool:
    return (
        (site == "amazon" and is_amazon_search_url(configured_url))
        or (site == "hepsiburada" and is_hepsiburada_search_url(configured_url))
    )


def _search_result_groups_from_state(state):
    groups = {}
    if not isinstance(state, dict):
        return groups
    for entry in state.values():
        if not isinstance(entry, dict):
            continue
        configured_url = str(entry.get("configured_url") or "").strip()
        result_url = str(entry.get("url") or "").strip()
        watch_name = str(entry.get("watch_name") or "").strip()
        site = str(entry.get("site") or "").strip()
        if not configured_url or not result_url or not watch_name:
            continue
        if not site:
            try:
                site = detect_site_from_url(configured_url)
            except Exception:  # noqa: BLE001
                continue
        if not _is_search_result_source(site, configured_url):
            continue
        groups[result_url] = {
            "search_group": normalize_item_key("search_result_group", site, watch_name, configured_url),
            "search_group_label": watch_name,
        }
    return groups


def _attach_legacy_search_groups(rows, state):
    groups = _search_result_groups_from_state(state)
    if not groups:
        return rows
    enriched = []
    for row in rows:
        if not isinstance(row, dict) or row.get("search_group"):
            enriched.append(row)
            continue
        metadata = groups.get(str(row.get("product_url") or "").strip())
        if not metadata:
            enriched.append(row)
            continue
        enriched_row = dict(row)
        enriched_row.update(metadata)
        enriched.append(enriched_row)
    return enriched


def _render_table():
    payload = load_json(SUMMARY_PATH, {})
    rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
    stock_rows = payload.get("stock_rows") if isinstance(payload.get("stock_rows"), list) else []
    rows = _attach_legacy_search_groups(rows, load_json(STATE_PATH, {}))
    if not rows and not stock_rows:
        return """
        <section class="summary-panel">
          <div class="summary-head"><h2>Özet Tablo</h2><span>Henüz tablo yok</span></div>
          <p class="empty-table">İlk kontrol döngüsü tamamlandığında son fiyat tablosu burada görünecek.</p>
        </section>
        """

    deal_rows = [row for row in rows if _is_target_hit(row)]
    watch_rows = [row for row in rows if not _is_target_hit(row)]
    row_count = escape(str(payload.get("row_count") or len(rows)))
    deal_count = escape(str(len(deal_rows)))
    stock_count = escape(str(payload.get("stock_row_count") or len(stock_rows)))
    sections = _render_table_section(
        "Hedef Fiyat Altındaki Fırsatlar",
        deal_rows,
        "Şu anda hedef fiyatın altına düşen ürün yok.",
        "deals-section",
    )
    sections += _render_table_section(
        "Hedefin Üstünde Kalan Ürünler",
        watch_rows,
        "Hedef üstünde bekleyen ürün yok.",
        collapse_search_results=True,
    )
    sections += _render_stock_section(stock_rows)
    return f"""
    <section class="summary-panel">
      <div class="summary-head"><h2>Özet Tablo</h2><span>{row_count} ürün · {deal_count} fırsat · {stock_count} stokta yok</span></div>
      {sections}
    </section>
    """


def _render_telegram_panel(summary, include_recent_notifications=True):
    telegram = summary.get("telegram") or {}
    state = str(telegram.get("state") or "Pasif")
    state_class = "status-ok" if state == "Dinleniyor" else ("status-warn" if state in {"Pasif", "Kod bekleniyor"} else "status-error")
    cards = [
        ("Telegram durumu", state, state_class),
        ("Telegram kanalları", telegram.get("channels", 0), ""),
        ("Keyword sayısı", telegram.get("keywords", 0), ""),
        ("Gönderilen bildirim", telegram.get("notifications", 0), ""),
        ("Son Telegram kontrolü", telegram.get("last_check", "-"), ""),
        ("Son Telegram bildirimi", telegram.get("last_notification", "-"), ""),
        ("Telegram hata sayısı (24s)", telegram.get("errors", 0), "status-error" if int(telegram.get("errors", 0) or 0) else ""),
    ]
    card_html = "".join(
        f"<section class='card {escape(str(css))}'><span>{escape(str(label))}</span><strong>{escape(str(value))}</strong></section>"
        for label, value, css in cards
    )
    recent_html = (
        _render_telegram_recent_notifications(telegram.get("recent_notifications") or [])
        if include_recent_notifications
        else ""
    )
    return f"""
    <section class="summary-panel">
      <div class="summary-head"><h2>Telegram Takip</h2><span>Keyword bildirimleri</span></div>
      <div class="grid">{card_html}</div>
      {recent_html}
    </section>
    """


def _render_telegram_recent_notifications(items):
    if not items:
        return "<div class='telegram-recent'><h3>Son Telegram Bildirimleri</h3><p>Henüz Telegram bildirimi yok.</p></div>"
    rows = []
    for item in items[:5]:
        if not isinstance(item, dict):
            continue
        keyword = escape(str(item.get("keyword") or "-"))
        channel = escape(str(item.get("channel") or "-"))
        created_at = escape(str(item.get("created_at") or "-"))
        message = escape(str(item.get("message") or ""))
        url = str(item.get("url") or "").strip()
        title = keyword
        if url:
            title_html = f"<a href='{escape(url, quote=True)}' target='_blank' rel='noopener noreferrer'>{title}</a>"
        else:
            title_html = f"<strong>{title}</strong>"
        rows.append(
            "<li>"
            f"{title_html}"
            f"<span>{channel} · {created_at}</span>"
            f"<em>{message}</em>"
            "</li>"
        )
    body = "".join(rows) if rows else "<li class='empty-error'>Henüz Telegram bildirimi yok.</li>"
    return f"<div class='telegram-recent'><h3>Son Telegram Bildirimleri</h3><ul>{body}</ul></div>"


def _send_test_notification():
    options = load_json(OPTIONS_PATH, {})
    user_key = str(options.get("pushover_user_key", "")).strip()
    api_token = str(options.get("pushover_api_token", "")).strip()
    timeout = int(options.get("request_timeout_seconds", 20) or 20)
    if not user_key or not api_token:
        return False, "Pushover anahtarları eksik. Config sekmesini kontrol et."
    payload = urllib.parse.urlencode(
        {
            "token": api_token,
            "user": user_key,
            "title": "Hermes test",
            "message": "Hermes test bildirimi. Ayarlar sağlıklı görünüyor.",
            "sound": "pushover",
            "priority": "0",
        }
    ).encode("utf-8")
    try:
        with urllib.request.urlopen(
            urllib.request.Request(PUSHOVER_URL, data=payload, method="POST"), timeout=timeout
        ) as response:
            response.read()
        return True, "Pushover test bildirimi gönderildi."
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        return False, f"Pushover hata verdi: {exc.code} {detail[:180]}"
    except Exception as exc:  # noqa: BLE001
        return False, f"Pushover test bildirimi gönderilemedi: {exc}"


def _reset_notifications_worker() -> None:
    try:
        from .config_loader import load_config
        from .service import check_once, reset_notification_suppression

        reset_count = reset_notification_suppression()
        config = load_config()
        check_once(config)
        log(f"Bildirim sifirlama sonrasi tek seferlik kontrol tamamlandi: sifirlanan_kayit={reset_count}")
    except Exception as exc:  # noqa: BLE001
        log(f"Bildirim sifirlama kontrolu tamamlanamadi: {exc}")
    finally:
        RESET_NOTIFICATIONS_LOCK.release()


def _reset_notifications_async():
    if not RESET_NOTIFICATIONS_LOCK.acquire(blocking=False):
        return False, "Bildirim sıfırlama zaten çalışıyor. Lütfen biraz sonra tekrar dene."
    thread = threading.Thread(target=_reset_notifications_worker, name="notification-reset-check", daemon=True)
    thread.start()
    return True, "Bildirim susturma hafızası sıfırlandı. Hedef altında kalan fırsatlar için tek seferlik kontrol arka planda başladı."


def _reset_price_history():
    if not PRICE_HISTORY_RESET_LOCK.acquire(blocking=False):
        return False, "Min/maks sıfırlama zaten çalışıyor. Lütfen biraz sonra tekrar dene."
    try:
        from .service import reset_price_history

        cleared_count = reset_price_history()
        return True, f"Min/maks fiyat geçmişi sıfırlandı. Temizlenen kayıt alanı: {cleared_count}."
    except Exception as exc:  # noqa: BLE001
        log(f"Min/maks fiyat gecmisi sifirlanamadi: {exc}")
        return False, f"Min/maks fiyat geçmişi sıfırlanamadı: {exc}"
    finally:
        PRICE_HISTORY_RESET_LOCK.release()


def _render_failed_links(detail):
    failed_links = detail.get("failed_links") or []
    if not failed_links:
        return ""
    rendered = []
    for item in failed_links:
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        message = escape(str(item.get("message") or "Hata ayrıntısı yok."))
        keywords = escape(str(item.get("keywords") or ""))
        keyword_line = f"<strong>{keywords}</strong>" if keywords else ""
        rendered.append(
            "<div class='failed-link'>"
            f"<span>Hatalı link</span>"
            f"{keyword_line}"
            f"<a href='{escape(url, quote=True)}' target='_blank' rel='noopener noreferrer'>{escape(_short_link(url, 96))}</a>"
            f"<em>{message}</em>"
            "</div>"
        )
    return "".join(rendered)


def _render_error_details(error_details):
    if not error_details:
        return "<li class='empty-error'>Son 24 saatte hata yok.</li>"
    items = []
    for detail in error_details:
        title = escape(str(detail.get("title") or "Hata"))
        meta = escape(str(detail.get("meta") or "Kontrol sırasında hata oluştu."))
        message = escape(str(detail.get("message") or "Hata ayrıntısı yok."))
        url = str(detail.get("url") or "").strip()
        link = ""
        if url:
            link = f"<a href='{escape(url, quote=True)}' target='_blank' rel='noopener noreferrer'>Linki aç</a>"
        items.append(
            "<li>"
            f"<strong>{title}</strong>"
            f"<span>{meta}</span>"
            f"<em>Hata: {message}</em>"
            f"{_render_failed_links(detail)}"
            f"{link}"
            "</li>"
        )
    return "".join(items)


def _render_page(path: str = "/") -> bytes:
    summary = _collect_summary()
    log_url, app_url = _addon_urls()
    params = urllib.parse.parse_qs(urllib.parse.urlparse(path).query)
    test_status = params.get("test", [""])[0]
    reset_status = params.get("reset", [""])[0]
    history_status = params.get("history", [""])[0]
    test_message = params.get("msg", [""])[0]
    status = "Çalışıyor" if summary["configured"] else "Ayar bekliyor"
    status_class = "status-ok" if summary["configured"] else "status-warn"
    error_class = "status-error" if int(summary["errors"]) > 0 else ""
    error_details_html = _render_error_details(summary.get("error_details") or [])

    cards = [
        ("Durum", status, status_class),
        ("Kontrol aralığı", f"{summary['interval']} saniye", ""),
        ("Çevrim süresi", summary["cycle_duration"], ""),
        ("Son güncelleme", summary.get("last_update", "-"), ""),
        ("Takip edilenler", summary["watches"], ""),
    ]
    card_html = "".join(
        f"<section class='card {escape(str(css))}'><span>{escape(str(label))}</span><strong>{escape(str(value))}</strong></section>"
        for label, value, css in cards
    )
    error_card_html = (
        "<section class='card error-card "
        + escape(str(error_class))
        + "'><span>Hata sayısı (son 24 saat)</span>"
        + f"<strong>{escape(str(summary['errors']))}</strong>"
        + f"<ul>{error_details_html}</ul></section>"
    )
    notice_html = ""
    if test_status in {"ok", "fail"} or reset_status in {"ok", "fail"} or history_status in {"ok", "fail"}:
        notice_status = test_status if test_status in {"ok", "fail"} else (reset_status if reset_status in {"ok", "fail"} else history_status)
        notice_class = "notice-ok" if notice_status == "ok" else "notice-fail"
        notice_html = f"<p class='notice {notice_class}'>{escape(test_message)}</p>"

    confirm_script = """
<script>
  document.querySelectorAll('form[data-confirm]').forEach((form) => {
    form.addEventListener('submit', (event) => {
      const message = form.getAttribute('data-confirm') || 'Bu işlemi yapmak istediğine emin misin?';
      if (!window.confirm(message)) {
        event.preventDefault();
      }
    });
  });
</script>"""
    html = f"""<!doctype html>
<html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta http-equiv="refresh" content="60"><title>Hermes</title><style>{DASHBOARD_CSS}</style></head><body><main><div class="hero"><div class="badge">Hermes</div><div class="actions"><a class="button primary" href="{log_url}" target="_top">LOG</a><a class="button secondary" href="{app_url}" target="_top">Config</a><form class="inline-form" method="post" action="./test-pushover"><button class="button test" type="submit">Pushover</button></form><form class="inline-form" method="post" action="./reset-notifications" data-confirm="Bildirim susturma hafızası sıfırlanacak ve hedef altında kalan fırsatlar için tek seferlik kontrol başlatılacak. Devam etmek istiyor musun?"><button class="button secondary" type="submit">Bildirim Sıfırla</button></form><form class="inline-form" method="post" action="./reset-price-history" data-confirm="Min/maks fiyat geçmişi temizlenecek ve güncel fiyattan yeniden başlayacak. Devam etmek istiyor musun?"><button class="button secondary" type="submit">Min/Maks Sıfırla</button></form></div>{notice_html}<div class="grid">{card_html}{error_card_html}</div>{_render_telegram_panel(summary, include_recent_notifications=False)}{_render_table()}{_render_telegram_recent_notifications((summary.get('telegram') or {}).get('recent_notifications') or [])}</div></main>{confirm_script}</body></html>"""
    return html.encode("utf-8")


def _public_token_from_path(path: str) -> str:
    parsed = urllib.parse.urlparse(path)
    parts = [urllib.parse.unquote(part) for part in parsed.path.split("/") if part]
    if len(parts) >= 2 and parts[0] == "public":
        return parts[1]
    params = urllib.parse.parse_qs(parsed.query)
    return str(params.get("token", [""])[0]).strip()


def _public_base_path(path: str) -> str:
    parsed = urllib.parse.urlparse(path)
    parts = [urllib.parse.quote(urllib.parse.unquote(part), safe="") for part in parsed.path.split("/") if part]
    if len(parts) >= 2 and parts[0] == "public":
        return "/" + "/".join(parts[:2])
    token = _public_token_from_path(path)
    return f"/public/{urllib.parse.quote(token, safe='')}" if token else "/public"


def _public_dashboard_allowed(path: str) -> bool:
    options = load_json(OPTIONS_PATH, {})
    if not isinstance(options, dict):
        return False
    if not parse_bool(options.get("public_dashboard_enabled"), default=False):
        return False
    expected_token = str(options.get("public_dashboard_token") or "").strip()
    if len(expected_token) < 24:
        return False
    return _public_token_from_path(path) == expected_token


def _render_public_page(path: str):
    if not _public_dashboard_allowed(path):
        return 404, b"not found\n"
    payload = load_json(SUMMARY_PATH, {})
    params = urllib.parse.parse_qs(urllib.parse.urlparse(path).query)
    action_status = ""
    action_message = ""
    for key in ("test", "reset", "history", "settings"):
        status = params.get(key, [""])[0]
        if status in {"ok", "fail"}:
            action_status = status
            action_message = params.get("msg", [""])[0]
            break
    notice_html = ""
    if action_status:
        notice_class = "notice-ok" if action_status == "ok" else "notice-fail"
        notice_html = f"<p class='notice {notice_class}'>{escape(action_message)}</p>"
    base_path = escape(_public_base_path(path), quote=True)
    options = load_json(OPTIONS_PATH, {})
    telegram_summary = _collect_telegram_summary(options if isinstance(options, dict) else {})
    telegram_recent_html = _render_telegram_recent_notifications(
        telegram_summary.get("recent_notifications") or []
    )
    cycle_duration = "-"
    last_update = "-"
    if isinstance(payload, dict):
        cycle_duration = escape(
            _duration_text(payload.get("cycle_duration_seconds"), payload.get("cycle_duration_minutes") or "-")
        )
        last_update = escape(_relative_time_text(payload.get("checked_at")))
    public_cycle_row = (
        "<div class='public-cycle-row'>"
        f"<section class='public-cycle-pill'><span>Çevrim süresi</span><strong>{cycle_duration}</strong></section>"
        f"<section class='public-cycle-pill'><span>Son güncelleme</span><strong>{last_update}</strong></section>"
        "</div>"
    )
    confirm_script = """
<script>
  document.querySelectorAll('form[data-confirm]').forEach((form) => {
    form.addEventListener('submit', (event) => {
      const message = form.getAttribute('data-confirm') || 'Bu işlemi yapmak istediğine emin misin?';
      if (!window.confirm(message)) {
        event.preventDefault();
      }
    });
  });
</script>"""
    html = f"""<!doctype html>
<html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover"><meta name="theme-color" content="#0f1222"><meta name="apple-mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-title" content="Hermes"><meta http-equiv="refresh" content="60"><title>Hermes</title><style>{DASHBOARD_CSS}</style></head><body class="public"><main><div class="hero"><div class="badge">Hermes</div><div class="actions public-actions"><a class="button secondary" href="{base_path}/settings">Ayarlar</a><form class="inline-form" method="post" action="{base_path}/test-pushover"><button class="button test" type="submit">Pushover</button></form><form class="inline-form" method="post" action="{base_path}/reset-notifications" data-confirm="Bildirim susturma hafızası sıfırlanacak ve hedef altında kalan fırsatlar için tek seferlik kontrol başlatılacak. Devam etmek istiyor musun?"><button class="button secondary" type="submit">Bildirim Sıfırla</button></form><form class="inline-form" method="post" action="{base_path}/reset-price-history" data-confirm="Min/maks fiyat geçmişi temizlenecek ve güncel fiyattan yeniden başlayacak. Devam etmek istiyor musun?"><button class="button secondary" type="submit">Min/Maks Sıfırla</button></form></div>{public_cycle_row}{notice_html}{_render_table()}{telegram_recent_html}<p class="footer">iPhone'da Safari paylaş menüsünden “Ana Ekrana Ekle” diyerek uygulama gibi kullanabilirsin.</p></div></main>{confirm_script}</body></html>"""
    return 200, html.encode("utf-8")


class _StatusHandler(BaseHTTPRequestHandler):
    def _redirect_with_message(self, flag_name: str, ok: bool, message: str) -> None:
        status = "ok" if ok else "fail"
        self.send_response(303)
        self.send_header("Location", f"?{flag_name}={status}&msg={urllib.parse.quote(message)}")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def do_GET(self) -> None:
        path = urllib.parse.urlparse(self.path).path.rstrip("/")
        status = 200
        if path == "/health":
            payload = b"ok\n"
            content_type = "text/plain; charset=utf-8"
        elif path == "/public" or path.startswith("/public/"):
            status, payload = _render_public_page(self.path)
            content_type = "text/html; charset=utf-8" if status == 200 else "text/plain; charset=utf-8"
        else:
            payload = _render_page(self.path)
            content_type = "text/html; charset=utf-8"
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self) -> None:
        content_length = int(self.headers.get("Content-Length", "0") or 0)
        if content_length:
            self.rfile.read(content_length)
        path = urllib.parse.urlparse(self.path).path.rstrip("/")
        if path.endswith("/reset-notifications"):
            ok, message = _reset_notifications_async()
            self._redirect_with_message("reset", ok, message)
            return
        if path.endswith("/reset-price-history"):
            ok, message = _reset_price_history()
            self._redirect_with_message("history", ok, message)
            return
        if not path.endswith("/test-pushover"):
            self.send_error(404)
            return
        ok, message = _send_test_notification()
        self._redirect_with_message("test", ok, message)

    def log_message(self, _format, *args) -> None:
        _ = args
        return


def run_dashboard() -> None:
    ThreadingHTTPServer(("0.0.0.0", WEB_PORT), _StatusHandler).serve_forever()


if __name__ == "__main__":
    run_dashboard()
