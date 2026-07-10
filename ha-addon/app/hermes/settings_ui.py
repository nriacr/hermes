import json
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
from html import escape

from .config_loader import DEFAULT_TELEGRAM_CHANNELS
from .constants import OPTIONS_PATH
from .logging_utils import log
from .storage import load_json, save_json
from .utils import parse_bool, watch_name_required_for_url

ADDON_SLUG = "hermes"
SUPERVISOR_BASE_URL = "http://supervisor"
WATCH_URL_FIELDS = ("url_1", "url_2", "url_3", "url_4", "url_5")

SETTINGS_CSS = """
:root { color-scheme: dark; --bg:#0f1222; --panel:#171a30; --card:#1e2139; --line:#313658; --text:#e8eaf8; --muted:#a6abd1; --accent:#c7a6ff; --accent2:#8ed6d2; --ok:#7fdcb8; --bad:#ff9cb5; }
* { box-sizing:border-box; } body { margin:0; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; background:radial-gradient(circle at top left,#1f2240,var(--bg) 56%); color:var(--text); font-size:14px; }
main { max-width:980px; margin:0 auto; padding:28px 18px 44px; } .hero { border:1px solid var(--line); border-radius:22px; padding:22px; background:var(--panel); box-shadow:0 18px 42px rgba(0,0,0,.35); }
h1 { margin:0 0 8px; font-size:34px; letter-spacing:-.04em; } h2 { margin:24px 0 10px; font-size:18px; } p { margin:0; color:var(--muted); line-height:1.5; font-size:13px; }
.actions { display:flex; flex-wrap:wrap; gap:10px; margin:18px 0; } .button { display:inline-flex; align-items:center; justify-content:center; min-height:40px; padding:0 14px; border-radius:13px; border:1px solid transparent; text-decoration:none; font-weight:800; font-size:13px; cursor:pointer; }
.button.primary { color:#14172a; background:linear-gradient(135deg,var(--accent),var(--accent2)); } .button.secondary { color:var(--text); background:#2a2f4d; border-color:var(--line); }
.notice { margin:14px 0; padding:11px 13px; border-radius:12px; font-weight:700; font-size:13px; } .notice-ok { color:#c6f7e6; background:rgba(127,220,184,.14); border:1px solid rgba(127,220,184,.38); } .notice-fail { color:#ffd8e3; background:rgba(255,156,181,.14); border:1px solid rgba(255,156,181,.38); }
.settings-section { margin-top:18px; border:1px solid var(--line); border-radius:18px; padding:16px; background:var(--card); } details { border:1px solid var(--line); border-radius:14px; background:#181c32; margin:9px 0; overflow:hidden; } summary { cursor:pointer; padding:13px 14px; font-weight:900; color:#f0f2ff; list-style:none; } summary::-webkit-details-marker { display:none; } summary::before { content:'\u25b8'; display:inline-block; margin-right:8px; color:var(--accent2); } details[open] summary::before { transform:rotate(90deg); }
.form-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:12px; padding:0 14px 14px; } label { display:grid; gap:6px; color:var(--muted); font-size:12px; font-weight:700; } input[type='text'], input[type='number'], input[type='url'], textarea { width:100%; min-height:40px; border-radius:11px; border:1px solid var(--line); background:#101428; color:var(--text); padding:10px 11px; font-size:13px; font-family:inherit; } textarea { resize:vertical; line-height:1.35; }
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


def _textarea(prefix, name, label, values=None, rows=5):
    if isinstance(values, list):
        value = "\n".join(str(item) for item in values)
    else:
        value = str(values or "")
    return (
        f"<label>{escape(label)}"
        f"<textarea name='{escape(prefix + name, quote=True)}' rows='{int(rows)}'>{escape(value)}</textarea>"
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
        value = str(item.get("name") or fallback).strip()
        return value or fallback
    return fallback


def _watch_urls_for_form(item):
    urls = []
    if isinstance(item, dict):
        for field_name in WATCH_URL_FIELDS:
            url = str(item.get(field_name) or "").strip()
            if url and url not in urls:
                urls.append(url)
    return urls[: len(WATCH_URL_FIELDS)]


def _details(title, prefix, inner, open_when_empty=False):
    open_attr = " open" if open_when_empty else ""
    return f"<details{open_attr}><summary>{escape(title)}</summary><div class='form-grid'>{inner}</div></details>"


def _watch_form(item, index, is_new=False):
    prefix = f"watches_{index}_"
    title = "Yeni takip ekle" if is_new else _summary_name(item, f"Takip {index + 1}")
    urls = _watch_urls_for_form(item)
    max_items = item.get("max_items_to_scan", 24 if is_new else "")
    notify_once = True if is_new else item.get("notify_once_in_24H", True)
    active = True if is_new else item.get("active", True)
    inner = "".join(
        [
            _field(prefix, "name", "Ad (ürün linklerinde boş bırakılabilir)", item.get("name", "")),
            _field(prefix, "target_price", "Hedef fiyat", item.get("target_price", ""), "number", required=not is_new),
            _field(prefix, "size", "Beden", item.get("size", "")),
            *[
                _field(
                    prefix,
                    field_name,
                    f"Link {url_index}",
                    urls[url_index - 1] if len(urls) >= url_index else "",
                    "url",
                )
                for url_index, field_name in enumerate(WATCH_URL_FIELDS, start=1)
            ],
            _field(prefix, "max_items_to_scan", "Arama linklerinde taranacak maksimum ürün", max_items, "number"),
            _field(prefix, "check_interval_minutes", "Özel kontrol aralığı (dk)", item.get("check_interval_minutes", ""), "number"),
            _checkbox(prefix, "notify_once_in_24H", "24 saat içinde aynı bildirimi tekrar gönderme", notify_once),
            _checkbox(prefix, "active", "Aktif", active),
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


def _telegram_section(options):
    channels = options.get("channels")
    if not isinstance(channels, list):
        channels = DEFAULT_TELEGRAM_CHANNELS
    keywords = options.get("keywords") if isinstance(options.get("keywords"), list) else []
    exclude_keywords = options.get("exclude_keywords") if isinstance(options.get("exclude_keywords"), list) else []
    inner = "".join(
        [
            _checkbox("", "telegram_enabled", "Telegram takip aktif", options.get("telegram_enabled", False)),
            _field("", "api_id", "Telegram API ID", options.get("api_id", "")),
            _field("", "api_hash", "Telegram API Hash", options.get("api_hash", "")),
            _field("", "phone_number", "Telefon numarası", options.get("phone_number", "")),
            _field("", "verification_code", "Telegram doğrulama kodu", options.get("verification_code", "")),
            _field("", "session_name", "Session adı", options.get("session_name", "telegram_keyword_alert")),
            _textarea("", "channels", "Kanallar (her satıra bir kanal)", channels, rows=7),
            _textarea("", "keywords", "Keyword'ler (her satıra bir keyword)", keywords, rows=5),
            _textarea("", "exclude_keywords", "Hariç tutulacak keyword'ler", exclude_keywords, rows=4),
        ]
    )
    return (
        "<section class='settings-section'><h2>Telegram takip</h2>"
        f"<details><summary>Telegram ayarları</summary><div class='form-grid'>{inner}</div></details>"
        "<p class='footer-note'>Telegram takip aktifse api_id, api_hash, telefon numarası, kanal ve keyword alanları dolu olmalı. Mesajda keyword geçerse ve exclude filtresine takılmazsa bildirim gönderilir.</p>"
        "</section>"
    )


def _bool_from_form(form, key, default=False):
    return key in form if key in form else default


def _build_watches(form):
    watches = []
    count = int(_first(form, "watches_count", "0") or 0)
    for index in range(count):
        prefix = f"watches_{index}_"
        if _bool_from_form(form, prefix + "delete"):
            continue
        name = _first(form, prefix + "name")
        target = _first(form, prefix + "target_price")
        size = _first(form, prefix + "size")
        max_items = _first(form, prefix + "max_items_to_scan")
        interval = _first(form, prefix + "check_interval_minutes")
        urls = []
        for field_name in WATCH_URL_FIELDS:
            url = _first(form, prefix + field_name)
            if url and url not in urls:
                urls.append(url)
        if not any([name, target, size, max_items, interval, *urls]):
            continue
        if not target or not urls:
            raise ValueError("Takip eklerken hedef fiyat ve en az bir link alanı dolu olmalı.")
        if not name and any(watch_name_required_for_url(url) for url in urls):
            raise ValueError("Arama linkleri için Ad alanı zorunlu. Ürün linklerinde boş bırakılabilir.")
        item = {
            "name": name,
            "target_price": _number(target),
            "notify_once_in_24H": _bool_from_form(form, prefix + "notify_once_in_24H"),
            "active": _bool_from_form(form, prefix + "active"),
        }
        if size:
            item["size"] = size
        for url_index, url in enumerate(urls, start=1):
            item[f"url_{url_index}"] = url
        if max_items:
            item["max_items_to_scan"] = _number(max_items)
        if interval:
            item["check_interval_minutes"] = _number(interval)
        watches.append(item)
    return watches


def _list_from_form(form, key):
    raw_value = _first(form, key)
    if not raw_value:
        return []
    values = []
    seen = set()
    for line in raw_value.replace(",", "\n").splitlines():
        value = line.strip()
        if not value or value.casefold() in seen:
            continue
        seen.add(value.casefold())
        values.append(value)
    return values


def _update_telegram_options(options, form):
    options["telegram_enabled"] = _bool_from_form(form, "telegram_enabled")
    options["api_id"] = _first(form, "api_id")
    options["api_hash"] = _first(form, "api_hash")
    options["phone_number"] = _first(form, "phone_number")
    options["verification_code"] = _first(form, "verification_code")
    options["session_name"] = _first(form, "session_name", "telegram_keyword_alert") or "telegram_keyword_alert"
    options["channels"] = _list_from_form(form, "channels") or DEFAULT_TELEGRAM_CHANNELS
    options["keywords"] = _list_from_form(form, "keywords")
    options["exclude_keywords"] = _list_from_form(form, "exclude_keywords")


def _clean_editable_options(options):
    keep_keys = (
        "interval_seconds",
        "request_delay_min_seconds",
        "request_delay_max_seconds",
        "pushover_user_key",
        "pushover_api_token",
        "public_dashboard_enabled",
        "public_dashboard_token",
    )
    return {key: options[key] for key in keep_keys if key in options}


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
<body><main><div class="hero"><h1>Hermes Ayarlar</h1><p>Listelerde yalnızca adlar görünür; satıra tıklayınca ayrıntılar açılır. Takip edilenler bölümünde aynı kayıt altına en fazla 5 link ekleyebilirsin; Hermes siteyi ve link tipini otomatik algılar.</p><div class="actions"><a class="button secondary" href="./">Ana ekran</a></div>{notice}<form method="post" action="./settings/save">
{_section("Takip edilenler", options.get("takip_edilenler"), _watch_form, "watches")}
{_telegram_section(options)}
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
        options = _clean_editable_options(options)
        options["takip_edilenler"] = _build_watches(form)
        _update_telegram_options(options, form)
        _save_options_to_supervisor(options)
        save_json(OPTIONS_PATH, options)
        log("Ayarlar Home Assistant config'e kaydedildi; Hermes yeniden başlatılacak.")
        _restart_addon_later()
        return True, "Ayarlar Home Assistant config'e kaydedildi. Hermes yeniden başlatılıyor; 10-20 saniye sonra sayfayı yenileyebilirsin."
    except Exception as exc:  # noqa: BLE001
        return False, f"Ayarlar kaydedilemedi: {exc}"
