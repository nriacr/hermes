"""Settings page: tracking cards, Telegram and timing options, saved once with one restart."""

import re
import urllib.parse
from html import escape
from typing import Any, Dict, List, Optional

from ..config import DEFAULT_TELEGRAM_CHANNELS, WATCH_URL_FIELDS, options_with_defaults, read_options, watch_group, watch_urls
from ..constants import (
    APP_VERSION, DEFAULT_PRIORITY, PRIORITIES, PRIORITY_DESCRIPTIONS, PRIORITY_LABELS, STATE_PATH, SUMMARY_PATH, normalize_priority,
)
from ..logging_utils import log
from ..storage import load_json
from ..supervisor import save_options_and_restart, schedule_restart
from ..utils import detect_site_from_url, format_tl, parse_bool, parse_decimal, site_label, utc_now, watch_name_required_for_url
from .dashboard import priority_dot
from .pages import link, render_notice, render_page, render_tool_actions, CONFIRM_SCRIPT

OTHER_GROUP = "Diğer"


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
    """A new card starts at every cycle; a saved card shows its priority (pre-3.12 cards: 6 hours)."""
    return DEFAULT_PRIORITY if is_new else normalize_priority(item.get("priority"))


def watch_form(item: Dict[str, Any], index: int, is_new: bool = False, groups=None, known_titles=None, show_remove=False) -> str:
    prefix = f"watches_{index}_"
    group = watch_group(item) or OTHER_GROUP
    display_name = watch_display_name(item, index, known_titles or {})
    title = "Yeni takip ekle" if is_new else f"[{group}] {display_name}"
    group_choices = list(groups or [])
    if not is_new and group != OTHER_GROUP and group not in group_choices:
        group_choices.append(group)
    urls = watch_urls(item)
    selected_group = "" if is_new else (str(item.get("group") or "").strip() or (group if group != OTHER_GROUP else ""))
    exclude_terms = item.get("exclude_terms", "") if isinstance(item, dict) else ""
    if isinstance(exclude_terms, list):
        exclude_terms = ", ".join(str(term).strip() for term in exclude_terms if str(term).strip())
    priority = _card_priority(item, is_new)
    link_fields = "".join(
        _field(prefix, field_name, f"Link {number}", urls[number - 1] if len(urls) >= number else "", "url")
        for number, field_name in enumerate(WATCH_URL_FIELDS, start=1)
    )
    priority_field = (
        f"<label class='watch-priority'>Öncelik<select name='{escape(prefix + 'priority', quote=True)}' data-watch-priority>"
        + "".join(f"<option value='{value}' title='{escape(PRIORITY_DESCRIPTIONS[value], quote=True)}'"
                  f"{' selected' if priority == value else ''}>{escape(PRIORITY_LABELS[value])}</option>" for value in PRIORITIES)
        + "</select></label>"
    )
    exclude_field = _field(prefix, "exclude_terms", "Hariç Tut", exclude_terms).replace("<label>", "<label class='watch-exclude'>", 1)
    flags = "".join([
        _checkbox(prefix, "include_variations", "Varyasyonları ekle", False if is_new else item.get("include_variations", False)),
        _checkbox(prefix, "official_seller_only", "Yalnızca platformun kendi satıcısı", False if is_new else item.get("official_seller_only", False)),
        _checkbox(prefix, "notify_once_in_24H", "24 saat sustur", True if is_new else item.get("notify_once_in_24H", True)),
        _checkbox(prefix, "active", "Aktif", True if is_new else item.get("active", True)),
    ])
    if is_new:
        actions = ("<div class='watch-actions'><button class='button secondary' type='button' data-remove-new-watch>Bu kartı kaldır</button></div>"
                   if show_remove else "")
    else:
        actions = "<div class='watch-actions'><button class='button danger' type='button' data-delete-watch>Sil</button></div>"
    inner = (
        f"<input type='hidden' name='{escape(prefix + 'delete', quote=True)}' value='0' data-delete-flag>"
        "<div class='watch-layout'><div class='watch-top'>"
        f"{_select(prefix, 'group', 'Grup', selected_group, group_choices)}"
        f"{_field(prefix, 'name', 'Ad', item.get('name', ''))}"
        f"{_field(prefix, 'target_price', 'Hedef Fiyat Maks', _price_input_value(item.get('target_price', '')))}"
        f"{_field(prefix, 'minimum_price', 'Hedef Fiyat Min', _price_input_value(item.get('minimum_price', '')))}"
        f"{_field(prefix, 'size', 'Beden', item.get('size', ''))}"
        f"</div><div class='watch-links'>{link_fields}</div>"
        f"<div class='watch-bottom'>{priority_field}{exclude_field}{flags}{actions}"
        "<p class='watch-hint'>Öncelik, fiyatın ne sıklıkla tarandığıdır: her çevrimde, 30 dk, 60 dk, 3 saat ya da 6 saatte bir. Satıcı filtresi şu an Amazon’da uygulanır; "
        "doğrulanmış Depo teklifleri korunur.</p></div></div>"
    )
    attributes = " data-new-watch='true'" if is_new else (
        f" data-watch-group='{escape(group, quote=True)}' data-watch-search='{escape(display_name, quote=True)}'"
    )
    dot = "" if is_new else priority_dot(priority)
    return f"<details data-watch-card{attributes}><summary>{dot}{escape(title)}</summary><div class='form-grid'>{inner}</div></details>"


def group_choices(configured_groups, items) -> List[str]:
    groups: List[str] = []
    for value in [str(group or "").strip() for group in configured_groups or []] + [
            watch_group(item) or OTHER_GROUP for item in _as_list(items)]:
        if value and value.casefold() not in {existing.casefold() for existing in groups}:
            groups.append(value)
    return groups


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


# Supervisor options shown at the bottom of Ayarlar: (key, label, note, lowest, highest).
TIMING_FIELDS = (
    ("interval_seconds", "Çevrim aralığı", "İki kontrol turu arasındaki bekleme (saniye)", 1, 86400),
    ("request_delay_min_seconds", "Bekleme süresi min", "Aynı sitede iki istek arasındaki en kısa süre (saniye)", 0, 120),
    ("request_delay_max_seconds", "Bekleme süresi maks", "Aynı sitede iki istek arasındaki en uzun süre (saniye)", 0, 120),
)


def _timing_section(options: Dict[str, Any]) -> str:
    fields = "".join(
        f"<label>{escape(label)}<input type='number' inputmode='numeric' name='{key}' min='{lowest}' max='{highest}' step='1' "
        f"value='{escape(str(options.get(key, '')), quote=True)}' required><small>{escape(note)}</small></label>"
        for key, label, note, lowest, highest in TIMING_FIELDS
    )
    return f"<section class='settings-section timing-settings'><h2>Zamanlama</h2><div class='form-grid'>{fields}</div></section>"


def _update_timing_options(options: Dict[str, Any], form) -> None:
    """Same limits as the add-on schema; a page without these fields keeps the saved values."""
    values = {}
    for key, label, _note, lowest, highest in TIMING_FIELDS:
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


def render_settings_page(base: str, params: Dict[str, List[str]]) -> bytes:
    options = read_options()
    configured_groups = _list_from_text("\n".join(str(group) for group in _as_list(options.get("gruplar"))))
    known_titles = stored_watch_titles()
    watches = _as_list(options.get("takip_edilenler"))
    groups = group_choices(configured_groups, watches)
    new_index = len(watches)
    template = watch_form({}, 0, is_new=True, groups=groups, show_remove=True).replace("watches_0_", "watches___INDEX___")
    filters = "".join(
        f"<button class='watch-group-filter' type='button' data-watch-group-filter='{escape(group, quote=True)}' aria-pressed='true'>{escape(group)}</button>"
        for group in group_choices(configured_groups, watches)
    )
    if filters:
        filters = f"<div class='watch-group-filters' aria-label='Takip edilen grup filtreleri'>{filters}</div>"
    notice = render_notice(params.get("saved", [""])[0], params.get("msg", [""])[0])
    body = (
        f"<h2 class='page-heading'>Ayarlar</h2>{notice}"
        f"<form id='settings-form' method='post' action='{escape(link(base, 'settings/save'), quote=True)}' data-settings-save>"
        "<input type='hidden' name='operation' value='update_existing'>"
        f"<input type='hidden' name='watches_count' id='watches-count' value='{new_index + 1}'>"
        "<section class='settings-section'><h2>Yeni takip ekle</h2>"
        f"<div id='new-watch-list'>{watch_form({}, new_index, is_new=True, groups=groups, known_titles=known_titles)}</div>"
        "<div class='actions'><button class='button secondary' type='button' id='add-watch-card'>Başka takip ekle</button></div>"
        f"<template id='new-watch-template'>{template}</template></section>"
        "<section class='settings-section watch-tools'><label class='watch-search'>Takip edilenlerde ara"
        "<input id='watch-search' type='search' placeholder='Ürün adında ara' autocomplete='off'></label>"
        f"{filters}"
        "</section>"
        "<section class='settings-section'><h2>Takip edilenler</h2>"
        + "".join(watch_form(item if isinstance(item, dict) else {}, index, groups=groups, known_titles=known_titles)
                  for index, item in enumerate(watches))
        + "</section>"
        + _telegram_section(options)
        + _timing_section(options)
        + "<div class='apply-bar'><p>Grup, fiyat, yeni kayıt ve silme işlemlerini sırayla yap; bitince bir kez uygula. "
        "Hermes yalnız o zaman yeniden başlar.</p><button class='button primary' type='submit'>Değişiklikleri uygula</button></div></form>"
        + render_tool_actions(base)
    )
    overlay = ("<div id='saving-overlay' class='saving-overlay' hidden><div class='saving-dialog'><div class='saving-spinner'></div>"
               "<h2 id='saving-title'>Ayarlar kaydediliyor</h2><p id='saving-message'>Tüm değişiklikler tek seferde Home Assistant'a "
               "yazılıyor. Hermes bir kez yeniden başlayacak; hazır olduğunda ayarlara otomatik dönülecek.</p></div></div>")
    script = f"<script src='{escape(link(base, 'settings.js'), quote=True)}?v={escape(APP_VERSION)}' defer></script>"
    return render_page(base, "settings", "Hermes Ayarlar", body, body_class="public settings-page", after_main=overlay, scripts=CONFIRM_SCRIPT + script)


def render_restart_page(base: str, params: Dict[str, List[str]]) -> bytes:
    message = params.get("msg", ["Ayarlar kaydedildi. Hermes yeniden başlatılıyor."])[0]
    return_to_main = params.get("return_to_main", [""])[0] == "1"
    destination = link(base, "") if return_to_main else link(base, "settings")
    body = (
        "<h2 class='page-heading'>Hermes yeniden başlatılıyor</h2>"
        f"<p class='notice notice-ok'>{escape(message)}</p>"
        "<p>Hermes yeniden başlarken bu sayfa kısa süre bekleyecek; "
        f"hazır olduğunda {'ana ekran' if return_to_main else 'ayarlar ekranı'} otomatik yenilenecek.</p>"
        "<p class='footer-note' id='restart-status'>Hazırlanıyor... Birkaç saniye içinde bağlantı kontrolü başlayacak.</p>"
        f"<div class='actions'><a class='button secondary' href='{escape(destination, quote=True)}'>"
        f"{'Ana ekrana dön' if return_to_main else 'Ayarlar ekranına dön'}</a></div>"
    )
    script = (f"<script id='hermes-restart-script' src='{escape(link(base, 'restart.js'), quote=True)}?v={escape(APP_VERSION)}' defer "
              f"data-settings-path='{escape(link(base, 'settings'), quote=True)}' data-return-path='{escape(destination, quote=True)}' "
              f"data-health-path='{escape(link(base, 'health'), quote=True)}'></script>")
    return render_page(base, "settings", "Hermes yeniden başlatılıyor", body, scripts=script)


# -- saving ---------------------------------------------------------------------------


def _price_from_form(value) -> int:
    try:
        return int(parse_decimal(value))
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Hedef fiyat geçersiz: {value!r}") from exc


def _card_context(index: int, name: str, urls: List[str]) -> str:
    identity = name or (urls[0] if urls else "yeni kayıt")
    return f"Takip {index + 1} ({identity[:93] + '...' if len(identity) > 96 else identity})"


def build_watch(form: Dict[str, List[str]], index: int) -> Optional[Dict[str, Any]]:
    prefix = f"watches_{index}_"
    name = _first(form, prefix + "name")
    group = _first(form, prefix + "group")
    target = _first(form, prefix + "target_price")
    minimum_price = _first(form, prefix + "minimum_price")
    size = _first(form, prefix + "size")
    exclude_terms = _first(form, prefix + "exclude_terms")
    priority = _first(form, prefix + "priority", DEFAULT_PRIORITY).casefold()
    if priority not in PRIORITIES:
        raise ValueError(f"{_card_context(index, name, [])}: öncelik geçersiz.")
    urls = list(dict.fromkeys(url for field in WATCH_URL_FIELDS if (url := _first(form, prefix + field))))
    if not any([name, target, size, *urls]):
        return None
    context = _card_context(index, name, urls)
    missing = [label for label, value in (("hedef fiyat", target), ("en az bir link", urls)) if not value]
    if missing:
        raise ValueError(f"{context}: {', '.join(missing)} alanı zorunlu.")
    if not name and any(watch_name_required_for_url(url) for url in urls):
        raise ValueError(f"{context}: bu bağlantı bir arama sayfası. Arama sonuçlarını doğru filtrelemek için "
                         "Ad alanı zorunlu; örneğin ürün modelini yazmalısın.")
    item: Dict[str, Any] = {
        "name": name,
        "group": group or watch_group({f"url_{number}": url for number, url in enumerate(urls, start=1)}),
        "target_price": _price_from_form(target),
        "include_variations": prefix + "include_variations" in form,
        "priority": priority,
        "official_seller_only": prefix + "official_seller_only" in form,
        "notify_once_in_24H": prefix + "notify_once_in_24H" in form,
        "active": prefix + "active" in form,
    }
    if minimum_price:
        item["minimum_price"] = _price_from_form(minimum_price)
    if exclude_terms:
        item["exclude_terms"] = exclude_terms
    if size:
        item["size"] = size
    for number, url in enumerate(urls, start=1):
        item[f"url_{number}"] = url
    return item


def build_watches(form: Dict[str, List[str]]) -> List[Dict[str, Any]]:
    watches = []
    for index in range(int(_first(form, "watches_count", "0") or 0)):
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
    source = existing_options if isinstance(existing_options, dict) else {}
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
        try:
            index = int(delete_index)
        except ValueError as exc:
            raise ValueError("Silinecek takip kaydı geçersiz.") from exc
        if not 0 <= index < len(existing_watches):
            raise ValueError("Silinecek takip kaydı bulunamadı.")
        removed = existing_watches.pop(index)
        options["takip_edilenler"] = existing_watches
        return options, f"{str(removed.get('name') or '').strip() or f'Takip {index + 1}'} takip kaydı silindi."
    if update_index != "":
        try:
            index = int(update_index)
        except ValueError as exc:
            raise ValueError("Güncellenecek takip kaydı geçersiz.") from exc
        if not 0 <= index < len(existing_watches):
            raise ValueError("Güncellenecek takip kaydı bulunamadı.")
        updated = build_watch(form, index)
        if not updated:
            raise ValueError(f"Takip {index + 1}: hedef fiyat ve en az bir link alanı zorunlu.")
        updated["check_now_token"] = utc_now()
        existing_watches[index] = updated
        options["takip_edilenler"] = existing_watches
        return options, f"{watch_display_name(updated, index, {})} takip kaydı güncellendi."
    if operation == "update_existing":
        options["takip_edilenler"] = build_watches(form)
        _update_telegram_options(options, form)
        _update_timing_options(options, form)
        return options, "Ayarlar kaydedildi."
    if operation == "add_watch":
        new_watches = build_watches(form)
        if len(new_watches) != 1:
            raise ValueError("Yeni takip eklemek için hedef fiyat ve en az bir link alanı zorunlu."
                             if not new_watches else "Yeni takip ekleme formunda yalnızca bir kayıt bulunmalı.")
        new_watches[0]["check_now_token"] = utc_now()
        options["takip_edilenler"] = existing_watches + new_watches
        return options, "Yeni takip kaydı eklendi."
    if operation == "update_telegram":
        _update_telegram_options(options, form)
        return options, "Telegram ayarları güncellendi."
    raise ValueError("Bilinmeyen ayar kaydetme işlemi.")


def handle_settings_save(body: bytes):
    try:
        form = urllib.parse.parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True)
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


def should_return_to_main(body: bytes) -> bool:
    form = urllib.parse.parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True)
    return _first(form, "return_to_main") == "1"
