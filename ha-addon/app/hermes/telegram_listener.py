import asyncio
import re
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, Optional
from urllib.parse import urlparse

import requests

from .constants import (
    TELEGRAM_ERROR_EVENTS_PATH,
    TELEGRAM_LOGIN_STATE_PATH,
    OPTIONS_PATH,
    TELEGRAM_QUICK_ADD_PATH,
    TELEGRAM_SEEN_MESSAGES_PATH,
    TELEGRAM_SESSION_PATH,
    TELEGRAM_STATUS_HEARTBEAT_SECONDS,
    TELEGRAM_STATUS_PATH,
)
from .logging_utils import log
from .models import HermesConfig, TelegramConfig
from .notifier import send_pushover
from .settings_ui import save_options_and_restart
from .storage import load_json, save_json
from .utils import (
    detect_site_from_url,
    build_headers,
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
URL_PATTERN = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)


def normalize_text(value: str) -> str:
    return normalize_offer_text(value)


def _now_text() -> str:
    return format_local_datetime(local_now())


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


def _extract_supported_url(text: str) -> str:
    """Return the first Hermes-supported product or search URL from a message."""
    for match in URL_PATTERN.finditer(str(text or "")):
        candidate = match.group(0).rstrip(".,;:!?)]}>'\"")
        try:
            detect_site_from_url(candidate)
            return candidate
        except Exception:
            resolved_url = _resolve_shared_url(candidate)
            if resolved_url:
                return resolved_url
    return ""


def _resolve_shared_url(url: str) -> str:
    """Follow a mobile share link once and accept only a supported final site."""
    parsed_url = urlparse(url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        return ""
    try:
        response = requests.get(
            url,
            headers=build_headers(url),
            timeout=15,
            allow_redirects=True,
            stream=True,
        )
        resolved_url = str(response.url or "")
        response.close()
        detect_site_from_url(resolved_url)
        return resolved_url
    except Exception as exc:  # noqa: BLE001
        log(f"Telegram kısa bağlantısı açılamadı: {exc}")
        return ""


def _quick_add_defaults() -> Dict[str, Any]:
    return {"pending": []}


def _load_quick_adds() -> Dict[str, Any]:
    payload = load_json(TELEGRAM_QUICK_ADD_PATH, _quick_add_defaults())
    if not isinstance(payload, dict):
        return _quick_add_defaults()
    pending = payload.get("pending")
    payload["pending"] = pending if isinstance(pending, list) else []
    return payload


def _save_quick_adds(payload: Dict[str, Any]) -> None:
    save_json(TELEGRAM_QUICK_ADD_PATH, payload)


def _prune_pending_quick_adds(items: Iterable[Dict[str, Any]]) -> list:
    cutoff = local_now() - timedelta(hours=QUICK_ADD_EXPIRY_HOURS)
    valid = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            created_at = datetime.fromisoformat(str(item.get("created_at") or ""))
            if created_at.tzinfo is None:
                created_at = created_at.astimezone()
        except ValueError:
            continue
        if created_at.astimezone() >= cutoff:
            valid.append(item)
    return valid


def _reply_to_message_id(event) -> Optional[int]:
    message = getattr(event, "message", None)
    direct_id = getattr(message, "reply_to_msg_id", None)
    if direct_id:
        return int(direct_id)
    reply_to = getattr(message, "reply_to", None)
    nested_id = getattr(reply_to, "reply_to_msg_id", None)
    return int(nested_id) if nested_id else None


def _pending_for_reply(event, pending_items: Iterable[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    reply_id = _reply_to_message_id(event)
    if reply_id is None:
        return None
    chat_id = str(getattr(event, "chat_id", ""))
    for item in pending_items:
        if str(item.get("chat_id", "")) != chat_id:
            continue
        if reply_id in {item.get("source_message_id"), item.get("prompt_message_id")}:
            return item
    return None


def _parse_target_price(text: str):
    value = str(text or "").strip()
    if not value:
        return None
    try:
        price = parse_decimal(value)
    except Exception:
        return None
    return price if price > 0 else None


def _quick_add_watch(url: str, target_price, name: str = "") -> str:
    """Append a Saved Messages watch through the same options path as the UI."""
    options = load_json(OPTIONS_PATH, {})
    if not isinstance(options, dict):
        options = {}
    watches = options.get("takip_edilenler")
    watches = [dict(item) for item in watches if isinstance(item, dict)] if isinstance(watches, list) else []
    normalized_url = str(url).strip()
    for watch in watches:
        for number in range(1, 6):
            if str(watch.get(f"url_{number}") or "").strip() == normalized_url:
                return "Bu bağlantı zaten takip ediliyor. Yeni kayıt oluşturulmadı."

    watch = {
        "name": str(name or "").strip(),
        "target_price": float(target_price),
        "url_1": normalized_url,
        "notify_once_in_24H": True,
        "active": True,
    }
    options["takip_edilenler"] = watches + [watch]
    save_options_and_restart(options)
    return "Takip kaydı eklendi"


async def _reply(event, text: str):
    return await event.reply(text)


async def _handle_saved_message_quick_add(event) -> bool:
    """Run the short Saved Messages conversation for adding a Hermes watch."""
    payload = _load_quick_adds()
    pending_items = _prune_pending_quick_adds(payload["pending"])
    pending = _pending_for_reply(event, pending_items)
    text = event.raw_text or ""

    if pending:
        if pending.get("stage") == "target_price":
            target_price = _parse_target_price(text)
            if target_price is None:
                await _reply(event, "Hermes: hedef fiyatı örneğin `40000` veya `40.000 TL` biçiminde yanıtla.")
                payload["pending"] = pending_items
                _save_quick_adds(payload)
                return True
            pending["target_price"] = str(target_price)
            if watch_name_required_for_url(str(pending.get("url") or "")):
                prompt = await _reply(
                    event,
                    "Hermes: bu bir arama sayfası. Takip edilecek ürünün adını yanıtla; "
                    "örnek: `Samsung Galaxy Tab S10 FE+`.",
                )
                pending["stage"] = "name"
                pending["prompt_message_id"] = getattr(prompt, "id", None)
                payload["pending"] = pending_items
                _save_quick_adds(payload)
                return True
            result = _quick_add_watch(str(pending["url"]), target_price)
            pending_items.remove(pending)
            payload["pending"] = pending_items
            _save_quick_adds(payload)
            await _reply(
                event,
                f"Hermes: {result}. Hedef fiyat: {format_tl(target_price, with_currency=True)}. "
                "Grup ve beden bilgisini Hermes Ayarlar ekranından ekleyebilirsin."
                if result == "Takip kaydı eklendi"
                else f"Hermes: {result}",
            )
            if result == "Takip kaydı eklendi":
                log(f"Telegram Kayıtlı Mesajlar ile takip eklendi: {pending['url']}")
            return True

        name = str(text).strip()
        if not name:
            await _reply(event, "Hermes: ürün adını yazman gerekiyor; örnek: `Samsung Galaxy Tab S10 FE+`.")
            return True
        target_price = parse_decimal(str(pending["target_price"]))
        result = _quick_add_watch(str(pending["url"]), target_price, name)
        pending_items.remove(pending)
        payload["pending"] = pending_items
        _save_quick_adds(payload)
        await _reply(
            event,
            f"Hermes: {result}. Hedef fiyat: {format_tl(target_price, with_currency=True)}. "
            "Grup ve beden bilgisini Hermes Ayarlar ekranından ekleyebilirsin."
            if result == "Takip kaydı eklendi"
            else f"Hermes: {result}",
        )
        if result == "Takip kaydı eklendi":
            log(f"Telegram Kayıtlı Mesajlar ile arama takibi eklendi: {name}")
        return True

    url = _extract_supported_url(text)
    if not url:
        payload["pending"] = pending_items
        _save_quick_adds(payload)
        return False

    prompt = await _reply(event, "Hermes: bu bağlantı için hedef fiyat nedir? Örnek: `40000` veya `40.000 TL`.")
    pending_items.append(
        {
            "chat_id": str(getattr(event, "chat_id", "")),
            "source_message_id": getattr(event, "id", None),
            "prompt_message_id": getattr(prompt, "id", None),
            "url": url,
            "stage": "target_price",
            "created_at": local_now().isoformat(),
        }
    )
    payload["pending"] = pending_items
    _save_quick_adds(payload)
    log(f"Telegram Kayıtlı Mesajlar bağlantısı algılandı: {url}")
    return True


def _matching_keyword(message_text: str, keywords: Iterable[str]) -> Optional[str]:
    normalized_message = normalize_text(message_text)
    for keyword in keywords:
        if normalize_text(keyword) in normalized_message:
            return keyword
    return None


def _has_exclude_keyword(message_text: str, exclude_keywords: Iterable[str]) -> bool:
    normalized_message = normalize_text(message_text)
    return any(normalize_text(keyword) in normalized_message for keyword in exclude_keywords)


def _telegram_message_link(event) -> str:
    chat = getattr(event, "chat", None)
    username = getattr(chat, "username", None)
    if username:
        return f"https://t.me/{username}/{event.id}"
    return ""


def _message_preview(text: str, max_length: int = 1024) -> str:
    clean = re.sub(r"\s+", " ", str(text or "")).strip()
    return clean[:max_length]


def _record_recent_notification(
    channel_name: str,
    keyword: str,
    url: str,
    text: str,
) -> None:
    status = _load_status()
    recent = status.get("recent_notifications")
    if not isinstance(recent, list):
        recent = []
    recent.insert(
        0,
        {
            "created_at": _now_text(),
            "channel": channel_name,
            "keyword": keyword,
            "url": url,
            "message": _message_preview(text, 180),
        },
    )
    status["recent_notifications"] = recent[:5]
    status["last_notification"] = _now_text()
    status["telegram_state"] = "Dinleniyor"
    save_json(TELEGRAM_STATUS_PATH, status)


def _send_keyword_notification(
    config: HermesConfig,
    event,
    channel_name: str,
    keyword: str,
    text: str,
) -> None:
    session = requests.Session()
    message_url = _telegram_message_link(event)
    message = _message_preview(text)
    send_pushover(
        session,
        config.pushover_user_key,
        config.pushover_api_token,
        "Telegram keyword alarmı",
        message,
        message_url,
        config.request_timeout_seconds,
        url_title="Telegram'da aç",
    )
    _increment_status("notifications_sent")
    _record_recent_notification(channel_name, keyword, message_url, text)
    log(f"Telegram bildirimi gönderildi: kanal={channel_name} | keyword={keyword}")


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
    saved_messages_chat = await client.get_me() if telegram_config.saved_messages_enabled else None
    listened_chats = list(resolved_channels)
    if saved_messages_chat is not None:
        listened_chats.append(saved_messages_chat)
    if not listened_chats:
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

        is_saved_message = (
            saved_messages_chat is not None
            and str(getattr(event, "chat_id", "")) == str(getattr(saved_messages_chat, "id", ""))
        )
        if is_saved_message:
            # Only messages the user writes in Saved Messages can start or answer a flow.
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
            _send_keyword_notification(config, event, _channel_label(event), keyword, text)
        except Exception as exc:  # noqa: BLE001
            record_telegram_error(f"Pushover bildirimi gönderilemedi: {exc}", "Telegram bildirim")

    client.add_event_handler(handle_message, events.NewMessage(chats=listened_chats))
    _save_status(telegram_state="Dinleniyor", last_error="")
    saved_messages_status = " | Kayıtlı Mesajlar hızlı ekleme aktif" if saved_messages_chat else ""
    log(
        f"Telegram kanal dinleme aktif: kanal={len(resolved_channels)} | "
        f"keyword={len(telegram_config.keywords)}{saved_messages_status}"
    )

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
