import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .constants import OPTIONS_PATH, PUSHOVER_URL, STATE_PATH, SUMMARY_PATH, TELEGRAM_ERROR_EVENTS_PATH, TELEGRAM_STATUS_PATH
from .storage import load_json
from .utils import (
    detect_site_from_url,
    normalize_item_key,
    parse_bool,
    parse_iso_datetime,
    repair_mojibake,
    site_label,
)

WEB_PORT = 8099
ADDON_SLUG = "hermes"

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
tbody tr.site-amazon { --site-bg:rgba(255,199,116,.10); --site-line:rgba(255,199,116,.48); --site-link:#ffd79a; } tbody tr.site-hepsiburada { --site-bg:rgba(255,153,112,.10); --site-line:rgba(255,153,112,.48); --site-link:#ffc1a5; } tbody tr.site-network { --site-bg:rgba(143,214,196,.10); --site-line:rgba(143,214,196,.48); --site-link:#aee7d8; } tbody tr.site-trendyol { --site-bg:rgba(245,170,196,.10); --site-line:rgba(245,170,196,.48); --site-link:#f7c1d3; } tbody tr.site-other { --site-bg:rgba(183,177,222,.10); --site-line:rgba(183,177,222,.42); --site-link:#cbc6ef; } tbody tr[class*='site-'] td { background:linear-gradient(90deg,var(--site-bg),rgba(30,33,57,.28)); } tbody tr[class*='site-'] td:first-child { border-left:4px solid var(--site-line); color:var(--site-link); font-weight:800; } tbody tr[class*='site-'] .product-cell a { color:var(--site-link); } tbody tr[class*='site-']:hover td { background:linear-gradient(90deg,rgba(255,255,255,.055),var(--site-bg)); }
.product-cell { max-width:360px; white-space:normal; line-height:1.22; } .product-cell a { color:#9ec0ff; text-decoration:none; } .product-cell a:hover { color:#d1b3ff; text-decoration:underline; } .product-cell span { display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; text-overflow:ellipsis; } .deal-row td { color:#b7f0dc; } .deal-row td:first-child { color:var(--site-link); } .deal-row .product-cell a { color:#b7f0dc; } .note { margin-top:18px; border-left:4px solid #b79ad6; padding:12px 14px; background:rgba(183,154,214,.15); border-radius:10px; font-size:13px; } .footer { margin-top:18px; font-size:12px; color:var(--muted); }
.public main { max-width:1180px; } .public .hero { padding:18px; } .public .badge { font-size:clamp(22px,4vw,36px); }
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


def _target_labels_for_page(page, targets, page_count):
    page_name = str(page.get("name") or "").strip()
    labels = []
    for target in targets:
        if not isinstance(target, dict):
            continue
        search_name = str(target.get("search_name") or "").strip()
        if search_name == page_name or (not search_name and page_count == 1):
            label = str(target.get("product_name") or target.get("name") or "").strip()
            if label:
                labels.append(label)
    return _unique_text(labels)


def _target_text(labels):
    if not labels:
        return "Aranan keyword belirtilmemiş"
    prefix = "Aranan keyword" if len(labels) == 1 else "Aranan keywordler"
    return f"{prefix}: {', '.join(labels)}"


def _context_for_product(item):
    url = str(item.get("url") or "").strip()
    if not url:
        return None
    try:
        site = detect_site_from_url(url)
        seller = site_label(site)
    except Exception:  # noqa: BLE001
        site = str(item.get("site") or "urun").strip().lower()
        seller = _site_name(site, False)
    name = str(item.get("name") or url).strip()
    key = normalize_item_key("product", site, url)
    return key, {
        "title": f"{seller}: {name}",
        "meta": "Ürün linki kontrol edilirken hata oluştu.",
        "url": url,
        "urls": [url],
        "keywords": [name],
    }


def _search_urls_from_page(item):
    urls = []
    for field_name in ("search_url", "search_url_2"):
        url = str(item.get(field_name) or "").strip()
        if url and url not in urls:
            urls.append(url)
    return urls


def _context_for_search_page(page, targets, page_count):
    name = str(page.get("name") or "").strip()
    urls = _search_urls_from_page(page)
    if not name or not urls:
        return None
    labels = _target_labels_for_page(page, targets, page_count)
    key = normalize_item_key("amazon_search", name, *urls)
    return key, {
        "title": f"Amazon arama: {name}",
        "meta": _target_text(labels),
        "url": urls[0],
        "urls": urls,
        "keywords": labels,
    }


def _error_contexts(options):
    products = options.get("products") if isinstance(options.get("products"), list) else []
    pages = options.get("amazon_search_pages", options.get("search_pages", []))
    targets = options.get("amazon_search_targets", options.get("search_targets", []))
    pages = pages if isinstance(pages, list) else []
    targets = targets if isinstance(targets, list) else []
    contexts = {}
    for item in products:
        if isinstance(item, dict):
            context = _context_for_product(item)
            if context:
                key, value = context
                contexts[key] = value
    for page in pages:
        if isinstance(page, dict):
            context = _context_for_search_page(page, targets, len(pages))
            if context:
                key, value = context
                contexts[key] = value
    return contexts


def _state_target_keywords(state_entry):
    targets = state_entry.get("targets")
    if not isinstance(targets, dict):
        return []
    labels = []
    for key in targets.keys():
        label = str(key or "").replace("_", " ").strip()
        if label:
            labels.append(label)
    return _unique_text(labels)


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
    is_search = isinstance(state_entry.get("targets"), dict)
    context = _find_error_context(state_key, state_entry, raw_error, contexts)
    failed_links = _error_link_details(raw_error)
    keywords = context.get("keywords") or (_state_target_keywords(state_entry) if is_search else [])
    keyword_text = _target_text(keywords) if is_search else ""
    for failed_link in failed_links:
        if keyword_text:
            failed_link["keywords"] = keyword_text
    url_text = (
        (failed_links[0]["url"] if failed_links else "")
        or str(context.get("url") or state_entry.get("url") or "").strip()
        or _extract_first_url(raw_error)
    )
    title = context.get("title") or _site_name(state_entry.get("site"), is_search)
    meta = context.get("meta") or keyword_text
    if not meta:
        meta = "Amazon arama sayfası kontrol edilirken hata oluştu." if is_search else "Ürün kontrol edilirken hata oluştu."
    elif is_search and "keyword" not in meta.casefold():
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
    products = options.get("products") if isinstance(options.get("products"), list) else []
    pages = options.get("amazon_search_pages", options.get("search_pages", []))
    targets = options.get("amazon_search_targets", options.get("search_targets", []))
    pages = pages if isinstance(pages, list) else []
    targets = targets if isinstance(targets, list) else []
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
            nested = value.get("targets")
            if isinstance(nested, dict):
                for target_state in nested.values():
                    if not isinstance(target_state, dict):
                        continue
                    checked_at = parse_iso_datetime(target_state.get("last_checked_at"))
                    if checked_at:
                        last_checks.append(checked_at.astimezone())

    interval_seconds = int(options.get("interval_seconds") or int(options.get("interval_minutes", 1) or 1) * 60)
    last_check = max(last_checks) if last_checks else None
    return {
        "interval": interval_seconds,
        "products": len(products),
        "amazon_pages": len(pages),
        "amazon_targets": len(targets),
        "last_check": last_check.strftime("%Y-%m-%d %H:%M:%S") if last_check else "-",
        "next_check": (last_check + timedelta(seconds=interval_seconds)).strftime("%Y-%m-%d %H:%M:%S") if last_check else "-",
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
        "duplicates": status.get("duplicates_suppressed", 0),
        "last_check": status.get("last_check") or "-",
        "last_notification": status.get("last_notification") or "-",
        "errors": _telegram_error_count_24h(),
        "recent_notifications": status.get("recent_notifications") if isinstance(status.get("recent_notifications"), list) else [],
    }


def _render_table_row(row):
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
    price = escape(str(row.get("price", "-")))
    target = escape(str(row.get("target", "-")))
    difference = escape(str(row.get("difference", "-")))
    price_range = escape(str(row.get("price_range") or f"{row.get('min_price', '-')} / {row.get('max_price', '-') }"))
    row_classes = [_site_theme_class(seller_text)]
    if _is_target_hit(row):
        row_classes.append("deal-row")
    row_class = f' class="{" ".join(row_classes)}"'
    return (
        f"<tr{row_class}><td>{seller}</td>"
        f'<td class="product-cell" title="{product_title}">{label}</td>'
        f"<td>{price}</td><td>{target}</td><td>{difference}</td><td>{price_range}</td></tr>"
    )


def _render_table_section(title, rows, empty_text, extra_class=""):
    if rows:
        body = "".join(_render_table_row(row) for row in rows)
    else:
        body = f"<tr class='empty-row'><td colspan='6'>{escape(empty_text)}</td></tr>"
    return f"""
      <div class="table-section {extra_class}">
        <h3>{escape(title)}</h3>
        <div class="table-wrap">
          <table>
            <thead><tr><th>Satıcı</th><th>Ürün Adı</th><th>Güncel</th><th>Hedef</th><th>Fark</th><th>Min / Maks</th></tr></thead>
            <tbody>{body}</tbody>
          </table>
        </div>
      </div>
    """


def _render_table():
    payload = load_json(SUMMARY_PATH, {})
    rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
    if not rows:
        return """
        <section class="summary-panel">
          <div class="summary-head"><h2>Özet Tablo</h2><span>Henüz tablo yok</span></div>
          <p class="empty-table">İlk kontrol döngüsü tamamlandığında son fiyat tablosu burada görünecek.</p>
        </section>
        """

    deal_rows = [row for row in rows if _is_target_hit(row)]
    watch_rows = [row for row in rows if not _is_target_hit(row)]
    checked_at = escape(str(payload.get("checked_at") or "-"))
    row_count = escape(str(payload.get("row_count") or len(rows)))
    deal_count = escape(str(len(deal_rows)))
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
    )
    return f"""
    <section class="summary-panel">
      <div class="summary-head"><h2>Özet Tablo</h2><span>{checked_at} · {row_count} ürün · {deal_count} fırsat</span></div>
      {sections}
    </section>
    """


def _render_telegram_panel(summary):
    telegram = summary.get("telegram") or {}
    state = str(telegram.get("state") or "Pasif")
    state_class = "status-ok" if state == "Dinleniyor" else ("status-warn" if state in {"Pasif", "Kod bekleniyor"} else "status-error")
    cards = [
        ("Telegram durumu", state, state_class),
        ("Telegram kanalları", telegram.get("channels", 0), ""),
        ("Keyword sayısı", telegram.get("keywords", 0), ""),
        ("Gönderilen bildirim", telegram.get("notifications", 0), ""),
        ("Susturulan tekrar", telegram.get("duplicates", 0), ""),
        ("Son Telegram kontrolü", telegram.get("last_check", "-"), ""),
        ("Son Telegram bildirimi", telegram.get("last_notification", "-"), ""),
        ("Telegram hata sayısı (24s)", telegram.get("errors", 0), "status-error" if int(telegram.get("errors", 0) or 0) else ""),
    ]
    card_html = "".join(
        f"<section class='card {escape(str(css))}'><span>{escape(str(label))}</span><strong>{escape(str(value))}</strong></section>"
        for label, value, css in cards
    )
    recent_html = _render_telegram_recent_notifications(telegram.get("recent_notifications") or [])
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
        price = str(item.get("price") or "").strip()
        price_text = f" · {escape(price)} TL" if price else ""
        message = escape(str(item.get("message") or ""))
        url = str(item.get("url") or "").strip()
        title = f"{keyword}{price_text}"
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
    test_message = params.get("msg", [""])[0]
    status = "Çalışıyor" if summary["configured"] else "Ayar bekliyor"
    status_class = "status-ok" if summary["configured"] else "status-warn"
    error_class = "status-error" if int(summary["errors"]) > 0 else ""
    error_details_html = _render_error_details(summary.get("error_details") or [])

    cards = [
        ("Durum", status, status_class),
        ("Kontrol aralığı", f"{summary['interval']} saniye", ""),
        ("Ürün linkleri", summary["products"], ""),
        ("Amazon arama sayfaları", summary["amazon_pages"], ""),
        ("Amazon arama hedefleri", summary["amazon_targets"], ""),
        ("Son kontrol", summary["last_check"], ""),
        ("Sonraki kontrol", summary["next_check"], ""),
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
    if test_status in {"ok", "fail"}:
        notice_class = "notice-ok" if test_status == "ok" else "notice-fail"
        notice_html = f"<p class='notice {notice_class}'>{escape(test_message)}</p>"

    html = f"""<!doctype html>
<html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta http-equiv="refresh" content="60"><title>Hermes</title><style>{DASHBOARD_CSS}</style></head><body><main><div class="hero"><div class="badge">Hermes</div><p>Ürün linkleri çok siteli çalışır; Amazon arama sayfaları Amazon'a özel mod olarak korunur. Telegram dinleme aktifse fırsat kanalları da aynı Pushover hattına bağlanır.</p><div class="actions"><a class="button primary" href="{log_url}" target="_top">LOG</a><a class="button secondary" href="{app_url}" target="_top">Config</a><form class="inline-form" method="post" action="./test-pushover"><button class="button test" type="submit">Pushover</button></form></div>{notice_html}<div class="grid">{card_html}{error_card_html}</div>{_render_telegram_panel(summary)}{_render_table()}<p class="note">LOG butonu log sekmesini, Config butonu yapılandırma sekmesini açar. Pushover butonu test bildirimi gönderir.</p><p class="footer">Sayfa 60 saniyede bir otomatik yenilenir.</p></div></main></body></html>"""
    return html.encode("utf-8")


def _public_token_from_path(path: str) -> str:
    parsed = urllib.parse.urlparse(path)
    parts = [urllib.parse.unquote(part) for part in parsed.path.split("/") if part]
    if len(parts) >= 2 and parts[0] == "public":
        return parts[1]
    params = urllib.parse.parse_qs(parsed.query)
    return str(params.get("token", [""])[0]).strip()


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
    checked_at = escape(str(payload.get("checked_at") or "-")) if isinstance(payload, dict) else "-"
    html = f"""<!doctype html>
<html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta http-equiv="refresh" content="60"><title>Hermes Tablo</title><style>{DASHBOARD_CSS}</style></head><body class="public"><main><div class="hero"><div class="badge">Hermes</div><p>Salt okunur fiyat tablosu. Son güncelleme: {checked_at}</p>{_render_table()}<p class="footer">Sayfa 60 saniyede bir otomatik yenilenir.</p></div></main></body></html>"""
    return 200, html.encode("utf-8")


class _StatusHandler(BaseHTTPRequestHandler):
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
        if not urllib.parse.urlparse(self.path).path.rstrip("/").endswith("/test-pushover"):
            self.send_error(404)
            return
        ok, message = _send_test_notification()
        status = "ok" if ok else "fail"
        self.send_response(303)
        self.send_header("Location", f"?test={status}&msg={urllib.parse.quote(message)}")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def log_message(self, _format, *args) -> None:
        _ = args
        return


def run_dashboard() -> None:
    ThreadingHTTPServer(("0.0.0.0", WEB_PORT), _StatusHandler).serve_forever()


if __name__ == "__main__":
    run_dashboard()
