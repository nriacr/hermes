import asyncio
import re
import threading
import time
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, Optional

import requests

from .constants import (
    TELEGRAM_ERROR_EVENTS_PATH,
    TELEGRAM_LOGIN_STATE_PATH,
    TELEGRAM_SEEN_DEALS_PATH,
    TELEGRAM_SEEN_MESSAGES_PATH,
    TELEGRAM_SESSION_PATH,
    TELEGRAM_STATUS_HEARTBEAT_SECONDS,
    TELEGRAM_STATUS_PATH,
)
from .logging_utils import log
from .models import HermesConfig, TelegramConfig
from .notifier import send_pushover
from .storage import load_json, save_json
from .utils import format_local_datetime, local_now, normalize_offer_text

try:
    from telethon import TelegramClient, events
    from telethon.errors import SessionPasswordNeededError
except Exception:  # pragma: no cover - handled at runtime inside the add-on
    TelegramClient = None
    events = None
    SessionPasswordNeededError = Exception


PRICE_PATTERNS = (
    re.compile(r"₺\s*(?P<price>\d{1,3}(?:\.\d{3})*(?:,\d{1,2})?|\d+(?:,\d{1,2})?)", re.IGNORECASE),
    re.compile(r"(?P<price>\d{1,3}(?:\.\d{3})*(?:,\d{1,2})?|\d+(?:,\d{1,2})?)\s*(?:TL|TRY|₺)", re.IGNORECASE),
)
MAX_SEEN_MESSAGES = 5000


def normalize_text(value: str) -> str:
    return normalize_offer_text(value)


def _today() -> str:
    return local_now().date().isoformat()


def _now_text() -> str:
    return format_local_datetime(local_now())


def _status_defaults() -> Dict[str, Any]:
    return {
        "telegram_enabled": False,
        "telegram_state": "Pasif",
        "telegram_channels": 0,
        "telegram_keywords": 0,
        "notifications_sent": 0,
        "duplicates_suppressed": 0,
        "last_check": "-",
        "last_notification": "-",
        "last_error": "",
    }


def _load_status() -> Dict[str, Any]:
    status = load_json(TELEGRAM_STATUS_PATH, {})
    if not isinstance(status, dict):
        status = {}
    defaults = _status_defaults()
    defaults.update(status)
    return defaults


def _save_status(**updates: Any) -> None:
    status = _load_status()
    status.update(updates)
    save_json(TELEGRAM_STATUS_PATH, status)


def _increment_status(field_name: str) -> None:
    status = _load_status()
    try:
        status[field_name] = int(status.get(field_name, 0) or 0) + 1
    except (TypeError, ValueError):
        status[field_name] = 1
    save_json(TELEGRAM_STATUS_PATH, status)


def _load_error_events() -> list:
    events_payload = load_json(TELEGRAM_ERROR_EVENTS_PATH, [])
    return events_payload if isinstance(events_payload, list) else []


def _prune_error_events(events_payload: Iterable[Dict[str, Any]]) -> list:
    cutoff = local_now() - timedelta(hours=24)
    pruned = []
    for item in events_payload:
        if not isinstance(item, dict):
            continue
        try:
            created_at = datetime.fromisoformat(str(item.get("created_at")))
            if created_at.tzinfo is None:
                created_at = created_at.astimezone()
        except ValueError:
            continue
        if created_at.astimezone() >= cutoff:
            pruned.append(item)
    return pruned


def record_telegram_error(message: str, context: str = "Telegram") -> None:
    events_payload = _prune_error_events(_load_error_events())
    event = {
        "created_at": local_now().isoformat(),
        "context": context,
        "message": str(message),
    }
    events_payload.append(event)
    save_json(TELEGRAM_ERROR_EVENTS_PATH, events_payload)
    _save_status(last_error=str(message), telegram_state="Hata")
    log(f"Telegram hata: {context} | {message}")


def telegram_error_count_24h() -> int:
    events_payload = _prune_error_events(_load_error_events())
    save_json(TELEGRAM_ERROR_EVENTS_PATH, events_payload)
    return len(events_payload)


def _message_key(event) -> str:
    return f"{event.chat_id}:{event.id}"


def _load_seen_messages() -> Dict[str, Any]:
    payload = load_json(TELEGRAM_SEEN_MESSAGES_PATH, {})
    return payload if isinstance(payload, dict) else {}


def _mark_message_seen(key: str) -> None:
    payload = _load_seen_messages()
    payload[key] = _now_text()
    if len(payload) > MAX_SEEN_MESSAGES:
        payload = dict(list(payload.items())[-MAX_SEEN_MESSAGES:])
    save_json(TELEGRAM_SEEN_MESSAGES_PATH, payload)


def _load_seen_deals() -> Dict[str, Any]:
    payload = load_json(TELEGRAM_SEEN_DEALS_PATH, {})
    return payload if isinstance(payload, dict) else {}


def _prune_seen_deals(payload: Dict[str, Any]) -> Dict[str, Any]:
    today = _today()
    return {key: value for key, value in payload.items() if value == today}


def _normalize_price(raw_price: str) -> Optional[str]:
    cleaned = str(raw_price or "").strip()
    cleaned = re.sub(r"[^\d,\.]", "", cleaned)
    if not cleaned:
        return None
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif "," in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    try:
        return f"{Decimal(cleaned):.2f}"
    except InvalidOperation:
        return None


def extract_price(text: str) -> Optional[str]:
    for pattern in PRICE_PATTERNS:
        match = pattern.search(text or "")
        if match:
            return _normalize_price(match.group("price"))
    return None


def _matching_keyword(message_text: str, keywords: Iterable[str]) -> Optional[str]:
    normalized_message = normalize_text(message_text)
    for keyword in keywords:
        if normalize_text(keyword) in normalized_message:
            return keyword
    return None


def _has_exclude_keyword(message_text: str, exclude_keywords: Iterable[str]) -> bool:
    normalized_message = normalize_text(message_text)
    return any(normalize_text(keyword) in normalized_message for keyword in exclude_keywords)


def _deal_key(keyword: str, price: str) -> str:
    return f"{normalize_text(keyword)}|{price}"


def _notify_once_enabled(keyword: str, notify_once_keywords: Iterable[str]) -> bool:
    normalized_keyword = normalize_text(keyword)
    return any(normalize_text(item) == normalized_keyword for item in notify_once_keywords)


def _duplicate_deal(keyword: str, price: Optional[str], notify_once_keywords: Iterable[str]) -> bool:
    if not price:
        return False
    if not _notify_once_enabled(keyword, notify_once_keywords):
        return False
    payload = _prune_seen_deals(_load_seen_deals())
    key = _deal_key(keyword, price)
    if payload.get(key) == _today():
        save_json(TELEGRAM_SEEN_DEALS_PATH, payload)
        _increment_status("duplicates_suppressed")
        log(f"Telegram tekrar susturuldu: keyword={keyword} | fiyat={price}")
        return True
    payload[key] = _today()
    save_json(TELEGRAM_SEEN_DEALS_PATH, payload)
    return False


def _telegram_message_link(event) -> str:
    chat = getattr(event, "chat", None)
    username = getattr(chat, "username", None)
    if username:
        return f"https://t.me/{username}/{event.id}"
    return ""


def _message_preview(text: str, max_length: int = 1024) -> str:
    clean = re.sub(r"\s+", " ", str(text or "")).strip()
    return clean[:max_length]


def _send_keyword_notification(
    config: HermesConfig,
    event,
    channel_name: str,
    keyword: str,
    price: Optional[str],
    text: str,
) -> None:
    session = requests.Session()
    price_line = f"\nFiyat: {price} TL" if price else ""
    message = (
        f"Kanal: {channel_name}\n"
        f"Keyword: {keyword}{price_line}\n\n"
        f"{_message_preview(text)}"
    )
    send_pushover(
        session,
        config.pushover_user_key,
        config.pushover_api_token,
        "Telegram keyword alarmı",
        message,
        _telegram_message_link(event),
        config.request_timeout_seconds,
        url_title="Telegram'da aç",
    )
    _increment_status("notifications_sent")
    _save_status(last_notification=_now_text(), telegram_state="Dinleniyor")
    log(f"Telegram bildirimi gönderildi: kanal={channel_name} | keyword={keyword} | fiyat={price or '-'}")


async def _wait_for_code() -> None:
    while True:
        await asyncio.sleep(TELEGRAM_STATUS_HEARTBEAT_SECONDS)
        log("Kanal dinleme devam ediyor.")


async def _ensure_login(client, telegram_config: TelegramConfig) -> bool:
    await client.connect()
    if await client.is_user_authorized():
        _save_status(telegram_state="Dinleniyor")
        return True

    login_state = load_json(TELEGRAM_LOGIN_STATE_PATH, {})
    if not isinstance(login_state, dict):
        login_state = {}

    if not telegram_config.verification_code:
        sent = await client.send_code_request(telegram_config.phone_number)
        save_json(
            TELEGRAM_LOGIN_STATE_PATH,
            {
                "phone_number": telegram_config.phone_number,
                "phone_code_hash": sent.phone_code_hash,
                "sent_at": local_now().isoformat(),
            },
        )
        _save_status(telegram_state="Kod bekleniyor")
        log("Telegram giriş kodu gönderildi. Lütfen verification_code alanına gelen kodu yazıp Hermes'i yeniden başlat.")
        await _wait_for_code()
        return False

    phone_code_hash = str(login_state.get("phone_code_hash") or "").strip()
    if not phone_code_hash:
        _save_status(telegram_state="Kod bekleniyor")
        log("Telegram phone_code_hash bulunamadı. verification_code alanını boşaltıp Hermes'i yeniden başlat; yeni kod gönderilecek.")
        await _wait_for_code()
        return False

    try:
        await client.sign_in(
            phone=telegram_config.phone_number,
            code=telegram_config.verification_code,
            phone_code_hash=phone_code_hash,
        )
    except SessionPasswordNeededError:
        record_telegram_error("Telegram hesabında 2FA şifresi gerekiyor. Hermes şu an 2FA password girişi desteklemiyor.")
        await _wait_for_code()
        return False

    save_json(TELEGRAM_LOGIN_STATE_PATH, {})
    _save_status(telegram_state="Dinleniyor")
    log("Telegram giriş başarılı. Session kalıcı olarak kaydedildi.")
    return True


async def _resolve_channels(client, channels: Iterable[str]) -> list:
    resolved = []
    for channel in channels:
        try:
            resolved.append(await client.get_entity(channel))
        except Exception as exc:  # noqa: BLE001
            record_telegram_error(f"Kanal erişilemedi: {channel} | {exc}", "Telegram kanal")
    return resolved


def _channel_label(event) -> str:
    chat = getattr(event, "chat", None)
    return (
        getattr(chat, "title", None)
        or getattr(chat, "username", None)
        or str(getattr(event, "chat_id", "Bilinmeyen kanal"))
    )


async def _run_telegram_listener(config: HermesConfig) -> None:
    telegram_config = config.telegram
    if TelegramClient is None or events is None:
        record_telegram_error("Telethon paketi bulunamadı. Add-on imajı yeniden build edilmeli.")
        return

    _save_status(
        telegram_enabled=True,
        telegram_state="Bağlanıyor",
        telegram_channels=len(telegram_config.channels),
        telegram_keywords=len(telegram_config.keywords),
        last_check=_now_text(),
    )
    log("Telegram bağlanıyor.")

    client = TelegramClient(str(TELEGRAM_SESSION_PATH), telegram_config.api_id, telegram_config.api_hash)
    if not await _ensure_login(client, telegram_config):
        return

    resolved_channels = await _resolve_channels(client, telegram_config.channels)
    if not resolved_channels:
        record_telegram_error("Dinlenebilir Telegram kanalı bulunamadı.")
        await _wait_for_code()
        return

    async def handle_message(event) -> None:
        text = event.raw_text or ""
        _save_status(last_check=_now_text(), telegram_state="Dinleniyor")
        key = _message_key(event)
        seen_messages = _load_seen_messages()
        if key in seen_messages:
            return
        _mark_message_seen(key)

        keyword = _matching_keyword(text, telegram_config.keywords)
        if not keyword:
            return
        if _has_exclude_keyword(text, telegram_config.exclude_keywords):
            log(f"Telegram mesajı exclude keyword nedeniyle atlandı: kanal={_channel_label(event)} | keyword={keyword}")
            return

        price = extract_price(text)
        if _duplicate_deal(keyword, price, telegram_config.notify_once_keywords):
            return

        try:
            _send_keyword_notification(config, event, _channel_label(event), keyword, price, text)
        except Exception as exc:  # noqa: BLE001
            record_telegram_error(f"Pushover bildirimi gönderilemedi: {exc}", "Telegram bildirim")

    client.add_event_handler(handle_message, events.NewMessage(chats=resolved_channels))
    _save_status(telegram_state="Dinleniyor", last_error="")
    log(f"Telegram kanal dinleme aktif: kanal={len(resolved_channels)} | keyword={len(telegram_config.keywords)}")

    while True:
        try:
            await asyncio.sleep(TELEGRAM_STATUS_HEARTBEAT_SECONDS)
            _save_status(last_check=_now_text(), telegram_state="Dinleniyor")
            telegram_error_count_24h()
            log("Kanal dinleme devam ediyor.")
        except asyncio.CancelledError:
            raise


def run_telegram_listener(config: HermesConfig) -> None:
    if not config.telegram.enabled:
        _save_status(
            telegram_enabled=False,
            telegram_state="Pasif",
            telegram_channels=0,
            telegram_keywords=0,
            last_check=_now_text(),
        )
        log("Telegram dinleme pasif.")
        return
    while True:
        try:
            asyncio.run(_run_telegram_listener(config))
            return
        except Exception as exc:  # noqa: BLE001
            record_telegram_error(exc)
            time.sleep(60)


def start_telegram_listener(config: HermesConfig) -> Optional[threading.Thread]:
    if not config.telegram.enabled:
        run_telegram_listener(config)
        return None
    thread = threading.Thread(target=run_telegram_listener, args=(config,), name="telegram-listener", daemon=True)
    thread.start()
    return thread
