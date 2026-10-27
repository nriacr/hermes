"""Settings page: tracking cards, Telegram and timing options, saved once with one restart."""

import re
import sqlite3
import uuid
import urllib.parse
from html import escape
from typing import Any, Dict, List, Optional

import requests

from .. import traffic
from ..config import (
    DEFAULT_TELEGRAM_CHANNELS, WATCH_URL_FIELDS, format_variant_filter, options_with_defaults, parse_variant_filter, read_options, watch_urls,
)
from ..constants import (
    APP_VERSION, DEFAULT_PRIORITY, DEFAULT_REQUEST_TIMEOUT_SECONDS, PRIORITIES, PRIORITY_DESCRIPTIONS, PRIORITY_LABELS, STATE_PATH, NOTIFY_WINDOWS, normalize_notify_window, SUMMARY_PATH, SITE_LABELS, normalize_priority,
)
from ..errors import HermesError
from ..logging_utils import log
from ..providers.base import ReadContext
from ..providers.registry import PROVIDER_TYPES
from ..storage import load_json
from ..supervisor import OPTIONS_LOCK, save_options_and_restart, schedule_restart
from ..utils import detect_site_from_url, format_tl, parse_bool, parse_decimal, site_label, utc_now, watch_name_required_for_url
from .dashboard import priority_dot
from .pages import link, render_notice, render_page, render_tool_actions, confirm_script_tag

OTHER_SITE = "Diğer"
# Card options each site shows besides the common ones.
SITE_OPTIONS = {provider.site: provider.card_options for provider in PROVIDER_TYPES}
MAX_WATCH_CARDS = 500
STALE_PAGE_MESSAGE = "Ayar sayfası eskimiş: takip listesi bu sayfa açıldıktan sonra değişti. Sayfayı yenile ve değişikliği tekrar yap."


def _as_list(value) -> list:
    return value if isinstance(value, list) else []


def _first(form: Dict[str, List[str]], key: str, default: str = "") -> str:
    values = form.get(key)
    return str(values[0]).strip() if values else default


# -- form controls --------------------------------------------------------------------


def _field(prefix, name, label, value="", field_type="text", required=False) -> str:
    return (f"<label>{escape(label)}<input type='{field_type}' name='{escape(prefix + name, quote=True)}' "
            f"value='{escape(str(value or ''), quote=True)}'{' required' if required else ''}></label>")


def _select(prefix, name, label, value, choices, placeholder="Seçilmedi") -> str:
    selected_value = str(value or "").strip()
    options = [f"<option value=''>{escape(placeholder)}</option>"]
    for choice in choices:
        text = str(choice or "").strip()
        if text:
            options.append(f"<option value='{escape(text, quote=True)}'{' selected' if text == selected_value else ''}>{escape(text)}</option>")
    return f"<label>{escape(label)}<select name='{escape(prefix + name, quote=True)}'>{''.join(options)}</select></label>"


def _textarea(prefix, name, label, values=None, rows=5) -> str:
    value = "\n".join(str(item) for item in values) if isinstance(values, list) else str(values or "")
    return f"<label>{escape(label)}<textarea name='{escape(prefix + name, quote=True)}' rows='{int(rows)}'>{escape(value)}</textarea></label>"


def _checkbox(prefix, name, label, checked=True, danger=False) -> str:
    return (f"<label class='checkbox-row{' danger' if danger else ''}'><input type='checkbox' name='{escape(prefix + name, quote=True)}' "
            f"value='1'{' checked' if parse_bool(checked, default=True) else ''}>{escape(label)}</label>")


def _price_input_value(value) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return format_tl(parse_decimal(text))
    except Exception:  # noqa: BLE001
        return text


# -- card titles ----------------------------------------------------------------------


def _url_keys(url) -> List[str]:
    raw_url = str(url or "").strip()
    if not raw_url:
        return []
    parsed = urllib.parse.urlparse(raw_url)
    canonical = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
    return [raw_url] if canonical == raw_url else [raw_url, canonical]


def _title_from_url(url) -> str:
    parsed = urllib.parse.urlparse(str(url or "").strip())
    slug = parsed.path.rstrip("/").rsplit("/", 1)[-1]
    slug = slug.split("-p", 1)[0].replace(".html", "").replace("-", " ").strip()
    if slug and not slug.startswith("productpage."):
        return " ".join(part.capitalize() for part in slug.split())
    try:
        return f"{site_label(detect_site_from_url(url))} ürünü"
    except Exception:  # noqa: BLE001
        host = parsed.netloc.removeprefix("www.").split(".", 1)[0]
        return f"{host.upper() or 'Ürün'} ürünü"


def stored_watch_titles() -> Dict[str, str]:
    """Titles already learned by price reads, for cards without a name."""
    titles: Dict[str, str] = {}

    def remember(url, title):
        title = str(title or "").strip()
        if title:
            for key in _url_keys(url):
                titles.setdefault(key, title)

    try:
        summary = load_json(SUMMARY_PATH, {})
        state = load_json(STATE_PATH, {})
    except (RuntimeError, sqlite3.Error):
        return titles
    for product_url, title in _learned_titles(summary, state):
        remember(product_url, title)
    return titles


def _learned_titles(summary, state):
    if isinstance(summary, dict):
        for row_set in (summary.get("rows"), summary.get("stock_rows")):
            for row in _as_list(row_set):
                if isinstance(row, dict):
                    yield row.get("product_url"), row.get("product_title")
    if isinstance(state, dict):
        for entry in state.values():
            if isinstance(entry, dict):
                yield entry.get("configured_url"), entry.get("title")


def watch_display_name(item, index: int, known_titles: Dict[str, str]) -> str:
    if isinstance(item, dict):
        name = str(item.get("name") or "").strip()
        if name:
            return name
        urls = watch_urls(item)
        for url in urls:
            for key in _url_keys(url):
                if str(known_titles.get(key) or "").strip():
                    return str(known_titles[key]).strip()
        if urls:
            return _title_from_url(urls[0])
    return f"Takip {index + 1}"


# -- rendering ------------------------------------------------------------------------


def _card_priority(item: Dict[str, Any], is_new: bool) -> str:
    """New cards start at one minute; stored choices use the shared migration."""
    return DEFAULT_PRIORITY if is_new else normalize_priority(item.get("priority"))


def _watch_site(item) -> str:
    for url in watch_urls(item):
        try:
            return detect_site_from_url(url)
        except HermesError:
            continue
    return ""


OPTION_LABELS = {
    "include_variations": "Varyasyonları ekle",
    "official_seller_only": "Yalnızca Amazon satıcısı",
    "auto_add_to_cart": "Otomatik sepete ekle",
}


def _site_options(prefix, item, site) -> str:
    """The site's own fields of a saved card."""
    parts = []
    for option in SITE_OPTIONS.get(site, ()):
        if option == "size":
            parts.append(_field(prefix, "size", "Beden", item.get("size", "")))
        elif option == "variant_filter":
            parts.append(_field(prefix, "variant_filter", "Varyant seçimi", item.get("variant_filter", "")))
        else:
            parts.append(_checkbox(prefix, option, OPTION_LABELS[option], item.get(option, False)))
    return "".join(parts)


def _common_fields(prefix, item, is_new) -> str:
    exclude_terms = item.get("exclude_terms", "")
    if isinstance(exclude_terms, list):
        exclude_terms = ", ".join(str(term).strip() for term in exclude_terms if str(term).strip())
    priority = _card_priority(item, is_new)
    priority_field = (
        f"<label class='watch-priority'>Öncelik<select name='{escape(prefix + 'priority', quote=True)}' data-watch-priority>"
        + "".join(f"<option value='{value}' title='{escape(PRIORITY_DESCRIPTIONS[value], quote=True)}'"
                  f"{' selected' if priority == value else ''}>{escape(PRIORITY_LABELS[value])}</option>" for value in PRIORITIES)
        + "</select></label>"
    )
    window_field = (
        f"<label class='watch-window'>Bildirim zamanı<select name='{escape(prefix + 'notify_window', quote=True)}'>"
        + "".join(f"<option value='{value}'{' selected' if normalize_notify_window(item.get('notify_window')) == value else ''}>"
                  f"{escape(label)}</option>" for value, label in NOTIFY_WINDOWS.items())
        + "</select></label>"
    )
    return (
        "<div class='watch-fields'>"
        f"{_field(prefix, 'name', 'Ad', item.get('name', ''))}"
        f"{_field(prefix, 'target_price', 'Hedef fiyat maks', _price_input_value(item.get('target_price', '')))}"
        f"{_field(prefix, 'minimum_price', 'Hedef fiyat min', _price_input_value(item.get('minimum_price', '')))}"
        f"{priority_field}{window_field}{_field(prefix, 'exclude_terms', 'Hariç tut', exclude_terms)}</div>"
        "<div class='watch-flags'>"
        + _checkbox(prefix, "active", "Aktif", True if is_new else item.get("active", True))
        + _checkbox(prefix, "notify_once_in_24H", "24 saat sustur", True if is_new else item.get("notify_once_in_24H", True))
        + _checkbox(prefix, "notify_90_day_low", "90 gün en düşük", False if is_new else item.get("notify_90_day_low", False))
        + "</div>"
    )


def _hidden_identity(prefix, item) -> str:
    return (f"<input type='hidden' name='{escape(prefix + 'id', quote=True)}' value='{escape(str(item.get('id') or ''), quote=True)}'>"
            f"<input type='hidden' name='{escape(prefix + 'delete', quote=True)}' value='0' data-delete-flag>")


def watch_form(item: Dict[str, Any], index: int, known_titles=None) -> str:
    """A saved card: its links, the common fields and only its site's own fields."""
    prefix = f"watches_{index}_"
    display_name = watch_display_name(item, index, known_titles or {})
    urls = watch_urls(item) or [""]
    links = "".join(_field(prefix, field_name, "Link" if number == 1 else f"Link {number}", url, "url")
                    for number, (field_name, url) in enumerate(zip(WATCH_URL_FIELDS, urls), start=1))
    site_options = _site_options(prefix, item, _watch_site(item))
    inner = (
        _hidden_identity(prefix, item)
        + f"<div class='watch-links'>{links}</div>"
        + _common_fields(prefix, item, False)
        + (f"<div class='watch-site-options'>{site_options}</div>" if site_options else "")
        + "<div class='watch-actions'><button class='button danger' type='button' data-delete-watch>Sil</button></div>"
    )
    return (f"<details data-watch-card data-watch-search='{escape(display_name, quote=True)}'><summary>"
            "<input type='checkbox' class='bulk-pick' data-bulk-pick aria-label='Toplu işlem için seç'>"
            f"{priority_dot(_card_priority(item, False))}{escape(display_name)}</summary><div class='form-grid'>{inner}</div></details>")


def new_watch_form(index: int, check_path: str) -> str:
    """Link first; the site's fields open after a successful check."""
    prefix = f"watches_{index}_"
    size_options = "<option value=''>Hepsi</option>"
    site_options = (
        f"<label data-site-option='size' hidden>Beden<select name='{escape(prefix + 'size', quote=True)}' data-size-select>{size_options}</select></label>"
        + "".join(f"<span data-site-option='{option}' hidden>{_checkbox(prefix, option, OPTION_LABELS[option], False)}</span>"
                  for option in ("include_variations", "official_seller_only", "auto_add_to_cart"))
        + f"<div data-site-option='variant_filter' data-variant-dims hidden></div>"
          f"<input type='hidden' name='{escape(prefix + 'variant_filter', quote=True)}' data-variant-filter>"
    )
    return (
        f"<div class='new-watch' data-new-watch data-check-path='{escape(check_path, quote=True)}'>"
        + _hidden_identity(prefix, {})
        + "<div class='link-row'>"
          f"<input type='url' name='{escape(prefix + 'url_1', quote=True)}' aria-label='Link' autocomplete='off' data-check-url>"
          "<button class='button primary' type='button' data-check-link>Kontrol et</button></div>"
        "<p class='check-error' data-check-error hidden></p>"
        "<div class='new-watch-body' data-new-body hidden>"
        "<div class='new-watch-product'><h3 data-product-title></h3><p class='product-sub' data-product-sub></p>"
        "<div class='variant-table' data-variant-table></div>"
        f"<div class='watch-site-options'>{site_options}</div></div>"
        f"<div class='new-watch-fields'>{_common_fields(prefix, {}, True)}</div></div></div>"
    )


def sites_in_order(watches) -> List[tuple]:
    """(site label, [(index, card)]) in the supported-site order; unknown links last."""
    grouped: Dict[str, List[tuple]] = {}
    for index, item in enumerate(watches):
        item = item if isinstance(item, dict) else {}
        site = _watch_site(item)
        grouped.setdefault(SITE_LABELS.get(site, OTHER_SITE), []).append((index, item))
    order = list(SITE_LABELS.values()) + [OTHER_SITE]
    return [(label, grouped[label]) for label in order if label in grouped]


def _list_from_text(raw_value: str) -> List[str]:
    values, seen = [], set()
    for line in str(raw_value or "").replace(",", "\n").splitlines():
        value = line.strip()
        if value and value.casefold() not in seen:
            seen.add(value.casefold())
            values.append(value)
    return values


def _telegram_section(options: Dict[str, Any]) -> str:
    channels = options.get("channels") if isinstance(options.get("channels"), list) else DEFAULT_TELEGRAM_CHANNELS
    inner = "".join([
        _checkbox("", "telegram_enabled", "Telegram takip aktif", options.get("telegram_enabled", False)),
        _checkbox("", "telegram_saved_messages_enabled", "Kayıtlı Mesajlar'dan hızlı takip ekleme aktif",
                  options.get("telegram_saved_messages_enabled", True)),
        _field("", "api_id", "Telegram API ID", options.get("api_id", "")),
        _field("", "api_hash", "Telegram API Hash", options.get("api_hash", "")),
        _field("", "phone_number", "Telefon numarası", options.get("phone_number", "")),
        _field("", "verification_code", "Telegram doğrulama kodu", options.get("verification_code", "")),
        _field("", "session_name", "Session adı", options.get("session_name", "telegram_keyword_alert")),
        _textarea("", "channels", "Kanallar (her satıra bir kanal)", channels, rows=7),
        _textarea("", "keywords", "Keyword'ler (her satıra bir keyword)", _as_list(options.get("keywords")), rows=5),
        _textarea("", "exclude_keywords", "Hariç tutulacak keyword'ler", _as_list(options.get("exclude_keywords")), rows=4),
    ])
    return ("<section class='settings-section'><h2>Telegram takip</h2>"
            f"<details><summary>Telegram ayarları</summary><div class='form-grid'>{inner}</div></details></section>")


# Supervisor options shown at the bottom of Ayarlar: (key, label, lowest, highest).
TIMING_FIELDS = (
    ("interval_seconds", "Çevrim aralığı", 1, 86400),
    ("request_delay_min_seconds", "Bekleme süresi min", 0, 120),
    ("request_delay_max_seconds", "Bekleme süresi maks", 0, 120),
)


def _timing_section(options: Dict[str, Any]) -> str:
    fields = "".join(
        f"<label>{escape(label)}<input type='number' inputmode='numeric' name='{key}' min='{lowest}' max='{highest}' step='1' "
        f"value='{escape(str(options.get(key, '')), quote=True)}' required></label>"
        for key, label, lowest, highest in TIMING_FIELDS
    )
    return f"<section class='settings-section timing-settings'><h2>Zamanlama</h2><div class='form-grid'>{fields}</div></section>"


def _daily_summary_section(options: Dict[str, Any]) -> str:
    box = _checkbox("", "daily_summary_enabled", "Günlük özet bildirimi (her sabah 09:00)", options.get("daily_summary_enabled", True))
    return ("<section class='settings-section'><h2>Günlük özet</h2>"
            f"{box}</section>")


def _update_timing_options(options: Dict[str, Any], form) -> None:
    """Same limits as the add-on schema; a page without these fields keeps the saved values."""
    values = {}
    for key, label, lowest, highest in TIMING_FIELDS:
        raw = _first(form, key)
        if not raw:
            continue
        try:
            value = int(raw)
        except ValueError as exc:
            raise ValueError(f"{label} tam sayı olmalı.") from exc
        if not lowest <= value <= highest:
            raise ValueError(f"{label} {lowest} ile {highest} saniye arasında olmalı.")
        values[key] = value
    low = values.get("request_delay_min_seconds", options.get("request_delay_min_seconds"))
    high = values.get("request_delay_max_seconds", options.get("request_delay_max_seconds"))
    if low is not None and high is not None and int(low) > int(high):
        raise ValueError("Bekleme süresi min, bekleme süresi maks değerinden büyük olamaz.")
    options.update(values)


HTTP_WARNING = "HTTP (şifresiz bağlantı)"


def supported_sites_text() -> str:
    return ", ".join(SITE_LABELS.values())


def bulk_toolbar() -> str:
    """Selection controls that only fill the existing card fields; saving stays the single apply step."""
    priorities = "".join(f"<option value='{value}'>{escape(label)}</option>" for value, label in PRIORITY_LABELS.items())
    return (
        "<section class='settings-section bulk-tools' data-bulk-tools><h2>Toplu işlem</h2>"
        "<div class='bulk-row'><button class='button secondary' type='button' data-bulk-all>Görünenleri seç</button>"
        "<button class='button secondary' type='button' data-bulk-none>Seçimi kaldır</button>"
        "<span class='bulk-count' data-bulk-count>0 seçili</span></div>"
        "<div class='bulk-row'><button class='button secondary' type='button' data-bulk-active='1'>Aktif yap</button>"
        "<button class='button secondary' type='button' data-bulk-active='0'>Pasif yap</button>"
        f"<select data-bulk-priority aria-label='Öncelik'>{priorities}</select>"
        "<button class='button secondary' type='button' data-bulk-set-priority>Önceliği uygula</button></div>"
        "</section>"
    )


def _site_groups(watches, known_titles) -> str:
    return "".join(
        f"<details class='site-group' data-site-group><summary>{escape(label)}<span class='site-count'>{len(cards)}</span></summary>"
        f"<div class='site-cards'>{''.join(watch_form(item, index, known_titles) for index, item in cards)}</div></details>"
        for label, cards in sites_in_order(watches)
    )


def render_settings_page(base: str, params: Dict[str, List[str]], *, access_bar: str = "", open_http: bool = False) -> bytes:
    options = read_options()
    known_titles = stored_watch_titles()
    watches = _as_list(options.get("takip_edilenler"))
    new_index = len(watches)
    notice = render_notice(params.get("saved", [""])[0], params.get("msg", [""])[0])
    http_notice = render_notice("warn", HTTP_WARNING) if open_http else ""
    body = (
        f"<h2 class='page-heading'>Ayarlar</h2>{access_bar}{http_notice}{notice}"
        f"<form id='settings-form' method='post' action='{escape(link(base, 'settings/save'), quote=True)}' data-settings-save>"
        "<input type='hidden' name='operation' value='update_existing'>"
        f"<input type='hidden' name='watches_count' id='watches-count' value='{new_index + 1}'>"
        "<section class='settings-section'><h2>Yeni takip</h2>"
        f"{new_watch_form(new_index, link(base, 'settings/check'))}</section>"
        "<section class='settings-section'><h2>Takip edilenler</h2>"
        "<input id='watch-search' type='search' placeholder='Ara' aria-label='Takip edilenlerde ara' autocomplete='off'>"
        f"{_site_groups(watches, known_titles)}</section>"
        + bulk_toolbar()
        + _telegram_section(options)
        + _timing_section(options)
        + _daily_summary_section(options)
        + "<div class='apply-bar'><p>Uygulayınca Hermes yeniden başlar.</p><button class='button primary' type='submit'>Değişiklikleri uygula</button></div></form>"
        + render_tool_actions(base)
    )
    overlay = ("<div id='saving-overlay' class='saving-overlay' hidden><div class='saving-dialog'><div class='saving-spinner'></div>"
               "<h2 id='saving-title'>Ayarlar kaydediliyor</h2><p id='saving-message'>Kaydediliyor, Hermes yeniden başlıyor…</p></div></div>")
    script = f"<script src='{escape(link(base, 'settings.js'), quote=True)}?v={escape(APP_VERSION)}' defer></script>"
    return render_page(base, "settings", "Hermes Ayarlar", body, body_class="public ov settings-page", after_main=overlay, scripts=confirm_script_tag(base) + script)


def render_restart_page(base: str, params: Dict[str, List[str]]) -> bytes:
    message = params.get("msg", ["Ayarlar kaydedildi. Hermes yeniden başlatılıyor."])[0]
    # After a save or restart the in-memory PIN session is gone, so returning to the settings page would only show
    # the lock screen. The home page is open to everyone; "return_to_main=0" still selects the settings page.
    return_to_main = params.get("return_to_main", ["1"])[0] != "0"
    destination = link(base, "") if return_to_main else link(base, "settings")
    body = (
        "<h2 class='page-heading'>Hermes yeniden başlatılıyor</h2>"
        f"<p class='notice notice-ok'>{escape(message)}</p>"
        "<p class='footer-note' id='restart-status'>Hazırlanıyor... Birkaç saniye içinde bağlantı kontrolü başlayacak.</p>"
        f"<div class='actions'><a class='button secondary' href='{escape(destination, quote=True)}'>"
        f"{'Ana ekrana dön' if return_to_main else 'Ayarlar ekranına dön'}</a></div>"
    )
    script = (f"<script id='hermes-restart-script' src='{escape(link(base, 'restart.js'), quote=True)}?v={escape(APP_VERSION)}' defer "
              f"data-settings-path='{escape(link(base, 'settings'), quote=True)}' data-return-path='{escape(destination, quote=True)}' "
              f"data-health-path='{escape(link(base, 'health'), quote=True)}'></script>")
    return render_page(base, "settings", "Hermes yeniden başlatılıyor", body, body_class="public ov", scripts=script)


# -- saving ---------------------------------------------------------------------------


def _price_from_form(value) -> int:
    try:
        return int(parse_decimal(value))
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Hedef fiyat geçersiz: {value!r}") from exc


def _card_context(index: int, name: str, urls: List[str]) -> str:
    identity = name or (urls[0] if urls else "yeni kayıt")
    return f"Takip {index + 1} ({identity[:93] + '...' if len(identity) > 96 else identity})"


def _require_valid_card(context: str, name: str, target: str, urls: List[str]) -> None:
    for url in urls:
        try:
            detect_site_from_url(url)
        except HermesError as exc:
            raise ValueError(f"{context}: {exc}. Desteklenen siteler: {supported_sites_text()}.") from exc
    missing = [label for label, value in (("hedef fiyat", target), ("en az bir link", urls)) if not value]
    if missing:
        raise ValueError(f"{context}: {', '.join(missing)} alanı zorunlu.")
    if not name and any(watch_name_required_for_url(url) for url in urls):
        raise ValueError(f"{context}: bu bağlantı bir arama sayfası. Arama sonuçlarını doğru filtrelemek için "
                         "Ad alanı zorunlu; örneğin ürün modelini yazmalısın.")


def build_watch(form: Dict[str, List[str]], index: int) -> Optional[Dict[str, Any]]:
    prefix = f"watches_{index}_"
    name = _first(form, prefix + "name")
    target = _first(form, prefix + "target_price")
    minimum_price = _first(form, prefix + "minimum_price")
    size = _first(form, prefix + "size")
    variant_filter = format_variant_filter(parse_variant_filter(_first(form, prefix + "variant_filter")))
    exclude_terms = _first(form, prefix + "exclude_terms")
    priority = _first(form, prefix + "priority", DEFAULT_PRIORITY).casefold()
    if priority == "cycle":  # an already-open pre-update form can still save safely
        priority = normalize_priority(priority)
    if priority not in PRIORITIES:
        raise ValueError(f"{_card_context(index, name, [])}: öncelik geçersiz.")
    urls = list(dict.fromkeys(url for field in WATCH_URL_FIELDS if (url := _first(form, prefix + field))))
    if not any([name, target, size, *urls]):
        return None
    _require_valid_card(_card_context(index, name, urls), name, target, urls)
    item: Dict[str, Any] = {
        "id": _first(form, prefix + "id") or uuid.uuid4().hex,
        "name": name,
        "target_price": _price_from_form(target),
        # Choosing variant values reads the variant family.
        "include_variations": prefix + "include_variations" in form or bool(variant_filter),
        "priority": priority,
        "official_seller_only": prefix + "official_seller_only" in form,
        "auto_add_to_cart": prefix + "auto_add_to_cart" in form,
        "notify_once_in_24H": prefix + "notify_once_in_24H" in form,
        "notify_90_day_low": prefix + "notify_90_day_low" in form,
        "notify_window": normalize_notify_window(_first(form, prefix + "notify_window")),
        "active": prefix + "active" in form,
    }
    if minimum_price:
        item["minimum_price"] = _price_from_form(minimum_price)
    if exclude_terms:
        item["exclude_terms"] = exclude_terms
    if size:
        item["size"] = size
    if variant_filter:
        item["variant_filter"] = variant_filter
    for number, url in enumerate(urls, start=1):
        item[f"url_{number}"] = url
    return item


def _watch_count(form: Dict[str, List[str]]) -> int:
    """Posted card count, bounded so a forged value cannot spin the loop."""
    try:
        count = int(_first(form, "watches_count", "0") or 0)
    except ValueError as exc:
        raise ValueError("Takip sayısı geçersiz.") from exc
    if not 0 <= count <= MAX_WATCH_CARDS:
        raise ValueError(f"Takip sayısı geçersiz (en fazla {MAX_WATCH_CARDS}).")
    return count


def _require_matching_id(form: Dict[str, List[str]], index: int, existing_watches: List[Dict[str, Any]]) -> None:
    """The card the page names must still be the one stored at that position."""
    posted = _first(form, f"watches_{index}_id") or _first(form, "watch_id")
    if posted and (index >= len(existing_watches) or str(existing_watches[index].get("id") or "") != posted):
        raise ValueError(STALE_PAGE_MESSAGE)


def _require_current_list(form: Dict[str, List[str]], count: int, existing_watches: List[Dict[str, Any]]) -> None:
    """A whole-page save is refused when the stored list is no longer the one the page showed."""
    posted_ids = [_first(form, f"watches_{index}_id") for index in range(count)]
    if not any(posted_ids):
        return  # an older page or a bare API call without card ids
    for index, posted in enumerate(posted_ids):
        if posted:
            _require_matching_id(form, index, existing_watches)
    known = {posted for posted in posted_ids if posted}
    if any(str(item.get("id") or "") not in known for item in existing_watches):
        raise ValueError(STALE_PAGE_MESSAGE)


def build_watches(form: Dict[str, List[str]]) -> List[Dict[str, Any]]:
    watches = []
    for index in range(_watch_count(form)):
        if _first(form, f"watches_{index}_delete") in {"1", "true", "on", "yes"}:
            continue
        item = build_watch(form, index)
        if item:
            watches.append(item)
    return watches


def _posted_watch_index(form) -> str:
    """The one watch card present in a standalone update form."""
    indices = {match.group(1) for field_name in form if (match := re.match(r"^watches_(\d+)_", str(field_name)))}
    return indices.pop() if len(indices) == 1 else ""


def _update_telegram_options(options: Dict[str, Any], form) -> None:
    options["telegram_enabled"] = "telegram_enabled" in form
    # An unchecked box is simply absent from the posted form.
    options["telegram_saved_messages_enabled"] = "telegram_saved_messages_enabled" in form
    for key in ("api_id", "api_hash", "phone_number", "verification_code"):
        options[key] = _first(form, key)
    options["session_name"] = _first(form, "session_name", "telegram_keyword_alert") or "telegram_keyword_alert"
    options["channels"] = _list_from_text(_first(form, "channels")) or list(DEFAULT_TELEGRAM_CHANNELS)
    options["keywords"] = _list_from_text(_first(form, "keywords"))
    options["exclude_keywords"] = _list_from_text(_first(form, "exclude_keywords"))


def apply_settings_operation(existing_options: Dict[str, Any], form: Dict[str, List[str]]):
    """Return the complete new option set and a message for the user."""
    source = options_with_defaults(existing_options if isinstance(existing_options, dict) else {})
    existing_watches = [dict(item) for item in _as_list(source.get("takip_edilenler")) if isinstance(item, dict)]
    options = options_with_defaults(source)
    operation = _first(form, "operation", "update_existing")
    delete_index = _first(form, "delete_watch_index")
    update_index = _first(form, "update_watch_index", _first(form, "watch_index"))
    # A cached older page could submit a new-watch form as an update without an
    # index. Treat it as the addition it was meant to be.
    if operation == "update_watch" and update_index == "" and _first(form, "watch_index") == "":
        operation = "add_watch"
    if operation == "delete_watch" and delete_index == "":
        delete_index = _first(form, "watch_index")
    if update_index == "" and operation == "update_watch":
        update_index = _posted_watch_index(form)

    if delete_index != "":
        return _delete_watch(options, form, existing_watches, delete_index)
    if update_index != "":
        return _update_watch(options, form, existing_watches, update_index)
    if operation == "update_existing":
        return _update_all_watches(options, form, existing_watches)
    if operation == "add_watch":
        return _add_watch(options, form, existing_watches)
    if operation == "update_telegram":
        _update_telegram_options(options, form)
        return options, "Telegram ayarları güncellendi."
    raise ValueError("Bilinmeyen ayar kaydetme işlemi.")


def _watch_position(raw_index: str, existing_watches, invalid: str, missing: str) -> int:
    try:
        index = int(raw_index)
    except ValueError as exc:
        raise ValueError(invalid) from exc
    if not 0 <= index < len(existing_watches):
        raise ValueError(missing)
    return index


def _delete_watch(options, form, existing_watches, delete_index):
    index = _watch_position(delete_index, existing_watches, "Silinecek takip kaydı geçersiz.",
                            "Silinecek takip kaydı bulunamadı.")
    _require_matching_id(form, index, existing_watches)
    removed = existing_watches.pop(index)
    options["takip_edilenler"] = existing_watches
    return options, f"{str(removed.get('name') or '').strip() or f'Takip {index + 1}'} takip kaydı silindi."


def _update_watch(options, form, existing_watches, update_index):
    index = _watch_position(update_index, existing_watches, "Güncellenecek takip kaydı geçersiz.",
                            "Güncellenecek takip kaydı bulunamadı.")
    _require_matching_id(form, index, existing_watches)
    updated = build_watch(form, index)
    if not updated:
        raise ValueError(f"Takip {index + 1}: hedef fiyat ve en az bir link alanı zorunlu.")
    updated["id"] = existing_watches[index]["id"]
    updated["check_now_token"] = utc_now()
    existing_watches[index] = updated
    options["takip_edilenler"] = existing_watches
    return options, f"{watch_display_name(updated, index, {})} takip kaydı güncellendi."


def _update_all_watches(options, form, existing_watches):
    built = []
    count = _watch_count(form)
    _require_current_list(form, count, existing_watches)
    for index in range(count):
        if _first(form, f"watches_{index}_delete") in {"1", "true", "on", "yes"}:
            continue
        item = build_watch(form, index)
        if item:
            if index < len(existing_watches):
                item["id"] = existing_watches[index]["id"]
            built.append(item)
    options["takip_edilenler"] = built
    _update_telegram_options(options, form)
    _update_timing_options(options, form)
    options["daily_summary_enabled"] = "daily_summary_enabled" in form
    return options, "Ayarlar kaydedildi."


def _add_watch(options, form, existing_watches):
    new_watches = build_watches(form)
    if len(new_watches) != 1:
        raise ValueError("Yeni takip eklemek için hedef fiyat ve en az bir link alanı zorunlu."
                         if not new_watches else "Yeni takip ekleme formunda yalnızca bir kayıt bulunmalı.")
    new_watches[0]["check_now_token"] = utc_now()
    options["takip_edilenler"] = existing_watches + new_watches
    return options, "Yeni takip kaydı eklendi."


def handle_settings_save(body: bytes):
    try:
        form = urllib.parse.parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True)
        with OPTIONS_LOCK:  # read, change and save as one step so two saves cannot overwrite each other
            options, change_message = apply_settings_operation(read_options(), form)
            save_options_and_restart(options)
        log("Ayarlar Home Assistant config'e kaydedildi; Hermes yeniden başlatılacak.")
        return True, f"{change_message} Hermes yeniden başlatılıyor; 10-20 saniye sonra sayfayı yenileyebilirsin."
    except Exception as exc:  # noqa: BLE001
        return False, f"Ayarlar kaydedilemedi: {exc}"


def handle_restart():
    """"Hermes'i yeniden başlat": the add-on restarts through Home Assistant, settings unchanged."""
    try:
        schedule_restart()
        log("Panelden yeniden başlatma istendi; Hermes Home Assistant üzerinden yeniden başlatılacak.")
        return True, "Hermes yeniden başlatılıyor; ayarlar değişmedi."
    except Exception as exc:  # noqa: BLE001
        return False, f"Hermes yeniden başlatılamadı: {exc}"


def _distinct_parts(text: str) -> str:
    """"Ürün / Ürün / Siyah" -> "Ürün / Siyah": some sites repeat the name in their variant titles."""
    return " / ".join(dict.fromkeys(part.strip() for part in str(text or "").split(" / ") if part.strip()))


def check_link(url: str, providers) -> Dict[str, Any]:
    """The new-card link check: the site and its live product, variants and choice boxes."""
    url = str(url or "").strip()
    try:
        site = detect_site_from_url(url)
    except HermesError:
        return {"ok": False, "error": "Bu site desteklenmiyor."}
    provider = providers[site]
    result: Dict[str, Any] = {"site": site, "options": list(provider.card_options)}
    try:
        with traffic.bind(site), requests.Session() as session:
            preview = provider.inspect(url, ReadContext(timeout=DEFAULT_REQUEST_TIMEOUT_SECONDS, session=session))
    except Exception as exc:  # noqa: BLE001
        log(f"Link kontrolü okunamadı: {site_label(site)} | {exc}")
        return {**result, "ok": False, "error": f"Sayfa okunamadı: {exc}"}
    return {
        **result,
        "ok": True,
        "title": _distinct_parts(preview.title),
        "variants": [{"label": _distinct_parts(variant.label), "price": format_tl(variant.price, with_currency=True) if variant.price is not None else "",
                      "in_stock": variant.in_stock, "attributes": variant.attributes} for variant in preview.variants],
        "dimensions": preview.dimensions,
    }
