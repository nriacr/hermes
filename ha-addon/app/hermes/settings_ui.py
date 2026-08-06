import json
import os
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from copy import deepcopy
from html import escape

from .config_loader import DEFAULT_TELEGRAM_CHANNELS
from .constants import OPTIONS_PATH, SITE_HM, SITE_ZARA, STATE_PATH, SUMMARY_PATH
from .logging_utils import log
from .storage import load_json, save_json
from .utils import detect_site_from_url, format_tl, parse_bool, parse_decimal, site_label, watch_name_required_for_url

ADDON_SLUG = "hermes"
SUPERVISOR_BASE_URL = "http://supervisor"
WATCH_URL_FIELDS = ("url_1", "url_2", "url_3", "url_4", "url_5")

SETTINGS_CSS = """
:root {
  color-scheme: dark;
  --bg: #121519;
  --panel: #1b1e24;
  --card: #242930;
  --line: #323843;
  --text: #eef1f5;
  --muted: #a2afbd;
  --accent: #c2d5f0;
  --accent2: #d6e4f7;
  --ok: #b2ebd5;
  --bad: #fca6b5;
  --transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
}
* {
  box-sizing: border-box;
  -webkit-tap-highlight-color: transparent;
}
body {
  margin: 0;
  font-family: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
  background: radial-gradient(circle at top left, #29303c, var(--bg) 65%);
  color: var(--text);
  font-size: 14px;
  line-height: 1.5;
  letter-spacing: -0.01em;
}
main {
  max-width: 1020px;
  margin: 0 auto;
  padding: 32px 20px 48px;
}
.hero {
  border: 1px solid var(--line);
  border-radius: 24px;
  padding: 28px;
  background: rgba(27, 30, 36, 0.85);
  backdrop-filter: blur(16px);
  box-shadow: 0 20px 50px rgba(0,0,0,0.4);
}
h1 {
  margin: 0 0 12px;
  color: #ffbba6;
  font-size: 34px;
  font-weight: 800;
  letter-spacing: -0.03em;
}
h2 {
  margin: 28px 0 14px;
  font-size: 19px;
  font-weight: 700;
  letter-spacing: -0.01em;
}
p {
  margin: 0;
  color: var(--muted);
  line-height: 1.6;
  font-size: 13.5px;
}
.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin: 24px 0;
}
.button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 42px;
  padding: 0 18px;
  border-radius: 14px;
  border: 1px solid transparent;
  text-decoration: none;
  font-weight: 700;
  font-size: 13px;
  cursor: pointer;
  transition: var(--transition);
}
.button.primary {
  color: #111418;
  background: linear-gradient(135deg, var(--accent), var(--accent2));
  box-shadow: 0 4px 12px rgba(194, 213, 240, 0.25);
}
.button.primary:hover {
  transform: translateY(-2px);
  box-shadow: 0 6px 16px rgba(194, 213, 240, 0.35);
}
.button.secondary {
  color: var(--text);
  background: #2a2f38;
  border-color: var(--line);
}
.button.secondary:hover {
  background: #363d49;
  border-color: #4b5566;
  transform: translateY(-1px);
}
.button.danger {
  color: #fff;
  background: linear-gradient(135deg, #cf465f, #b9364d);
  border-color: #ed7288;
  box-shadow: 0 4px 12px rgba(185, 54, 77, 0.2);
}
.button.danger:hover {
  background: linear-gradient(135deg, #e0526c, #cf465f);
  transform: translateY(-2px);
  box-shadow: 0 6px 16px rgba(185, 54, 77, 0.3);
}
.button.is-submitting {
  pointer-events: none;
  opacity: .7;
}
.notice {
  margin: 18px 0;
  padding: 14px 16px;
  border-radius: 14px;
  font-weight: 600;
  font-size: 13.5px;
  line-height: 1.4;
}
.notice-ok {
  color: #c9ebd8;
  background: rgba(178, 235, 213, 0.1);
  border: 1px solid rgba(178, 235, 213, 0.35);
}
.notice-fail {
  color: #ffcad1;
  background: rgba(252, 166, 181, 0.1);
  border: 1px solid rgba(252, 166, 181, 0.35);
}
.settings-section {
  margin-top: 24px;
  border: 1px solid var(--line);
  border-radius: 20px;
  padding: 20px;
  background: var(--card);
}
details {
  border: 1px solid var(--line);
  border-radius: 16px;
  background: #1f242d;
  margin: 12px 0;
  overflow: hidden;
  transition: var(--transition);
}
details:hover {
  border-color: #434c5b;
}
summary {
  cursor: pointer;
  padding: 14px 16px;
  font-weight: 700;
  color: #f5f7fa;
  list-style: none;
  font-size: 14.5px;
  display: flex;
  align-items: center;
}
summary::-webkit-details-marker {
  display: none;
}
summary::before {
  content: '▸';
  display: inline-block;
  margin-right: 10px;
  color: var(--muted);
  font-size: 18px;
  transition: transform 0.2s ease;
}
details[open] summary::before {
  transform: rotate(90deg);
}
details[open] summary {
  border-bottom: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.02);
}
.watch-tools {
  display: grid;
  gap: 16px;
}
.watch-search {
  display: grid;
  gap: 8px;
  max-width: 480px;
  margin: 0;
  color: var(--muted);
  font-size: 12px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
.watch-search input {
  width: 100%;
  min-height: 42px;
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 10px 14px;
  background: #141619;
  color: var(--text);
  font: inherit;
  font-weight: 500;
  letter-spacing: normal;
  text-transform: none;
  transition: var(--transition);
}
.watch-search input:focus {
  outline: none;
  border-color: var(--accent);
  box-shadow: 0 0 0 3px rgba(194, 213, 240, 0.15);
}
.watch-group-filters {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin: 0;
}
.watch-group-filter {
  min-height: 36px;
  border: 1px solid var(--line);
  border-radius: 999px;
  padding: 0 16px;
  background: #2a2f38;
  color: var(--text);
  font: inherit;
  font-size: 12.5px;
  font-weight: 600;
  cursor: pointer;
  transition: var(--transition);
}
.watch-group-filter[aria-pressed='false'] {
  color: var(--muted);
  background: #15181d;
  opacity: .65;
  text-decoration: line-through;
}
.watch-group-filter:hover {
  border-color: #5c6475;
  transform: translateY(-1px);
}
.watch-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 42px;
}
.saving-overlay {
  position: fixed;
  inset: 0;
  z-index: 20;
  display: grid;
  place-items: center;
  padding: 20px;
  background: rgba(10, 12, 16, 0.85);
  backdrop-filter: blur(8px);
}
.saving-overlay[hidden] {
  display: none;
}
.saving-dialog {
  width: min(100%, 450px);
  border: 1px solid var(--line);
  border-radius: 24px;
  padding: 28px;
  background: var(--card);
  box-shadow: 0 25px 60px rgba(0,0,0,0.6);
  text-align: center;
}
.saving-dialog h2 {
  margin: 0 0 10px;
  font-size: 21px;
}
.saving-dialog p {
  font-size: 14px;
  color: var(--muted);
}
.saving-spinner {
  width: 36px;
  height: 36px;
  margin: 0 auto 18px;
  border: 4px solid rgba(255, 255, 255, 0.1);
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: hermes-spin .8s cubic-bezier(0.5, 0.1, 0.1, 0.5) infinite;
}
@keyframes hermes-spin {
  to { transform: rotate(360deg); }
}
.form-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
  gap: 14px;
  padding: 14px;
}
label {
  display: grid;
  gap: 6px;
  color: var(--muted);
  font-size: 12px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
input[type='text'], input[type='number'], input[type='url'], select, textarea {
  width: 100%;
  min-height: 42px;
  border-radius: 12px;
  border: 1px solid var(--line);
  background: #141619;
  color: var(--text);
  padding: 10px 12px;
  font-size: 13.5px;
  font-family: inherit;
  font-weight: 500;
  letter-spacing: normal;
  text-transform: none;
  transition: var(--transition);
}
input[type='text']:focus, input[type='number']:focus, input[type='url']:focus, select:focus, textarea:focus {
  outline: none;
  border-color: var(--accent);
  box-shadow: 0 0 0 3px rgba(194, 213, 240, 0.15);
}
textarea {
  resize: vertical;
  line-height: 1.4;
}
.checkbox-row {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 42px;
  color: var(--text);
  cursor: pointer;
}
.checkbox-row input {
  width: 18px;
  height: 18px;
  accent-color: var(--accent);
  cursor: pointer;
}
.danger {
  color: #ffcad1;
}
.footer-note {
  margin-top: 18px;
  border-left: 4px solid var(--muted);
  padding: 14px 16px;
  background: rgba(255, 255, 255, 0.02);
  border-radius: 12px;
  font-size: 13.5px;
}
.watch-layout {
  grid-column: 1 / -1;
  display: grid;
  gap: 14px;
}
.watch-top {
  display: grid;
  grid-template-columns: minmax(110px, .7fr) minmax(220px, 2fr) minmax(110px, .65fr) minmax(120px, .75fr) minmax(100px, .6fr);
  gap: 14px;
  align-items: end;
}
.watch-links {
  display: grid;
  gap: 10px;
}
.watch-bottom {
  display: flex;
  flex-wrap: wrap;
  align-items: end;
  gap: 14px;
}
.watch-bottom > label:first-child {
  flex: 0 1 210px;
  max-width: 210px;
}
.watch-exclude {
  flex: 1 1 250px;
}
.watch-bottom .checkbox-row {
  flex: 0 0 auto;
  padding-bottom: 1px;
}
.watch-bottom .watch-actions {
  margin-left: auto;
}
@media (max-width: 900px) {
  .watch-top { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 700px) {
  .watch-bottom > label:first-child { flex: 1 1 100%; max-width: none; }
  .watch-bottom .watch-actions { width: 100%; margin-left: 0; }
  .watch-bottom .watch-actions .button { width: 100%; min-height: 46px; }
}
@media (max-width: 420px) {
  .watch-top { grid-template-columns: 1fr; }
}
"""

SETTINGS_SCRIPT = """
(() => {
  const storageKey = 'hermes-hidden-watch-groups';
  const hiddenGroups = new Set(JSON.parse(localStorage.getItem(storageKey) || '[]'));
  const normalize = (value) => String(value || '').toLocaleLowerCase('tr-TR');
  const watchSearch = document.getElementById('watch-search');

  const refreshWatchList = () => {
    const searchText = normalize(watchSearch?.value.trim());
    document.querySelectorAll('[data-watch-group]').forEach((item) => {
      const groupHidden = hiddenGroups.has(normalize(item.dataset.watchGroup || 'Diğer'));
      const productName = normalize(item.dataset.watchSearch);
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
      refreshWatchList();
    });
  });
  watchSearch?.addEventListener('input', refreshWatchList);
  refreshWatchList();

  const savingOverlay = document.getElementById('saving-overlay');
  const savingTitle = document.getElementById('saving-title');
  const savingMessage = document.getElementById('saving-message');
  document.querySelectorAll('form[data-settings-save]').forEach((form) => {
    form.addEventListener('submit', (event) => {
      const button = event.submitter;
      const isDelete = button?.dataset.deleteWatch === 'true';
      const operation = form.querySelector('input[name="operation"]');
      // New-watch and Telegram forms already declare their own operation.
      // Only an existing watch card can switch between update and delete.
      if (operation?.value === 'update_watch') {
        operation.value = isDelete ? 'delete_watch' : 'update_watch';
      }
      if (button) {
        // Do not disable the submitter here: mobile Safari then excludes its
        // name/value from the submitted form and turns a delete into an update.
        button.classList.add('is-submitting');
        button.setAttribute('aria-disabled', 'true');
        button.textContent = isDelete ? 'Siliniyor...' : 'Kaydediliyor...';
      }
      savingTitle.textContent = isDelete ? 'Takip siliniyor' : 'Ayarlar kaydediliyor';
      savingMessage.textContent = isDelete
        ? 'Takip kaydı kaldırılıyor. Hermes yeniden başlatılacak; hazır olduğunda ayarlara otomatik dönülecek.'
        : 'Hermes değişiklikleri Home Assistant’a yazıyor. Ardından kısa bir yeniden başlatma yapılacak; hazır olduğunda ayarlara otomatik dönülecek.';
      savingOverlay.hidden = false;
    });
  });
})();
""".strip()

SETTINGS_RESTART_SCRIPT = """
(() => {
  const script = document.getElementById('hermes-restart-script');
  const settingsPath = script?.dataset.settingsPath || '../settings';
  const returnPath = script?.dataset.returnPath || settingsPath;
  const healthPath = script?.dataset.healthPath || '../health';
  const statusBox = document.getElementById('restart-status');
  let attempts = 0;

  const waitForHermes = async () => {
    attempts += 1;
    statusBox.textContent = `Hermes kontrol ediliyor... Deneme ${attempts}`;
    try {
      const response = await fetch(`${healthPath}?ts=${Date.now()}`, { cache: 'no-store' });
      if (response.ok) {
        statusBox.textContent = 'Hermes hazır. Sayfa yenileniyor...';
        const separator = returnPath.includes('?') ? '&' : '?';
        window.location.href = `${returnPath}${separator}saved=ok&msg=${encodeURIComponent('Hermes hazır. Ayarlar güncellendi.')}`;
        return;
      }
    } catch (_error) {
      statusBox.textContent = 'Hermes yeniden başlıyor, bağlantı bekleniyor...';
    }
    window.setTimeout(waitForHermes, 2000);
  };

  window.setTimeout(waitForHermes, 6000);
})();
""".strip()


def _as_list(value):
    return value if isinstance(value, list) else []


def _first(form, key, default=""):
    values = form.get(key)
    if not values:
        return default
    return str(values[0]).strip()


def should_return_to_main_after_save(body) -> bool:
    """Existing watch cards request a return to the dashboard after restart."""
    if not isinstance(body, (bytes, bytearray)):
        return False
    form = urllib.parse.parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True)
    return _first(form, "return_to_main") == "1"


def _number(value):
    text = str(value or "").strip()
    if text == "":
        return None
    try:
        return int(text) if text.isdigit() else float(text)
    except ValueError:
        return text


def _price_input_value(value):
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return format_tl(parse_decimal(text))
    except Exception:  # noqa: BLE001
        return text


def _price_from_form(value):
    try:
        return int(parse_decimal(value))
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Hedef fiyat geçersiz: {value!r}") from exc


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


def _watch_form(item, index, is_new=False, groups=None, known_titles=None):
    prefix = f"watches_{index}_"
    group = _watch_group(item)
    display_name = _watch_display_name(item, index, known_titles or {})
    title = "Yeni takip ekle" if is_new else f"[{group}] {display_name}"
    group_choices = list(groups or [])
    if not is_new and group != "Diğer" and group not in group_choices:
        group_choices.append(group)
    urls = _watch_urls_for_form(item)
    notify_once = True if is_new else item.get("notify_once_in_24H", True)
    active = True if is_new else item.get("active", True)
    include_variations = False if is_new else item.get("include_variations", False)
    selected_group = "" if is_new else str(item.get("group") or "").strip()
    if not selected_group and group == "Moda":
        selected_group = "Moda"
    exclude_terms = item.get("exclude_terms", "") if isinstance(item, dict) else ""
    if isinstance(exclude_terms, list):
        exclude_terms = ", ".join(str(term).strip() for term in exclude_terms if str(term).strip())
    link_fields = "".join(
        _field(
            prefix,
            field_name,
            f"Link {url_index}",
            urls[url_index - 1] if len(urls) >= url_index else "",
            "url",
        )
        for url_index, field_name in enumerate(WATCH_URL_FIELDS, start=1)
    )
    interval_field = _field(
        prefix,
        "check_interval_minutes",
        "Özel kontrol aralığı (dk)",
        item.get("check_interval_minutes", ""),
        "number",
    )
    exclude_field = _field(
        prefix,
        "exclude_terms",
        "Hariç Tut",
        exclude_terms,
    ).replace("<label>", "<label class='watch-exclude'>", 1)
    notification_fields = "".join(
        [
            _checkbox(prefix, "include_variations", "Varyasyonları ekle", include_variations),
            _checkbox(prefix, "notify_once_in_24H", "24 saat sustur", notify_once),
            _checkbox(prefix, "active", "Aktif", active),
        ]
    )
    action_fields = (
        "<div class='watch-actions'><button class='button primary' type='submit'>Yeni Takibi Ekle</button></div>"
        if is_new
        else (
            "<div class='watch-actions'>"
            f"<button class='button primary' type='submit' name='update_watch_index' value='{index}' "
            "data-update-watch='true'>Güncelle</button>"
            f"<button class='button danger' type='submit' name='delete_watch_index' value='{index}' "
            "data-delete-watch='true' formnovalidate>Sil</button>"
            "</div>"
        )
    )
    inner = (
        "<div class='watch-layout'>"
        "<div class='watch-top'>"
        f"{_select(prefix, 'group', 'Grup', selected_group, group_choices)}"
        f"{_field(prefix, 'name', 'Ad', item.get('name', ''))}"
        f"{_field(prefix, 'target_price', 'Hedef Fiyat Maks', _price_input_value(item.get('target_price', '')))}"
        f"{_field(prefix, 'minimum_price', 'Hedef Fiyat Min', _price_input_value(item.get('minimum_price', '')))}"
        f"{_field(prefix, 'size', 'Beden', item.get('size', ''))}"
        "</div>"
        f"<div class='watch-links'>{link_fields}</div>"
        "<div class='watch-bottom'>"
        f"{interval_field}{exclude_field}{notification_fields}{action_fields}"
        "</div></div>"
    )
    group_attribute = "" if is_new else f" data-watch-group='{escape(group, quote=True)}'"
    search_attribute = "" if is_new else f" data-watch-search='{escape(display_name, quote=True)}'"
    details = (
        f"<details{group_attribute}{search_attribute}>"
        f"<summary>{escape(title)}</summary><div class='form-grid'>{inner}</div></details>"
    )
    if is_new:
        return details
    return (
        "<form method='post' action='./settings/save' data-settings-save>"
        "<input type='hidden' name='operation' value='update_watch'>"
        f"<input type='hidden' name='watch_index' value='{index}'>"
        "<input type='hidden' name='return_to_main' value='1'>"
        f"{details}</form>"
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
    def render_watch(item, index):
        return _watch_form(
            item if isinstance(item, dict) else {},
            index,
            groups=groups,
            known_titles=known_titles,
        )

    watches_html = "".join(render_watch(item, index) for index, item in enumerate(safe_items))
    return (
        "<section class='settings-section watch-tools'>"
        f"{search_html}{filters_html}</section>"
        "<section class='settings-section'><h2>Takip edilenler</h2>"
        f"{watches_html}</section>"
    )


def _new_watch_section(groups, known_titles=None):
    return (
        "<section class='settings-section'><form method='post' action='./settings/save' data-settings-save>"
        "<input type='hidden' name='operation' value='add_watch'><input type='hidden' name='watches_count' value='1'>"
        f"{_watch_form({}, 0, is_new=True, groups=groups, known_titles=known_titles)}"
        "</form>"
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
            _checkbox(
                "",
                "telegram_saved_messages_enabled",
                "Kayıtlı Mesajlar'dan hızlı takip ekleme aktif",
                options.get("telegram_saved_messages_enabled", True),
            ),
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
        "</section>"
    )


def _bool_from_form(form, key, default=False):
    return key in form if key in form else default


def _watch_form_context(index, name, urls):
    identity = name or (urls[0] if urls else "yeni kayıt")
    if len(identity) > 96:
        identity = f"{identity[:93]}..."
    return f"Takip {index + 1} ({identity})"


def _build_watch(form, index):
    prefix = f"watches_{index}_"
    name = _first(form, prefix + "name")
    group = _first(form, prefix + "group")
    target = _first(form, prefix + "target_price")
    minimum_price = _first(form, prefix + "minimum_price")
    size = _first(form, prefix + "size")
    exclude_terms = _first(form, prefix + "exclude_terms")
    interval = _first(form, prefix + "check_interval_minutes")
    urls = []
    for field_name in WATCH_URL_FIELDS:
        url = _first(form, prefix + field_name)
        if url and url not in urls:
            urls.append(url)
    if not group and any(detect_site_from_url(url) in {SITE_ZARA, SITE_HM} for url in urls):
        group = "Moda"
    if not any([name, target, size, *urls]):
        return None
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
        "target_price": _price_from_form(target),
        "include_variations": _bool_from_form(form, prefix + "include_variations"),
        "notify_once_in_24H": _bool_from_form(form, prefix + "notify_once_in_24H"),
        "active": _bool_from_form(form, prefix + "active"),
    }
    if minimum_price:
        item["minimum_price"] = _price_from_form(minimum_price)
    if exclude_terms:
        item["exclude_terms"] = exclude_terms
    if size:
        item["size"] = size
    for url_index, url in enumerate(urls, start=1):
        item[f"url_{url_index}"] = url
    if interval:
        item["check_interval_minutes"] = _number(interval)
    return item


def _build_watches(form):
    watches = []
    count = int(_first(form, "watches_count", "0") or 0)
    for index in range(count):
        item = _build_watch(form, index)
        if item:
            watches.append(item)
    return watches


def _posted_watch_index(form):
    """Find the one watch card present in a standalone update form."""
    posted_indices = {
        match.group(1)
        for field_name in form
        if (match := re.match(r"^watches_(\d+)_", str(field_name)))
    }
    return posted_indices.pop() if len(posted_indices) == 1 else ""


def _apply_settings_operation(existing_options, form):
    source_options = existing_options if isinstance(existing_options, dict) else {}
    existing_watches = [
        dict(item) for item in _as_list(source_options.get("takip_edilenler")) if isinstance(item, dict)
    ]
    options = _options_for_save(source_options)
    operation = _first(form, "operation", "update_existing")
    delete_index = _first(form, "delete_watch_index")
    update_index = _first(form, "update_watch_index", _first(form, "watch_index"))

    # A cached pre-fix settings script could mistakenly submit a new-watch
    # form as an update. It has no persisted watch_index, so preserve all
    # existing cards and treat it as the addition it was intended to be.
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
        if index < 0 or index >= len(existing_watches):
            raise ValueError("Silinecek takip kaydı bulunamadı.")
        removed = existing_watches.pop(index)
        removed_name = str(removed.get("name") or "").strip() or f"Takip {index + 1}"
        options["takip_edilenler"] = existing_watches
        return options, f"{removed_name} takip kaydı silindi."

    if update_index != "":
        try:
            index = int(update_index)
        except ValueError as exc:
            raise ValueError("Güncellenecek takip kaydı geçersiz.") from exc
        if index < 0 or index >= len(existing_watches):
            raise ValueError("Güncellenecek takip kaydı bulunamadı.")
        updated = _build_watch(form, index)
        if not updated:
            raise ValueError(f"Takip {index + 1}: hedef fiyat ve en az bir link alanı zorunlu.")
        existing_watches[index] = updated
        options["takip_edilenler"] = existing_watches
        return options, f"{_watch_display_name(updated, index, {})} takip kaydı güncellendi."

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

    if operation == "update_telegram":
        _update_telegram_options(options, form)
        return options, "Telegram ayarları güncellendi."

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
    options["telegram_saved_messages_enabled"] = _bool_from_form(
        form, "telegram_saved_messages_enabled", default=True
    )
    options["api_id"] = _first(form, "api_id")
    options["api_hash"] = _first(form, "api_hash")
    options["phone_number"] = _first(form, "phone_number")
    options["verification_code"] = _first(form, "verification_code")
    options["session_name"] = _first(form, "session_name", "telegram_keyword_alert") or "telegram_keyword_alert"
    options["channels"] = _list_from_form(form, "channels") or DEFAULT_TELEGRAM_CHANNELS
    options["keywords"] = _list_from_form(form, "keywords")
    options["exclude_keywords"] = _list_from_form(form, "exclude_keywords")


def _options_for_save(options):
    """Preserve every existing add-on option while supplying schema defaults."""
    saved = deepcopy(options) if isinstance(options, dict) else {}
    defaults = {
        "interval_seconds": 60,
        "request_delay_min_seconds": 3,
        "request_delay_max_seconds": 8,
        "pushover_user_key": "",
        "pushover_api_token": "",
        "public_dashboard_enabled": False,
        "public_dashboard_token": "",
        "telegram_enabled": False,
        "telegram_saved_messages_enabled": True,
        "api_id": "",
        "api_hash": "",
        "phone_number": "",
        "verification_code": "",
        "session_name": "telegram_keyword_alert",
        "channels": list(DEFAULT_TELEGRAM_CHANNELS),
        "keywords": [],
        "exclude_keywords": [],
        "gruplar": [],
        "takip_edilenler": [],
    }
    for key, value in defaults.items():
        saved.setdefault(key, deepcopy(value))
    return saved


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


def save_options_and_restart(options):
    """Persist shared add-on options and restart only after a successful save."""
    saved_options = _options_for_save(options)
    _save_options_to_supervisor(saved_options)
    save_json(OPTIONS_PATH, saved_options)
    _restart_addon_later()
    return saved_options


def render_settings_script():
    return SETTINGS_SCRIPT.encode("utf-8")


def render_settings_restart_script():
    return SETTINGS_RESTART_SCRIPT.encode("utf-8")


def render_settings_restart_page(message, settings_path="../settings", health_path="../health", return_path=None):
    destination = return_path or settings_path
    destination_label = "Ana ekrana dön" if return_path else "Ayarlar ekranına dön"
    destination_note = "ana ekran" if return_path else "ayarlar ekranı"
    html = f"""<!doctype html>
<html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Hermes yeniden başlatılıyor</title><style>{SETTINGS_CSS}</style></head>
<body><main><div class="hero"><h1>Hermes yeniden başlatılıyor</h1>
<p class="notice notice-ok">{escape(message)}</p>
<p>Değişiklikler Home Assistant yapılandırmasına kaydedildi. Hermes yeniden başlarken bu sayfa kısa süre bekleyecek; hazır olduğunda {destination_note} otomatik yenilenecek.</p>
<p class="footer-note" id="restart-status">Hazırlanıyor... Birkaç saniye içinde bağlantı kontrolü başlayacak.</p>
<div class="actions"><a class="button secondary" href="{escape(destination, quote=True)}">{destination_label}</a></div>
</div></main><script id="hermes-restart-script" src="./restart.js" defer data-settings-path="{escape(settings_path, quote=True)}" data-return-path="{escape(destination, quote=True)}" data-health-path="{escape(health_path, quote=True)}"></script></body></html>"""
    return html.encode("utf-8")


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
    html = f"""<!doctype html>
<html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Hermes Ayarlar</title><style>{SETTINGS_CSS}</style></head>
<body><main><div class="hero"><h1>Hermes Ayarlar</h1><div class="actions"><a class="button secondary" href="./">Ana ekran</a></div>{notice}
{_new_watch_section(groups, known_titles)}
{_watch_section(options.get("takip_edilenler"), groups, known_titles)}
<form method="post" action="./settings/save" data-settings-save>{_telegram_section(options)}
<input type="hidden" name="operation" value="update_telegram">
<div class="actions"><button class="button primary" type="submit">Telegram Ayarlarını Güncelle</button></div></form>
</div></main><div id="saving-overlay" class="saving-overlay" hidden><div class="saving-dialog"><div class="saving-spinner"></div><h2 id="saving-title">Ayarlar kaydediliyor</h2><p id="saving-message">Hermes değişiklikleri Home Assistant'a yazıyor. Ardından kısa bir yeniden başlatma yapılacak; hazır olduğunda ayarlara otomatik dönülecek.</p></div></div><script src="./settings.js" defer></script></body></html>"""
    return html.encode("utf-8")


def handle_settings_save(body):
    try:
        form = urllib.parse.parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True)
        options = load_json(OPTIONS_PATH, {})
        if not isinstance(options, dict):
            options = {}
        options, change_message = _apply_settings_operation(options, form)
        save_options_and_restart(options)
        log("Ayarlar Home Assistant config'e kaydedildi; Hermes yeniden başlatılacak.")
        return True, f"{change_message} Hermes yeniden başlatılıyor; 10-20 saniye sonra sayfayı yenileyebilirsin."
    except Exception as exc:  # noqa: BLE001
        return False, f"Ayarlar kaydedilemedi: {exc}"
