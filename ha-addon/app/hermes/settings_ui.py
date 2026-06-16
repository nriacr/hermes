import json
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
from html import escape

from .constants import OPTIONS_PATH
from .logging_utils import log
from .storage import load_json, save_json
from .utils import parse_bool

ADDON_SLUG = "hermes"
SUPERVISOR_BASE_URL = "http://supervisor"

SETTINGS_CSS = """
:root { color-scheme: dark; --bg:#0f1222; --panel:#171a30; --card:#1e2139; --line:#313658; --text:#e8eaf8; --muted:#a6abd1; --accent:#c7a6ff; --accent2:#8ed6d2; --ok:#7fdcb8; --bad:#ff9cb5; }
* { box-sizing:border-box; } body { margin:0; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; background:radial-gradient(circle at top left,#1f2240,var(--bg) 56%); color:var(--text); font-size:14px; }
main { max-width:980px; margin:0 auto; padding:28px 18px 44px; } .hero { border:1px solid var(--line); border-radius:22px; padding:22px; background:var(--panel); box-shadow:0 18px 42px rgba(0,0,0,.35); }
h1 { margin:0 0 8px; font-size:34px; letter-spacing:-.04em; } h2 { margin:24px 0 10px; font-size:18px; } p { margin:0; color:var(--muted); line-height:1.5; font-size:13px; }
.actions { display:flex; flex-wrap:wrap; gap:10px; margin:18px 0; } .button { display:inline-flex; align-items:center; justify-content:center; min-height:40px; padding:0 14px; border-radius:13px; border:1px solid transparent; text-decoration:none; font-weight:800; font-size:13px; cursor:pointer; }
.button.primary { color:#14172a; background:linear-gradient(135deg,var(--accent),var(--accent2)); } .button.secondary { color:var(--text); background:#2a2f4d; border-color:var(--line); }
.notice { margin:14px 0; padding:11px 13px; border-radius:12px; font-weight:700; font-size:13px; } .notice-ok { color:#c6f7e6; background:rgba(127,220,184,.14); border:1px solid rgba(127,220,184,.38); } .notice-fail { color:#ffd8e3; background:rgba(255,156,181,.14); border:1px solid rgba(255,156,181,.38); }
.settings-section { margin-top:18px; border:1px solid var(--line); border-radius:18px; padding:16px; background:var(--card); } details { border:1px solid var(--line); border-radius:14px; background:#181c32; margin:9px 0; overflow:hidden; } summary { cursor:pointer; padding:13px 14px; font-weight:900; color:#f0f2ff; list-style:none; } summary::-webkit-details-marker { display:none; } summary::before { content:'\u25b8'; display:inline-block; margin-right:8px; color:var(--accent2); } details[open] summary::before { transform:rotate(90deg); }
.form-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:12px; padding:0 14px 14px; } label { display:grid; gap:6px; color:var(--muted); font-size:12px; font-weight:700; } input[type='text'], input[type='number'], input[type='url'] { width:100%; min-height:40px; border-radius:11px; border:1px solid var(--line); background:#101428; color:var(--text); padding:0 11px; font-size:13px; }
.checkbox-row { display:flex; align-items:center; gap:9px; min-height:40px; color:var(--text); } .danger { color:#ffd8e3; } .footer-note { margin-top:14px; border-left:4px solid #b79ad6; padding:12px 14px; background:rgba(183,154,214,.15); border-radius:10px; font-size:13px; }
"""


def _as_list(value):
    return value if isinstance(value, list) else []


def _first(form, key, default=""):
    values = form.get(key)
    if not values:
        return default
    return str(values[0]).strip()


def _number(value):
    text = str(value or "").strip()
    if text == "":
        return None
    try:
        return int(text) if text.isdigit() else float(text)
    except ValueError:
        return text


def _field(prefix, name, label, value="", field_type="text", required=False):
    required_attr = " required" if required else ""
    return (
        f"<label>{escape(label)}"
        f"<input type='{field_type}' name='{escape(prefix + name, quote=True)}' value='{escape(str(value or ''), quote=True)}'{required_attr}>"
        "</label>"
    )


def _checkbox(prefix, name, label, checked=True, danger=False):
    checked_attr = " checked" if parse_bool(checked, default=True) else ""
    danger_class = " danger" if danger else ""
    return (
        f"<label class='checkbox-row{danger_class}'>"
        f"<input type='checkbox' name='{escape(prefix + name, quote=True)}' value='1'{checked_attr}>"
        f"{escape(label)}</label>"
    )


def _summary_name(item, fallback):
    if isinstance(item, dict):
        value = str(item.get("name") or item.get("product_name") or fallback).strip()
        return value or fallback
    return fallback


def _search_target_title(item, fallback):
    if isinstance(item, dict):
        value = str(item.get("product_name") or item.get("name") or fallback).strip()
        return value or fallback
    return fallback


def _details(title, prefix, inner, open_when_empty=False):
    open_attr = " open" if open_when_empty else ""
    return f"<details{open_attr}><summary>{escape(title)}</summary><div class='form-grid'>{inner}</div></details>"


def _product_form(item, index, is_new=False):
    prefix = f"products_{index}_"
    title = "Yeni ürün ekle" if is_new else _summary_name(item, f"Ürün {index + 1}")
    inner = "".join(
        [
            _field(prefix, "name", "Ad", item.get("name", ""), required=not is_new),
            _field(prefix, "url", "URL", item.get("url", ""), "url", required=not is_new),
            _field(prefix, "target_price", "Hedef fiyat", item.get("target_price", ""), "number", required=not is_new),
            _field(prefix, "check_interval_minutes", "Özel kontrol aralığı (dk)", item.get("check_interval_minutes", ""), "number"),
            _checkbox(prefix, "notify_once_in_24H", "24 saat içinde aynı bildirimi tekrar gönderme", item.get("notify_once_in_24H", True)),
            _checkbox(prefix, "active", "Aktif", item.get("active", True)),
            _checkbox(prefix, "delete", "Sil", False, danger=True) if not is_new else "",
        ]
    )
    return _details(title, prefix, inner, open_when_empty=is_new)


def _search_page_form(item, index, is_new=False):
    prefix = f"amazon_search_pages_{index}_"
    title = "Yeni Amazon arama sayfası ekle" if is_new else _summary_name(item, f"Amazon arama sayfası {index + 1}")
    inner = "".join(
        [
            _field(prefix, "name", "Ad", item.get("name", ""), required=not is_new),
            _field(prefix, "search_url", "Arama URL", item.get("search_url", ""), "url", required=not is_new),
            _field(prefix, "search_url_2", "Arama URL 2", item.get("search_url_2", ""), "url"),
            _field(prefix, "max_items_to_scan", "Taranacak maksimum ürün", item.get("max_items_to_scan", ""), "number"),
            _checkbox(prefix, "delete", "Sil", False, danger=True) if not is_new else "",
        ]
    )
    return _details(title, prefix, inner, open_when_empty=is_new)


def _search_target_form(item, index, is_new=False):
    prefix = f"amazon_search_targets_{index}_"
    title = "Yeni Amazon arama hedefi ekle" if is_new else _search_target_title(item, f"Amazon arama hedefi {index + 1}")
    inner = "".join(
        [
            _field(prefix, "search_name", "Arama sayfası adı", item.get("search_name", "")),
            _field(prefix, "product_name", "Ürün adı", item.get("product_name") or item.get("name", ""), required=not is_new),
            _field(prefix, "target_price", "Hedef fiyat", item.get("target_price", ""), "number", required=not is_new),
            _checkbox(prefix, "notify_once_in_24H", "24 saat içinde aynı bildirimi tekrar gönderme", item.get("notify_once_in_24H", True)),
            _checkbox(prefix, "active", "Aktif", item.get("active", True)),
            _checkbox(prefix, "delete", "Sil", False, danger=True) if not is_new else "",
        ]
    )
    return _details(title, prefix, inner, open_when_empty=is_new)


def _section(title, items, renderer, section_name):
    safe_items = _as_list(items)
    rows = [renderer(item if isinstance(item, dict) else {}, index) for index, item in enumerate(safe_items)]
    rows.append(renderer({}, len(safe_items), is_new=True))
    return (
        f"<section class='settings-section'><h2>{escape(title)}</h2>"
        f"<input type='hidden' name='{escape(section_name)}_count' value='{len(safe_items) + 1}'>"
        f"{''.join(rows)}</section>"
    )


def _bool_from_form(form, key, default=False):
    return key in form if key in form else default


def _build_products(form):
    products = []
    count = int(_first(form, "products_count", "0") or 0)
    for index in range(count):
        prefix = f"products_{index}_"
        if _bool_from_form(form, prefix + "delete"):
            continue
        name = _first(form, prefix + "name")
        url = _first(form, prefix + "url")
        target = _first(form, prefix + "target_price")
        interval = _first(form, prefix + "check_interval_minutes")
        if not any([name, url, target, interval]):
            continue
        if not name or not url or not target:
            raise ValueError("Ürün eklerken ad, URL ve hedef fiyat alanları dolu olmalı.")
        item = {
            "name": name,
            "url": url,
            "target_price": _number(target),
            "notify_once_in_24H": _bool_from_form(form, prefix + "notify_once_in_24H"),
            "active": _bool_from_form(form, prefix + "active"),
        }
        if interval:
            item["check_interval_minutes"] = _number(interval)
        products.append(item)
    return products


def _build_search_pages(form):
    pages = []
    count = int(_first(form, "amazon_search_pages_count", "0") or 0)
    for index in range(count):
        prefix = f"amazon_search_pages_{index}_"
        if _bool_from_form(form, prefix + "delete"):
            continue
        name = _first(form, prefix + "name")
        url = _first(form, prefix + "search_url")
        url_2 = _first(form, prefix + "search_url_2")
        max_items = _first(form, prefix + "max_items_to_scan")
        if not any([name, url, url_2, max_items]):
            continue
        if not name or not url:
            raise ValueError("Amazon arama sayfası eklerken ad ve arama URL alanları dolu olmalı.")
        item = {"name": name, "search_url": url}
        if url_2:
            item["search_url_2"] = url_2
        if max_items:
            item["max_items_to_scan"] = _number(max_items)
        pages.append(item)
    return pages


def _build_search_targets(form):
    targets = []
    count = int(_first(form, "amazon_search_targets_count", "0") or 0)
    for index in range(count):
        prefix = f"amazon_search_targets_{index}_"
        if _bool_from_form(form, prefix + "delete"):
            continue
        search_name = _first(form, prefix + "search_name")
        product_name = _first(form, prefix + "product_name")
        target_price = _first(form, prefix + "target_price")
        if not any([search_name, product_name, target_price]):
            continue
        if not product_name or not target_price:
            raise ValueError("Amazon arama hedefi eklerken ürün adı ve hedef fiyat alanları dolu olmalı.")
        item = {
            "product_name": product_name,
            "target_price": _number(target_price),
            "notify_once_in_24H": _bool_from_form(form, prefix + "notify_once_in_24H"),
            "active": _bool_from_form(form, prefix + "active"),
        }
        if search_name:
            item["search_name"] = search_name
        targets.append(item)
    return targets


def _current_addon_slug():
    hostname = os.getenv("HOSTNAME", "").strip()
    hyphen_slug = ADDON_SLUG.replace("_", "-")
    if hostname.endswith(f"-{hyphen_slug}"):
        repository_id = hostname[: -(len(hyphen_slug) + 1)]
        if repository_id:
            return f"{repository_id}_{ADDON_SLUG}"
    return hostname.replace("-", "_") if hostname else f"local_{ADDON_SLUG}"


def _supervisor_headers():
    token = os.getenv("SUPERVISOR_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Supervisor token bulunamadı. Hermes 1.0.33 veya üzeri sürüme güncelleyip add-on'u yeniden başlat.")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _post_supervisor(path, payload=None, timeout=8):
    data = json.dumps(payload or {}).encode("utf-8")
    request = urllib.request.Request(
        f"{SUPERVISOR_BASE_URL}{path}",
        data=data,
        method="POST",
        headers=_supervisor_headers(),
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"Supervisor API hata verdi: {exc.code} {detail[:240]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Supervisor API bağlantısı kurulamadı: {exc.reason}") from exc


def _save_options_to_supervisor(options):
    slug = urllib.parse.quote(_current_addon_slug(), safe="")
    _post_supervisor(f"/addons/{slug}/options", {"options": options})


def _restart_addon():
    try:
        slug = urllib.parse.quote(_current_addon_slug(), safe="")
        _post_supervisor(f"/addons/{slug}/restart", {}, timeout=5)
    except Exception as exc:  # noqa: BLE001
        log(f"Ayarlar kaydedildi ama Hermes otomatik yeniden başlatılamadı: {exc}")


def _restart_addon_later(delay_seconds=2.0):
    timer = threading.Timer(delay_seconds, _restart_addon)
    timer.daemon = True
    timer.start()


def render_settings_page(path="/"):
    options = load_json(OPTIONS_PATH, {})
    if not isinstance(options, dict):
        options = {}
    params = urllib.parse.parse_qs(urllib.parse.urlparse(path).query)
    status = params.get("saved", [""])[0]
    message = params.get("msg", [""])[0]
    notice = ""
    if status in {"ok", "fail"}:
        css = "notice-ok" if status == "ok" else "notice-fail"
        notice = f"<p class='notice {css}'>{escape(message)}</p>"
    html = f"""<!doctype html>
<html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Hermes Ayarlar</title><style>{SETTINGS_CSS}</style></head>
<body><main><div class="hero"><h1>Hermes Ayarlar</h1><p>Listelerde yalnızca adlar görünür; satıra tıklayınca ayrıntılar açılır. Kaydettiğinde Home Assistant config güncellenir ve Hermes kısa süre içinde yeniden başlatılır.</p><div class="actions"><a class="button secondary" href="./">Ana ekran</a></div>{notice}<form method="post" action="./settings/save">
{_section("Ürünler", options.get("products"), _product_form, "products")}
{_section("Amazon arama sayfaları", options.get("amazon_search_pages"), _search_page_form, "amazon_search_pages")}
{_section("Amazon arama hedefleri", options.get("amazon_search_targets"), _search_target_form, "amazon_search_targets")}
<div class="actions"><button class="button primary" type="submit">Kaydet</button><a class="button secondary" href="./">Vazgeç</a></div>
<p class="footer-note">Kaydet sonrası ekran birkaç saniye içinde “yeniden başlatılıyor” mesajı verir. Hermes yeniden başlarken sayfa kısa süre yanıt vermeyebilir; 10-20 saniye sonra yenileyebilirsin.</p>
</form></div></main></body></html>"""
    return html.encode("utf-8")


def handle_settings_save(body):
    try:
        form = urllib.parse.parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True)
        options = load_json(OPTIONS_PATH, {})
        if not isinstance(options, dict):
            options = {}
        options["products"] = _build_products(form)
        options["amazon_search_pages"] = _build_search_pages(form)
        options["amazon_search_targets"] = _build_search_targets(form)
        _save_options_to_supervisor(options)
        save_json(OPTIONS_PATH, options)
        log("Ayarlar Home Assistant config'e kaydedildi; Hermes yeniden başlatılacak.")
        _restart_addon_later()
        return True, "Ayarlar Home Assistant config'e kaydedildi. Hermes yeniden başlatılıyor; 10-20 saniye sonra sayfayı yenileyebilirsin."
    except Exception as exc:  # noqa: BLE001
        return False, f"Ayarlar kaydedilemedi: {exc}"
