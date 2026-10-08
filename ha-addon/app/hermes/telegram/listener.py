"""Telegram: keyword relay from deal channels and quick-add from Saved Messages."""

import asyncio
import re
import threading
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, Optional
from urllib.parse import parse_qs, urlparse

import requests

from ..config import read_options, watch_urls
from ..constants import (
    DEFAULT_PRIORITY,
    TELEGRAM_ERROR_EVENTS_PATH,
    TELEGRAM_LOGIN_STATE_PATH,
    TELEGRAM_QUICK_ADD_GROUP,
    TELEGRAM_QUICK_ADD_PATH,
    TELEGRAM_SEEN_MESSAGES_PATH,
    TELEGRAM_SESSION_PATH,
    TELEGRAM_STATUS_HEARTBEAT_SECONDS,
    TELEGRAM_STATUS_PATH,
)
from ..logging_utils import log
from ..models import HermesConfig, TelegramConfig
from ..notifier import Pushover
from ..storage import file_lock, load_json, save_json
from ..supervisor import save_options_and_restart
from ..utils import (
    build_headers,
    detect_site_from_url,
    format_local_datetime,
    format_tl,
    local_now,
    normalize_offer_text,
    parse_decimal,
    watch_name_required_for_url,
)

try:
    from telethon import TelegramClient, events
    from telethon.errors import SessionPasswordNeededError
except Exception:  # pragma: no cover - handled at runtime inside the add-on
    TelegramClient = None
    events = None
    SessionPasswordNeededError = Exception

MAX_SEEN_MESSAGES = 5000
QUICK_ADD_EXPIRY_HOURS = 24
RECONNECT_DELAY_SECONDS = 60
URL_PATTERN = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)


class WaitingForUser(Exception):
    """Login needs a code or a password from the user; retrying cannot help."""


def _now_text() -> str:
    return format_local_datetime(local_now())


# -- status shown on the dashboard -------------------------------------------------


def _status_defaults() -> Dict[str, Any]:
    return {
        "telegram_enabled": False,
        "telegram_state": "Pasif",
        "telegram_channels": 0,
        "telegram_keywords": 0,
        "notifications_sent": 0,
        "last_check": "-",
        "last_notification": "-",
        "last_error": "",
    }


def _load_status() -> Dict[str, Any]:
    status = load_json(TELEGRAM_STATUS_PATH, {})
    defaults = _status_defaults()
    defaults.update(status if isinstance(status, dict) else {})
    return defaults


def _update_status(**updates: Any) -> Dict[str, Any]:
    with file_lock(TELEGRAM_STATUS_PATH):
        status = _load_status()
        status.update(updates)
        save_json(TELEGRAM_STATUS_PATH, status)
        return status


def _record_recent_notification(channel_name: str, keyword: str, url: str, text: str) -> None:
    with file_lock(TELEGRAM_STATUS_PATH):
        status = _load_status()
        recent = status.get("recent_notifications") if isinstance(status.get("recent_notifications"), list) else []
        recent.insert(0, {
            "created_at": _now_text(), "channel": channel_name, "keyword": keyword, "url": url,
            "message": _message_preview(text, 180),
        })
        try:
            status["notifications_sent"] = int(status.get("notifications_sent", 0) or 0) + 1
        except (TypeError, ValueError):
            status["notifications_sent"] = 1
        status.update(recent_notifications=recent[:5], last_notification=_now_text(), telegram_state="Dinleniyor")
        save_json(TELEGRAM_STATUS_PATH, status)


def _parse_created_at(item: Dict[str, Any]) -> Optional[datetime]:
    try:
        created_at = datetime.fromisoformat(str(item.get("created_at") or ""))
    except ValueError:
        return None
    return created_at if created_at.tzinfo else created_at.astimezone()


def _prune_error_events(events_payload: Iterable[Dict[str, Any]]) -> list:
    cutoff = local_now() - timedelta(hours=24)
    return [item for item in events_payload
            if isinstance(item, dict) and (created := _parse_created_at(item)) and created.astimezone() >= cutoff]


def record_telegram_error(message: Any, context: str = "Telegram") -> None:
    with file_lock(TELEGRAM_ERROR_EVENTS_PATH):
        payload = load_json(TELEGRAM_ERROR_EVENTS_PATH, [])
        events_payload = _prune_error_events(payload if isinstance(payload, list) else [])
        events_payload.append({"created_at": local_now().isoformat(), "context": context, "message": str(message)})
        save_json(TELEGRAM_ERROR_EVENTS_PATH, events_payload)
    _update_status(last_error=str(message), telegram_state="Hata")
    log(f"Telegram hata: {context} | {message}")


def prune_error_events() -> int:
    with file_lock(TELEGRAM_ERROR_EVENTS_PATH):
        payload = load_json(TELEGRAM_ERROR_EVENTS_PATH, [])
        events_payload = _prune_error_events(payload if isinstance(payload, list) else [])
        save_json(TELEGRAM_ERROR_EVENTS_PATH, events_payload)
    return len(events_payload)


# -- message deduplication ---------------------------------------------------------


def _message_key(event) -> str:
    return f"{event.chat_id}:{event.id}"


def _first_time_seen(key: str) -> bool:
    """Remember a message; False when it was already handled before a restart."""
    with file_lock(TELEGRAM_SEEN_MESSAGES_PATH):
        payload = load_json(TELEGRAM_SEEN_MESSAGES_PATH, {})
        payload = payload if isinstance(payload, dict) else {}
        if key in payload:
            return False
        payload[key] = _now_text()
        if len(payload) > MAX_SEEN_MESSAGES:
            payload = dict(list(payload.items())[-MAX_SEEN_MESSAGES:])
        save_json(TELEGRAM_SEEN_MESSAGES_PATH, payload)
        return True


# -- Saved Messages quick add --------------------------------------------------------


def _resolve_shared_url(url: str) -> str:
    """Follow a mobile share link once and accept only a supported final site."""
    parsed_url = urlparse(url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        return ""
    try:
        response = requests.get(url, headers=build_headers(url), timeout=15, allow_redirects=True, stream=True)
        resolved_url = str(response.url or "")
        response.close()
        detect_site_from_url(resolved_url)
        return resolved_url
    except Exception as exc:  # noqa: BLE001
        log(f"Telegram kısa bağlantısı açılamadı: {exc}")
        return ""


def _extract_supported_url(text: str) -> str:
    """Return the first Hermes-supported product or search URL from a message."""
    for match in URL_PATTERN.finditer(str(text or "")):
        candidate = match.group(0).rstrip(".,;:!?)]}>'\"")
        try:
            detect_site_from_url(candidate)
            return candidate
        except Exception:  # noqa: BLE001
            resolved_url = _resolve_shared_url(candidate)
            if resolved_url:
                return resolved_url
    return ""


def _load_quick_adds() -> Dict[str, Any]:
    payload = load_json(TELEGRAM_QUICK_ADD_PATH, {"pending": []})
    if not isinstance(payload, dict):
        return {"pending": []}
    payload["pending"] = payload.get("pending") if isinstance(payload.get("pending"), list) else []
    return payload


def _prune_pending_quick_adds(items: Iterable[Dict[str, Any]]) -> list:
    cutoff = local_now() - timedelta(hours=QUICK_ADD_EXPIRY_HOURS)
    return [item for item in items
            if isinstance(item, dict) and (created := _parse_created_at(item)) and created.astimezone() >= cutoff]


def _reply_to_message_id(event) -> Optional[int]:
    message = getattr(event, "message", None)
    direct_id = getattr(message, "reply_to_msg_id", None)
    if direct_id:
        return int(direct_id)
    nested_id = getattr(getattr(message, "reply_to", None), "reply_to_msg_id", None)
    return int(nested_id) if nested_id else None


def _pending_for_reply(event, pending_items: Iterable[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    reply_id = _reply_to_message_id(event)
    if reply_id is None:
        return None
    chat_id = str(getattr(event, "chat_id", ""))
    for item in pending_items:
        if str(item.get("chat_id", "")) == chat_id and reply_id in {item.get("source_message_id"), item.get("prompt_message_id")}:
            return item
    return None


def _parse_target_price(text: str):
    value = str(text or "").strip()
    if not value:
        return None
    try:
        price = parse_decimal(value)
    except Exception:  # noqa: BLE001
        return None
    return price if price > 0 else None


def _quick_add_search_name(url: str) -> str:
    """Infer the required search keyword so Saved Messages needs one reply only."""
    query = parse_qs(urlparse(str(url or "")).query)
    for key in ("k", "q", "query", "search"):
        values = query.get(key) or []
        value = re.sub(r"\s+", " ", str(values[0] or "")).strip() if values else ""
        if value:
            return value[:160]
    return ""


def _ensure_quick_add_group(options: Dict[str, Any]) -> None:
    groups = options.get("gruplar")
    groups = [str(group).strip() for group in groups if str(group).strip()] if isinstance(groups, list) else []
    if TELEGRAM_QUICK_ADD_GROUP.casefold() not in {group.casefold() for group in groups}:
        groups.append(TELEGRAM_QUICK_ADD_GROUP)
    options["gruplar"] = groups


QUICK_ADD_DONE = "Takip kaydı eklendi"


def _quick_add_watch(url: str, target_price, name: str = "") -> str:
    """Append a Saved Messages watch through the same options path as the UI."""
    options = read_options()
    watches = [dict(item) for item in options.get("takip_edilenler", []) if isinstance(item, dict)] \
        if isinstance(options.get("takip_edilenler"), list) else []
    normalized_url = str(url).strip()
    if any(normalized_url in watch_urls(watch) for watch in watches):
        return "Bu bağlantı zaten takip ediliyor. Yeni kayıt oluşturulmadı."
    normalized_name = str(name or "").strip()
    if not normalized_name and watch_name_required_for_url(normalized_url):
        normalized_name = _quick_add_search_name(normalized_url)
        if not normalized_name:
            return "Arama bağlantısındaki ürün adı çözümlenemedi. Bağlantıyı Hermes Ayarlar ekranından ekle."
    options["takip_edilenler"] = watches + [{
        "name": normalized_name,
        "group": TELEGRAM_QUICK_ADD_GROUP,
        "target_price": float(target_price),
        "url_1": normalized_url,
        "notify_once_in_24H": True,
        "active": True,
        "priority": DEFAULT_PRIORITY,
    }]
    _ensure_quick_add_group(options)
    save_options_and_restart(options)
    return QUICK_ADD_DONE


async def _handle_saved_message_quick_add(event) -> bool:
    """Run the short Saved Messages conversation for adding a Hermes watch."""
    payload = _load_quick_adds()
    pending_items = _prune_pending_quick_adds(payload["pending"])
    pending = _pending_for_reply(event, pending_items)
    text = event.raw_text or ""

    if pending and pending.get("stage") == "target_price":
        target_price = _parse_target_price(text)
        if target_price is None:
            await event.reply("Hermes: hedef fiyatı örneğin `40000` veya `40.000 TL` biçiminde yanıtla.")
            payload["pending"] = pending_items
            save_json(TELEGRAM_QUICK_ADD_PATH, payload)
            return True
        result = _quick_add_watch(str(pending["url"]), target_price)
        pending_items.remove(pending)
        payload["pending"] = pending_items
        save_json(TELEGRAM_QUICK_ADD_PATH, payload)
        if result == QUICK_ADD_DONE:
            await event.reply(
                f"Hermes: {result}. Hedef fiyat: {format_tl(target_price, with_currency=True)}. "
                f"Kayıt `{TELEGRAM_QUICK_ADD_GROUP}` grubuna eklendi; beden ve diğer ayarları Hermes Ayarlar ekranından düzenleyebilirsin."
            )
            log(f"Telegram Kayıtlı Mesajlar ile takip eklendi: {pending['url']}")
        else:
            await event.reply(f"Hermes: {result}")
        return True

    url = _extract_supported_url(text)
    if not url:
        payload["pending"] = pending_items
        save_json(TELEGRAM_QUICK_ADD_PATH, payload)
        return False
    prompt = await event.reply("Hermes: bu bağlantı için hedef fiyat nedir? Örnek: `40000` veya `40.000 TL`.")
    pending_items.append({
        "chat_id": str(getattr(event, "chat_id", "")),
        "source_message_id": getattr(event, "id", None),
        "prompt_message_id": getattr(prompt, "id", None),
        "url": url,
        "stage": "target_price",
        "created_at": local_now().isoformat(),
    })
    payload["pending"] = pending_items
    save_json(TELEGRAM_QUICK_ADD_PATH, payload)
    log(f"Telegram Kayıtlı Mesajlar bağlantısı algılandı: {url}")
    return True


# -- channel keyword relay -----------------------------------------------------------


def _matching_keyword(message_text: str, keywords: Iterable[str]) -> Optional[str]:
    normalized_message = normalize_offer_text(message_text)
    return next((keyword for keyword in keywords if normalize_offer_text(keyword) in normalized_message), None)


def _has_exclude_keyword(message_text: str, exclude_keywords: Iterable[str]) -> bool:
    normalized_message = normalize_offer_text(message_text)
    return any(normalize_offer_text(keyword) in normalized_message for keyword in exclude_keywords)


def _telegram_message_link(event) -> str:
    username = getattr(getattr(event, "chat", None), "username", None)
    return f"https://t.me/{username}/{event.id}" if username else ""


def _message_preview(text: str, max_length: int = 1024) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()[:max_length]


def _channel_label(event) -> str:
    chat = getattr(event, "chat", None)
    return getattr(chat, "title", None) or getattr(chat, "username", None) or str(getattr(event, "chat_id", "Bilinmeyen kanal"))


def _send_keyword_notification(notifier: Pushover, event, channel_name: str, keyword: str, text: str) -> None:
    message_url = _telegram_message_link(event)
    notifier.send("Telegram keyword alarmı", _message_preview(text), message_url, url_title="Telegram'da aç")
    _record_recent_notification(channel_name, keyword, message_url, text)
    log(f"Telegram bildirimi gönderildi: kanal={channel_name} | keyword={keyword}")


# -- connection ---------------------------------------------------------------------


async def _ensure_login(client, telegram_config: TelegramConfig) -> None:
    await client.connect()
    if await client.is_user_authorized():
        _update_status(telegram_state="Dinleniyor")
        return
    login_state = load_json(TELEGRAM_LOGIN_STATE_PATH, {})
    login_state = login_state if isinstance(login_state, dict) else {}
    if not telegram_config.verification_code:
        sent = await client.send_code_request(telegram_config.phone_number)
        save_json(TELEGRAM_LOGIN_STATE_PATH, {
            "phone_number": telegram_config.phone_number,
            "phone_code_hash": sent.phone_code_hash,
            "sent_at": local_now().isoformat(),
        })
        _update_status(telegram_state="Kod bekleniyor")
        log("Telegram giriş kodu gönderildi. Ayarlardaki doğrulama kodu alanına gelen kodu yazıp kaydet.")
        raise WaitingForUser()
    phone_code_hash = str(login_state.get("phone_code_hash") or "").strip()
    if not phone_code_hash:
        _update_status(telegram_state="Kod bekleniyor")
        log("Telegram phone_code_hash bulunamadı. Doğrulama kodu alanını boşaltıp kaydet; yeni kod gönderilecek.")
        raise WaitingForUser()
    try:
        await client.sign_in(phone=telegram_config.phone_number, code=telegram_config.verification_code,
                             phone_code_hash=phone_code_hash)
    except SessionPasswordNeededError:
        record_telegram_error("Telegram hesabında 2FA şifresi gerekiyor. Hermes şu an 2FA password girişi desteklemiyor.")
        raise WaitingForUser()
    save_json(TELEGRAM_LOGIN_STATE_PATH, {})
    _update_status(telegram_state="Dinleniyor")
    log("Telegram giriş başarılı. Session kalıcı olarak kaydedildi.")


async def _resolve_channels(client, channels: Iterable[str]) -> list:
    resolved = []
    for channel in channels:
        try:
            resolved.append(await client.get_entity(channel))
        except Exception as exc:  # noqa: BLE001
            record_telegram_error(f"Kanal erişilemedi: {channel} | {exc}", "Telegram kanal")
    return resolved


async def _heartbeat() -> None:
    while True:
        await asyncio.sleep(TELEGRAM_STATUS_HEARTBEAT_SECONDS)
        _update_status(last_check=_now_text(), telegram_state="Dinleniyor")
        prune_error_events()
        log("Telegram kanal dinleme devam ediyor.")


async def _listen(config: HermesConfig, notifier: Pushover) -> None:
    """Listen until Telegram disconnects; the caller reconnects."""
    telegram_config = config.telegram
    _update_status(telegram_enabled=True, telegram_state="Bağlanıyor", telegram_channels=len(telegram_config.channels),
                   telegram_keywords=len(telegram_config.keywords), last_check=_now_text())
    log("Telegram bağlanıyor.")
    client = TelegramClient(str(TELEGRAM_SESSION_PATH), telegram_config.api_id, telegram_config.api_hash)
    try:
        await _ensure_login(client, telegram_config)
        resolved_channels = await _resolve_channels(client, telegram_config.channels)
        saved_messages_chat = await client.get_me() if telegram_config.saved_messages_enabled else None
        listened_chats = list(resolved_channels) + ([saved_messages_chat] if saved_messages_chat is not None else [])
        if not listened_chats:
            record_telegram_error("Dinlenebilir Telegram kanalı bulunamadı.")
            raise WaitingForUser()

        async def handle_message(event) -> None:
            text = event.raw_text or ""
            _update_status(last_check=_now_text(), telegram_state="Dinleniyor")
            if not _first_time_seen(_message_key(event)):
                return
            if saved_messages_chat is not None and str(getattr(event, "chat_id", "")) == str(getattr(saved_messages_chat, "id", "")):
                # Only messages the user writes in Saved Messages start or answer a flow.
                if getattr(event, "out", False):
                    try:
                        await _handle_saved_message_quick_add(event)
                    except Exception as exc:  # noqa: BLE001
                        record_telegram_error(f"Kayıtlı Mesajlar ile takip eklenemedi: {exc}", "Telegram hızlı ekleme")
                return
            keyword = _matching_keyword(text, telegram_config.keywords)
            if not keyword:
                return
            if _has_exclude_keyword(text, telegram_config.exclude_keywords):
                log(f"Telegram mesajı exclude keyword nedeniyle atlandı: kanal={_channel_label(event)} | keyword={keyword}")
                return
            try:
                _send_keyword_notification(notifier, event, _channel_label(event), keyword, text)
            except Exception as exc:  # noqa: BLE001
                record_telegram_error(f"Pushover bildirimi gönderilemedi: {exc}", "Telegram bildirim")

        client.add_event_handler(handle_message, events.NewMessage(chats=listened_chats))
        _update_status(telegram_state="Dinleniyor", last_error="")
        saved_note = " | Kayıtlı Mesajlar hızlı ekleme aktif" if saved_messages_chat else ""
        log(f"Telegram kanal dinleme aktif: kanal={len(resolved_channels)} | keyword={len(telegram_config.keywords)}{saved_note}")
        heartbeat = asyncio.ensure_future(_heartbeat())
        try:
            await client.run_until_disconnected()
        finally:
            heartbeat.cancel()
        raise ConnectionError("Telegram bağlantısı kapandı.")
    finally:
        await client.disconnect()


def run_telegram_listener(config: HermesConfig, stop: Optional[threading.Event] = None) -> None:
    stop = stop or threading.Event()
    if not config.telegram.enabled:
        _update_status(telegram_enabled=False, telegram_state="Pasif", telegram_channels=0, telegram_keywords=0,
                       last_check=_now_text())
        log("Telegram dinleme pasif.")
        return
    if TelegramClient is None or events is None:
        record_telegram_error("Telethon paketi bulunamadı. Add-on imajı yeniden kurulmalı.")
        return
    notifier = Pushover(config.pushover_user_key, config.pushover_api_token, config.request_timeout_seconds)
    while not stop.is_set():
        try:
            asyncio.run(_listen(config, notifier))
        except WaitingForUser:
            log("Telegram kullanıcı işlemi bekliyor; ayarlar kaydedilince yeniden denenecek.")
            return
        except Exception as exc:  # noqa: BLE001
            record_telegram_error(f"{exc} | {RECONNECT_DELAY_SECONDS} sn sonra yeniden bağlanılacak.")
        stop.wait(RECONNECT_DELAY_SECONDS)


def start_telegram_listener(config: HermesConfig, stop: Optional[threading.Event] = None) -> Optional[threading.Thread]:
    if not config.telegram.enabled:
        run_telegram_listener(config, stop)
        return None
    thread = threading.Thread(target=run_telegram_listener, args=(config, stop), name="telegram-listener", daemon=True)
    thread.start()
    return thread
