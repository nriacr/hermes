import json
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
from html import escape

from .config_loader import DEFAULT_TELEGRAM_CHANNELS
from .constants import OPTIONS_PATH, SITE_HM, SITE_ZARA, STATE_PATH, SUMMARY_PATH
from .logging_utils import log
from .storage import load_json, save_json
from .utils import detect_site_from_url, parse_bool, site_label, watch_name_required_for_url

ADDON_SLUG = "hermes"
SUPERVISOR_BASE_URL = "http://supervisor"
WATCH_URL_FIELDS = ("url_1", "url_2", "url_3", "url_4", "url_5")

SETTINGS_CSS = """
:root { color-scheme: dark; --bg:#0f1222; --panel:#171a30; --card:#1e2139; --line:#313658; --text:#e8eaf8; --muted:#a6abd1; --accent:#c7a6ff; --accent2:#8ed6d2; --ok:#7fdcb8; --bad:#ff9cb5; }
* { box-sizing:border-box; } body { margin:0; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; background:radial-gradient(circle at top left,#1f2240,var(--bg) 56%); color:var(--text); font-size:14px; }
main { max-width:980px; margin:0 auto; padding:28px 18px 44px; } .hero { border:1px solid var(--line); border-radius:22px; padding:22px; background:var(--panel); box-shadow:0 18px 42px rgba(0,0,0,.35); }
h1 { margin:0 0 8px; font-size:34px; letter-spacing:-.04em; } h2 { margin:24px 0 10px; font-size:18px; } p { margin:0; color:var(--muted); line-height:1.5; font-size:13px; }
.actions { display:flex; flex-wrap:wrap; gap:10px; margin:18px 0; } .button { display:inline-flex; align-items:center; justify-content:center; min-height:40px; padding:0 14px; border-radius:13px; border:1px solid transparent; text-decoration:none; font-weight:800; font-size:13px; cursor:pointer; }
.button.primary { color:#14172a; background:linear-gradient(135deg,var(--accent),var(--accent2)); } .button.secondary { color:var(--text); background:#2a2f4d; border-color:var(--line); } .button.danger { color:#fff5f7; background:#b9364d; border-color:#ed7288; } .button.danger:hover { background:#cf465f; }
.notice { margin:14px 0; padding:11px 13px; border-radius:12px; font-weight:700; font-size:13px; } .notice-ok { color:#c6f7e6; background:rgba(127,220,184,.14); border:1px solid rgba(127,220,184,.38); } .notice-fail { color:#ffd8e3; background:rgba(255,156,181,.14); border:1px solid rgba(255,156,181,.38); }
.settings-section { margin-top:18px; border:1px solid var(--line); border-radius:18px; padding:16px; background:var(--card); } details { border:1px solid var(--line); border-radius:14px; background:#181c32; margin:9px 0; overflow:hidden; } summary { cursor:pointer; padding:13px 14px; font-weight:900; color:#f0f2ff; list-style:none; } summary::-webkit-details-marker { display:none; } summary::before { content:'\u25b8'; display:inline-block; margin-right:8px; color:var(--accent2); } details[open] summary::before { transform:rotate(90deg); } .watch-search { display:grid; gap:6px; max-width:440px; margin:0 0 12px; color:var(--muted); font-size:12px; font-weight:750; } .watch-search input { width:100%; min-height:40px; border:1px solid var(--line); border-radius:11px; padding:10px 11px; background:#101428; color:var(--text); font:inherit; } .watch-group-filters { display:flex; flex-wrap:wrap; gap:8px; margin:0 0 12px; } .watch-group-filter { min-height:34px; border:1px solid var(--line); border-radius:999px; padding:0 12px; background:#2a2f4d; color:var(--text); font:700 12px inherit; cursor:pointer; } .watch-group-filter[aria-pressed='false'] { color:var(--muted); background:#15182d; opacity:.72; text-decoration:line-through; } .watch-group-filter:hover { border-color:var(--accent2); }
.saving-overlay { position:fixed; inset:0; z-index:20; display:grid; place-items:center; padding:20px; background:rgba(7,9,19,.78); backdrop-filter:blur(5px); } .saving-overlay[hidden] { display:none; } .saving-dialog { width:min(100%,430px); border:1px solid rgba(142,214,210,.45); border-radius:18px; padding:22px; background:#1e2139; box-shadow:0 22px 50px rgba(0,0,0,.5); } .saving-dialog h2 { margin:0 0 9px; font-size:20px; } .saving-dialog p { font-size:14px; } .saving-spinner { width:28px; height:28px; margin:0 0 14px; border:4px solid rgba(142,214,210,.22); border-top-color:var(--accent2); border-radius:50%; animation:hermes-spin .8s linear infinite; } @keyframes hermes-spin { to { transform:rotate(360deg); } }
.form-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:12px; padding:0 14px 14px; } label { display:grid; gap:6px; color:var(--muted); font-size:12px; font-weight:700; } input[type='text'], input[type='number'], input[type='url'], select, textarea { width:100%; min-height:40px; border-radius:11px; border:1px solid var(--line); background:#101428; color:var(--text); padding:10px 11px; font-size:13px; font-family:inherit; } textarea { resize:vertical; line-height:1.35; }
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


def _select(prefix, name, label, value, choices, placeholder="Seçilmedi"):
    selected_value = str(value or "").strip()
    options = [f"<option value=''>{escape(placeholder)}</option>"]
    for choice in choices:
        text = str(choice or "").strip()
        if not text:
            continue
        selected = " selected" if text == selected_value else ""
        options.append(f"<option value='{escape(text, quote=True)}'{selected}>{escape(text)}</option>")
    return (
        f"<label>{escape(label)}"
        f"<select name='{escape(prefix + name, quote=True)}'>{''.join(options)}</select>"
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


def _watch_group(item):
    if not isinstance(item, dict):
        return "Diğer"
    group = str(item.get("group") or "").strip()
    if group:
        return group
    if any(detect_site_from_url(url) in {SITE_ZARA, SITE_HM} for url in _watch_urls_for_form(item)):
        return "Moda"
    return "Diğer"


def _watch_urls_for_form(item):
    urls = []
    if isinstance(item, dict):
        for field_name in WATCH_URL_FIELDS:
            url = str(item.get(field_name) or "").strip()
            if url and url not in urls:
                urls.append(url)
    return urls[: len(WATCH_URL_FIELDS)]


def _watch_url_keys(url):
    raw_url = str(url or "").strip()
    if not raw_url:
        return []
    parsed = urllib.parse.urlparse(raw_url)
    canonical = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
    return [raw_url] if canonical == raw_url else [raw_url, canonical]


def _title_from_url(url):
    parsed = urllib.parse.urlparse(str(url or "").strip())
    slug = parsed.path.rstrip("/").rsplit("/", 1)[-1]
    slug = slug.split("-p", 1)[0].replace(".html", "").replace("-", " ").strip()
    if slug and not slug.startswith("productpage."):
        return " ".join(part.capitalize() for part in slug.split())
    try:
        return f"{site_label(detect_site_from_url(url))} ürünü"
    except Exception:  # noqa: BLE001
        pass
    host = parsed.netloc.removeprefix("www.").split(".", 1)[0]
    return f"{host.upper() or 'Ürün'} ürünü"


def _stored_watch_titles():
    """Map configured URLs to titles already learned during price checks."""
    titles = {}

    def remember(url, title):
        url = str(url or "").strip()
        title = str(title or "").strip()
        if not title:
            return
        for key in _watch_url_keys(url):
            titles.setdefault(key, title)

    summary = load_json(SUMMARY_PATH, {})
    if isinstance(summary, dict):
        for row_set in (summary.get("rows"), summary.get("stock_rows")):
            for row in _as_list(row_set):
                if isinstance(row, dict):
                    remember(row.get("product_url"), row.get("product_title"))

    state = load_json(STATE_PATH, {})
    if isinstance(state, dict):
        for entry in state.values():
            if isinstance(entry, dict):
                remember(entry.get("configured_url"), entry.get("title"))
    return titles


def _watch_display_name(item, index, known_titles):
    if isinstance(item, dict):
        name = str(item.get("name") or "").strip()
        if name:
            return name
        for url in _watch_urls_for_form(item):
            for key in _watch_url_keys(url):
                title = str(known_titles.get(key) or "").strip()
                if title:
                    return title
        urls = _watch_urls_for_form(item)
        if urls:
            return _title_from_url(urls[0])
    return f"Takip {index + 1}"


def _details(title, prefix, inner, open_when_empty=False):
    open_attr = " open" if open_when_empty else ""
    return f"<details{open_attr}><summary>{escape(title)}</summary><div class='form-grid'>{inner}</div></details>"


def _watch_form(item, index, is_new=False, groups=None, known_titles=None):
    prefix = f"watches_{index}_"
    group = _watch_group(item)
    display_name = _watch_display_name(item, index, known_titles or {})
    title = "Yeni takip ekle" if is_new else f"[{group}] {display_name}"
    group_choices = list(groups or [])
    if not is_new and group != "Diğer" and group not in group_choices:
        group_choices.append(group)
    urls = _watch_urls_for_form(item)
    max_items = item.get("max_items_to_scan", 24 if is_new else "")
    notify_once = True if is_new else item.get("notify_once_in_24H", True)
    active = True if is_new else item.get("active", True)
    selected_group = "" if is_new else str(item.get("group") or "").strip()
    if not selected_group and group == "Moda":
        selected_group = "Moda"
    inner = "".join(
        [
            _field(prefix, "name", "Ad (ürün linklerinde boş bırakılabilir)", item.get("name", "")),
            _select(prefix, "group", "Grup", selected_group, group_choices),
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
            (
                f"<button class='button danger' type='submit' name='delete_watch_index' value='{index}' "
                "data-delete-watch='true'>Sil</button>"
                if not is_new
                else ""
            ),
        ]
    )
    group_attribute = "" if is_new else f" data-watch-group='{escape(group, quote=True)}'"
    search_attribute = "" if is_new else f" data-watch-search='{escape(display_name, quote=True)}'"
    return (
        f"<details{group_attribute}{search_attribute}{' open' if is_new else ''}>"
        f"<summary>{escape(title)}</summary><div class='form-grid'>{inner}</div></details>"
    )


def _section(title, items, renderer, section_name, include_new=False):
    safe_items = _as_list(items)
    rows = [renderer(item if isinstance(item, dict) else {}, index) for index, item in enumerate(safe_items)]
    if include_new:
        rows.append(renderer({}, len(safe_items), is_new=True))
    count = len(safe_items) + (1 if include_new else 0)
    return (
        f"<section class='settings-section'><h2>{escape(title)}</h2>"
        f"<input type='hidden' name='{escape(section_name)}_count' value='{count}'>"
        f"{''.join(rows)}</section>"
    )


def _watch_section(items, configured_groups, known_titles=None):
    safe_items = _as_list(items)
    groups = []
    for group in configured_groups or []:
        value = str(group or "").strip()
        if value and value.casefold() not in {existing.casefold() for existing in groups}:
            groups.append(value)
    for item in safe_items:
        group = _watch_group(item)
        if group.casefold() not in {value.casefold() for value in groups}:
            groups.append(group)
    filters = "".join(
        f"<button class='watch-group-filter' type='button' data-watch-group-filter='{escape(group, quote=True)}' aria-pressed='true'>{escape(group)}</button>"
        for group in groups
    )
    filters_html = (
        "<div class='watch-group-filters' aria-label='Takip edilen grup filtreleri'>"
        f"{filters}</div>"
        if filters
        else ""
    )
    search_html = (
        "<label class='watch-search'>Takip edilenlerde ara"
        "<input id='watch-search' type='search' placeholder='Ürün adında ara' autocomplete='off'>"
        "</label>"
    )
    renderer = lambda item, index, is_new=False: _watch_form(
        item,
        index,
        is_new,
        groups=groups,
        known_titles=known_titles,
    )
    return (
        search_html
        + filters_html
        + _section("Takip edilenler", safe_items, renderer, "watches")
        + "<p class='footer-note'>Grup seçeneklerini Home Assistant Configuration ekranındaki <strong>gruplar</strong> listesinde tanımlayabilirsin. Buradan seçilen grup yalnızca düzenleme ve filtreleme içindir; takip kurallarını değiştirmez.</p>"
    )


def _new_watch_section(groups, known_titles=None):
    return (
        "<section class='settings-section'><h2>Yeni takip ekle</h2>"
        "<input type='hidden' name='watches_count' value='1'>"
        f"{_watch_form({}, 0, is_new=True, groups=groups, known_titles=known_titles)}"
        "</section>"
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


def _watch_form_context(index, name, urls):
    identity = name or (urls[0] if urls else "yeni kayıt")
    if len(identity) > 96:
        identity = f"{identity[:93]}..."
    return f"Takip {index + 1} ({identity})"


def _build_watches(form):
    watches = []
    count = int(_first(form, "watches_count", "0") or 0)
    for index in range(count):
        prefix = f"watches_{index}_"
        if _bool_from_form(form, prefix + "delete"):
            continue
        name = _first(form, prefix + "name")
        group = _first(form, prefix + "group")
        target = _first(form, prefix + "target_price")
        size = _first(form, prefix + "size")
        max_items = _first(form, prefix + "max_items_to_scan")
        interval = _first(form, prefix + "check_interval_minutes")
        urls = []
        for field_name in WATCH_URL_FIELDS:
            url = _first(form, prefix + field_name)
            if url and url not in urls:
                urls.append(url)
        if not group and any(detect_site_from_url(url) in {SITE_ZARA, SITE_HM} for url in urls):
            group = "Moda"
        # The always-visible new-watch form sends default values such as max_items=24.
        # A row without an actual product detail must never block saving existing watches.
        if not any([name, target, size, *urls]):
            continue
        context = _watch_form_context(index, name, urls)
        if not target or not urls:
            missing = []
            if not target:
                missing.append("hedef fiyat")
            if not urls:
                missing.append("en az bir link")
            raise ValueError(f"{context}: {', '.join(missing)} alanı zorunlu.")
        if not name and any(watch_name_required_for_url(url) for url in urls):
            raise ValueError(
                f"{context}: bu bağlantı bir arama sayfası. Arama sonuçlarını doğru filtrelemek için "
                "Ad alanı zorunlu; örneğin ürün modelini yazmalısın."
            )
        item = {
            "name": name,
            "group": group,
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


def _apply_settings_operation(existing_options, form):
    source_options = existing_options if isinstance(existing_options, dict) else {}
    existing_watches = [
        dict(item) for item in _as_list(source_options.get("takip_edilenler")) if isinstance(item, dict)
    ]
    options = _clean_editable_options(source_options)
    operation = _first(form, "operation", "update_existing")
    delete_index = _first(form, "delete_watch_index")

    if delete_index != "":
        try:
            index = int(delete_index)
        except ValueError as exc:
            raise ValueError("Silinecek takip kaydı geçersiz.") from exc
        if index < 0 or index >= len(existing_watches):
            raise ValueError("Silinecek takip kaydı bulunamadı.")
        removed = existing_watches.pop(index)
        removed_name = str(removed.get("name") or "").strip() or f"Takip {index + 1}"
        options["takip_edilenler"] = existing_watches
        return options, f"{removed_name} takip kaydı silindi."

    if operation == "update_existing":
        options["takip_edilenler"] = _build_watches(form)
        _update_telegram_options(options, form)
        return options, f"{len(options['takip_edilenler'])} mevcut takip kaydı güncellendi."

    if operation == "add_watch":
        new_watches = _build_watches(form)
        if not new_watches:
            raise ValueError("Yeni takip eklemek için hedef fiyat ve en az bir link alanı zorunlu.")
        if len(new_watches) != 1:
            raise ValueError("Yeni takip ekleme formunda yalnızca bir kayıt bulunmalı.")
        options["takip_edilenler"] = existing_watches + new_watches
        return options, "Yeni takip kaydı eklendi."

    raise ValueError("Bilinmeyen ayar kaydetme işlemi.")


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
        "gruplar",
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
    groups = _list_from_form(
        {"groups": ["\n".join(str(group) for group in _as_list(options.get("gruplar")))]},
        "groups",
    )
    known_titles = _stored_watch_titles()
    params = urllib.parse.parse_qs(urllib.parse.urlparse(path).query)
    status = params.get("saved", [""])[0]
    message = params.get("msg", [""])[0]
    notice = ""
    if status in {"ok", "fail"}:
        css = "notice-ok" if status == "ok" else "notice-fail"
        notice = f"<p class='notice {css}'>{escape(message)}</p>"
    filter_script = """
<script>
  const storageKey = 'hermes-hidden-watch-groups';
  const hiddenGroups = new Set(JSON.parse(localStorage.getItem(storageKey) || '[]'));
  const normalize = (value) => value.toLocaleLowerCase('tr-TR');
  const watchSearch = document.getElementById('watch-search');
  const refreshWatchGroups = () => {
    const searchText = normalize(watchSearch ? watchSearch.value.trim() : '');
    document.querySelectorAll('[data-watch-group]').forEach((item) => {
      const groupHidden = hiddenGroups.has(normalize(item.dataset.watchGroup || 'Diğer'));
      const productName = normalize(item.dataset.watchSearch || '');
      const searchHidden = Boolean(searchText) && !productName.includes(searchText);
      item.hidden = groupHidden || searchHidden;
    });
    document.querySelectorAll('[data-watch-group-filter]').forEach((button) => {
      const hidden = hiddenGroups.has(normalize(button.dataset.watchGroupFilter || 'Diğer'));
      button.setAttribute('aria-pressed', String(!hidden));
      button.title = hidden ? 'Grubu göster' : 'Grubu gizle';
    });
  };
  document.querySelectorAll('[data-watch-group-filter]').forEach((button) => {
    button.addEventListener('click', () => {
      const group = normalize(button.dataset.watchGroupFilter || 'Diğer');
      if (hiddenGroups.has(group)) hiddenGroups.delete(group); else hiddenGroups.add(group);
      localStorage.setItem(storageKey, JSON.stringify([...hiddenGroups]));
      refreshWatchGroups();
    });
  });
  if (watchSearch) watchSearch.addEventListener('input', refreshWatchGroups);
  refreshWatchGroups();

  const savingOverlay = document.getElementById('saving-overlay');
  const savingTitle = document.getElementById('saving-title');
  const savingMessage = document.getElementById('saving-message');
  document.querySelectorAll('form[data-settings-save]').forEach((form) => {
    form.addEventListener('submit', (event) => {
      const button = event.submitter;
      const isDelete = button && button.dataset.deleteWatch === 'true';
      if (button) {
        button.disabled = true;
        button.textContent = isDelete ? 'Siliniyor...' : 'Kaydediliyor...';
      }
      savingTitle.textContent = isDelete ? 'Takip siliniyor' : 'Ayarlar kaydediliyor';
      savingMessage.textContent = isDelete
        ? 'Takip kaydı kaldırılıyor. Hermes yeniden başlatılacak; hazır olduğunda ayarlara otomatik dönülecek.'
        : 'Hermes değişiklikleri Home Assistant'a yazıyor. Ardından kısa bir yeniden başlatma yapılacak; hazır olduğunda ayarlara otomatik dönülecek.';
      savingOverlay.hidden = false;
    });
  });
</script>"""
    html = f"""<!doctype html>
<html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Hermes Ayarlar</title><style>{SETTINGS_CSS}</style></head>
<body><main><div class="hero"><h1>Hermes Ayarlar</h1><p>Mevcut takipleri güncelleme ve yeni takip ekleme işlemleri ayrı çalışır. Listelerde yalnızca adlar görünür; satıra tıklayınca ayrıntılar açılır.</p><div class="actions"><a class="button secondary" href="./">Ana ekran</a></div>{notice}<form method="post" action="./settings/save" data-settings-save>
{_new_watch_section(groups, known_titles)}
<input type="hidden" name="operation" value="add_watch">
<div class="actions"><button class="button primary" type="submit">Yeni Takibi Ekle</button></div>
</form><form method="post" action="./settings/save" data-settings-save>
{_watch_section(options.get("takip_edilenler"), groups, known_titles)}
{_telegram_section(options)}
<input type="hidden" name="operation" value="update_existing">
<div class="actions"><button class="button primary" type="submit">Güncellemeleri Kaydet</button><a class="button secondary" href="./">Vazgeç</a></div>
</form><p class="footer-note">Kayıt sonrası Hermes yeniden başlatılır. Sayfa kısa süre yanıt vermeyebilir; 10-20 saniye sonra otomatik olarak hazır olur.</p>
</div></main><div id="saving-overlay" class="saving-overlay" hidden><div class="saving-dialog"><div class="saving-spinner"></div><h2 id="saving-title">Ayarlar kaydediliyor</h2><p id="saving-message">Hermes değişiklikleri Home Assistant'a yazıyor. Ardından kısa bir yeniden başlatma yapılacak; hazır olduğunda ayarlara otomatik dönülecek.</p></div></div>{filter_script}</body></html>"""
    return html.encode("utf-8")


def handle_settings_save(body):
    try:
        form = urllib.parse.parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True)
        options = load_json(OPTIONS_PATH, {})
        if not isinstance(options, dict):
            options = {}
        options, change_message = _apply_settings_operation(options, form)
        _save_options_to_supervisor(options)
        save_json(OPTIONS_PATH, options)
        log("Ayarlar Home Assistant config'e kaydedildi; Hermes yeniden başlatılacak.")
        _restart_addon_later()
        return True, f"{change_message} Hermes yeniden başlatılıyor; 10-20 saniye sonra sayfayı yenileyebilirsin."
    except Exception as exc:  # noqa: BLE001
        return False, f"Ayarlar kaydedilemedi: {exc}"
