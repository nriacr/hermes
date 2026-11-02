"""Telegram: keyword relay from deal channels and quick-add from Saved Messages."""

import asyncio
import ipaddress
import re
import socket
import threading
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, Optional
from urllib.parse import parse_qs, urljoin, urlparse

import requests

from .. import traffic
from ..config import format_variant_filter, parse_variant_filter, read_options, watch_urls
from ..constants import (
    PRIORITY_LABELS,
    TELEGRAM_ERROR_EVENTS_PATH,
    TELEGRAM_LOGIN_STATE_PATH,
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
from ..supervisor import OPTIONS_LOCK, save_options_and_restart
from ..utils import (
    build_headers,
    detect_site_from_url,
    format_local_datetime,
    format_tl,
    local_now,
    normalize_offer_text,
    parse_decimal,
    utc_now,
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
FLAME = "\U0001F525"
URL_PATTERN = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)
# A bare price answers the latest question without a Telegram reply.
PRICE_ONLY_PATTERN = re.compile(r"^[\d\s.,]+(?:tl|₺)?$", re.IGNORECASE)
BOT_PREFIX = "Hermes:"
PREVIEW_ATTEMPTS = 4
POLL_ANSWER_LIMIT = 10
POLL_CHECK_SECONDS = 3
POLL_TEXT_LIMIT = 100
POLL_SKIP_LABEL = "Bu bölümde seçimim yok"
# Telegram-added cards are read every 15 minutes until changed on the settings page.
QUICK_ADD_PRIORITY = "15m"
SINGLE_CHOICE_KEYS = {"model", "priority"}
# A price is accepted only after every choice question has completed.
ANSWER_STAGES = {"poll", "target_price"}
PREVIEW_WAIT_SECONDS = 2


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


MAX_SHARE_REDIRECTS = 5


def _is_public_address(host: str) -> bool:
    """True only when every address the host resolves to is a routable public one."""
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except (OSError, UnicodeError):
        return False
    if not addresses:
        return False
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address.split("%", 1)[0])
        except ValueError:
            return False
        if not ip.is_global or ip.is_multicast:
            return False
    return True


def _public_http_url(url: str) -> bool:
    parsed_url = urlparse(url)
    return parsed_url.scheme in {"http", "https"} and bool(parsed_url.hostname) and _is_public_address(parsed_url.hostname)


def _resolve_shared_url(url: str) -> str:
    """Follow a mobile share link and accept only a supported final site.

    Every hop is checked so a short link cannot lead Hermes to a private or local address."""
    current = url
    try:
        for _hop in range(MAX_SHARE_REDIRECTS + 1):
            if not _public_http_url(current):
                log("Telegram kısa bağlantısı genel olmayan bir adrese yönleniyor; reddedildi.")
                return ""
            response = requests.get(current, headers=build_headers(current), timeout=15, allow_redirects=False, stream=True)
            traffic.count_requests(response, "telegram")
            location = response.headers.get("Location") if response.is_redirect else ""
            response.close()
            if not location:
                detect_site_from_url(current)
                return current
            current = urljoin(current, location)
        log("Telegram kısa bağlantısı çok fazla yönlendirme içeriyor; reddedildi.")
        return ""
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


async def _preview_url(event) -> str:
    """A short link the site will not open for Hermes (Hepsiburada app.hb.biz answers 403) may
    still be named in Telegram's own link preview; it can arrive a few seconds after the message."""
    message = getattr(event, "message", None)
    for attempt in range(PREVIEW_ATTEMPTS):
        webpage = getattr(getattr(message, "media", None), "webpage", None)
        for candidate in (getattr(webpage, "url", ""), getattr(webpage, "display_url", "")):
            candidate = str(candidate or "")
            if candidate and not candidate.startswith("http"):
                candidate = "https://" + candidate
            if candidate:
                try:
                    detect_site_from_url(candidate)
                    log(f"Telegram önizlemesinden ürün adresi alındı: {candidate}")
                    return candidate
                except Exception:  # noqa: BLE001
                    pass
        ready = webpage is not None and type(webpage).__name__ != "WebPagePending"
        if ready or attempt == PREVIEW_ATTEMPTS - 1:
            break
        await asyncio.sleep(PREVIEW_WAIT_SECONDS)
        client = getattr(event, "client", None)
        if client is None:
            break
        message = await client.get_messages(event.chat_id, ids=event.id)
    log(f"Telegram önizlemesinde desteklenen adres yok: {type(webpage).__name__} {getattr(webpage, 'url', '')}")
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
        message_ids = {item.get("source_message_id"), item.get("prompt_message_id"), item.get("price_message_id"),
                       (item.get("current") or {}).get("message_id")}
        if str(item.get("chat_id", "")) == chat_id and reply_id in message_ids:
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


def _quick_add_card(url: str, providers=None) -> Dict[str, Any]:
    """Read the link like the new card's Kontrol et and keep the choices the link itself names."""
    from ..providers.registry import ProviderSet
    from ..web.settings import check_link  # imported here: the web package is heavier than the listener

    result = check_link(url, providers if providers is not None else ProviderSet())
    if not result.get("ok"):
        return {"error": str(result.get("error") or "Sayfa okunamadı.")}
    if result.get("search"):
        name = _quick_add_search_name(url)
        if not name:
            return {"error": "Arama bağlantısındaki ürün adı çözümlenemedi"}
        return {"title": name, "name": name, "size": "", "variant_filter": ""}
    options = set(result.get("options") or [])
    dimensions = result.get("dimensions") or {}
    defaults = result.get("defaults") or {}
    title = str(result.get("title") or "").strip()
    groups = _choice_groups(options, dimensions, defaults, result.get("variants") or [])
    # A model site (Togg) names the card after the model, as the new card does.
    name = ((defaults.get("Model") or [""])[0] if "model" in options else title).strip()
    size = ""
    sizes = [str(value) for value in defaults.get("Beden") or []]
    if "size" in options and len(sizes) == 1:
        size = sizes[0]
    chosen: Dict[str, list] = {}
    if "variant_filter" in options:
        for dimension, values in dimensions.items():
            if len(values) < 2 or (dimension == "Beden" and "size" in options):
                continue
            picked = [value for value in defaults.get(dimension) or [] if value in values]
            if picked:
                chosen[dimension] = picked
    return {"title": title or name, "name": name, "size": size, "variant_filter": format_variant_filter(chosen),
            "groups": groups}


def _choice_groups(options, dimensions, defaults, variants) -> list:
    """The new card's choice boxes, as poll groups: sizes, variant values and models with two or more values."""
    in_stock = {value for item in variants if item.get("in_stock") for value in (item.get("attributes") or {}).values()}
    every = {value for item in variants for value in (item.get("attributes") or {}).values()}
    out = sorted(every - in_stock)
    groups = []
    for dimension, values in dimensions.items():
        values = [str(value) for value in values]
        if len(values) < 2:
            continue
        if dimension == "Model" and "model" in options:
            key = "model"
        elif dimension == "Beden" and "size" in options:
            key = "size"
        elif "variant_filter" in options and not (dimension == "Beden" and "size" in options):
            key = f"variant:{dimension}"
        else:
            continue
        groups.append({"key": key, "title": dimension, "values": values, "out": [value for value in values if value in out],
                       "chosen": [value for value in defaults.get(dimension) or [] if value in values]})
    groups.append({"key": "priority", "title": "Tarama aralığı",
                   "values": list(PRIORITY_LABELS.values()), "out": [], "chosen": []})
    return groups


def _poll_media(group: Dict[str, Any], values: list, part: str):
    from telethon.tl.types import InputMediaPoll, Poll, PollAnswer, TextWithEntities

    answers = [PollAnswer(TextWithEntities((value + (" (tükendi)" if value in group["out"] else ""))[:POLL_TEXT_LIMIT], []),
                          str(index).encode()) for index, value in enumerate(values)]
    question = TextWithEntities(f"{group['title']}{part}", [])
    poll = Poll(id=int.from_bytes(uuid.uuid4().bytes[:8], "big") >> 1, question=question, answers=answers,
                multiple_choice=group["key"] not in SINGLE_CHOICE_KEYS)
    return InputMediaPoll(poll=poll)


def _poll_steps(card: Dict[str, Any]) -> list:
    """The choice questions in order, one poll each (split at Telegram's answer limit)."""
    steps = []
    for group in card.get("groups") or []:
        # Leave room for skipping a section, so every value remains selectable
        # without forcing a choice from every section of a long list.
        width = POLL_ANSWER_LIMIT - 1 if len(group["values"]) > POLL_ANSWER_LIMIT else POLL_ANSWER_LIMIT
        chunks = [group["values"][start:start + width] for start in range(0, len(group["values"]), width)]
        for number, values in enumerate(chunks, start=1):
            if len(chunks) > 1:
                values = values + [POLL_SKIP_LABEL]
            if len(values) < 2:
                continue
            part = f" ({number}/{len(chunks)})" if len(chunks) > 1 else ""
            steps.append({"key": group["key"], "title": group["title"], "part": part, "values": values, "out": group["out"]})
    return steps


async def _send_poll(client, chat_id, step: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        message = await client.send_file(chat_id, _poll_media(step, step["values"], step["part"]))
    except Exception as exc:  # noqa: BLE001
        log(f"Telegram seçenek anketi gönderilemedi: {step['title']} | {exc}")
        return None
    poll = getattr(getattr(message, "media", None), "poll", None)
    return {**step, "message_id": getattr(message, "id", None), "poll_id": getattr(poll, "id", None)}


async def _ask_next(client, pending: Dict[str, Any]) -> None:
    """Send the next choice poll; after the last one ask for the target price."""
    if pending.get("steps"):
        step = pending["steps"][0]
        sent = await _send_poll(client, int(pending["chat_id"]), step)
        if sent:
            pending["steps"].pop(0)
            pending.update(stage="poll", current=sent)
            log(f"Telegram hızlı ekleme sorusu: {step['title']}{step['part']}")
            return
        pending.update(stage="failed", current=None)
        await client.send_message(int(pending["chat_id"]),
                                  f"Hermes: {step['title']} anketi gönderilemedi. Takip eklenmedi; bağlantıyı yeniden paylaş.")
        return
    pending.update(stage="target_price", current=None)
    message = await client.send_message(int(pending["chat_id"]), "Hermes: Hedef fiyat nedir?")
    message_id = getattr(message, "id", None)
    pending["price_message_id"] = message_id if isinstance(message_id, int) else None
    log("Telegram hızlı ekleme sorusu: Hedef fiyat")


def _chosen_values(results, values: list) -> list:
    picked = []
    for item in getattr(results, "results", None) or []:
        option = bytes(getattr(item, "option", b"") or b"").decode(errors="ignore")
        if getattr(item, "chosen", False) and option.isdigit() and int(option) < len(values):
            picked.append(values[int(option)])
    return picked


async def _advance(client, pending: Dict[str, Any], picked: list) -> None:
    current = pending["current"]
    values = [value for value in picked if value != POLL_SKIP_LABEL]
    if values:
        choices = pending.setdefault("choices", {})
        choices[current["key"]] = list(dict.fromkeys(choices.get(current["key"], []) + values))
    text = f"{', '.join(values)} seçildi." if values else "Bu bölümde seçim yapılmadı."
    await client.send_message(int(pending["chat_id"]), f"Hermes: {current['title']}{current['part']}: {text}")
    log(f"Telegram hızlı ekleme seçimi: {current['title']}{current['part']}: {text}")
    await _ask_next(client, pending)


async def _read_current_poll(client, pending: Dict[str, Any]) -> list:
    current = pending.get("current") or {}
    message = await client.get_messages(int(pending["chat_id"]), ids=current.get("message_id"))
    return _chosen_values(getattr(getattr(message, "media", None), "results", None), current.get("values") or [])


async def _handle_poll_vote(client, update) -> bool:
    """A vote update for the current choice poll: confirm it and move to the next question."""
    payload = _load_quick_adds()
    pending_items = _prune_pending_quick_adds(payload["pending"])
    poll_id = getattr(update, "poll_id", None)
    pending = next((item for item in pending_items if item.get("stage") == "poll"
                    and (item.get("current") or {}).get("poll_id") == poll_id), None)
    if pending is None:
        return False
    picked = _chosen_values(getattr(update, "results", None), pending["current"]["values"])
    if not picked:
        try:  # an update without our own ticks: read the poll message itself
            picked = await _read_current_poll(client, pending)
        except Exception as exc:  # noqa: BLE001
            log(f"Telegram seçenek anketi okunamadı: {exc}")
    if not picked:
        return False
    await _advance(client, pending, picked)
    payload["pending"] = pending_items
    save_json(TELEGRAM_QUICK_ADD_PATH, payload)
    return True


async def _check_open_polls(client) -> bool:
    """Telegram does not always send this session a vote made on the phone, so the open poll is read directly."""
    payload = _load_quick_adds()
    pending_items = _prune_pending_quick_adds(payload["pending"])
    advanced = False
    for pending in [item for item in pending_items if item.get("stage") == "poll" and item.get("current")]:
        try:
            picked = await _read_current_poll(client, pending)
        except Exception as exc:  # noqa: BLE001
            log(f"Telegram seçenek anketi okunamadı: {exc}")
            continue
        if picked:
            await _advance(client, pending, picked)
            advanced = True
    if advanced:
        payload["pending"] = pending_items
        save_json(TELEGRAM_QUICK_ADD_PATH, payload)
    return advanced


async def _watch_open_polls(client, lock: asyncio.Lock) -> None:
    while True:
        await asyncio.sleep(POLL_CHECK_SECONDS)
        try:
            if any(item.get("stage") == "poll" for item in _load_quick_adds()["pending"]):
                async with lock:  # the vote update and this check never answer one poll twice
                    await _check_open_polls(client)
        except Exception as exc:  # noqa: BLE001
            record_telegram_error(f"Kayıtlı Mesajlar anket cevabı işlenemedi: {exc}", "Telegram hızlı ekleme")


def _apply_poll_choices(pending: Dict[str, Any], chosen: Dict[str, list]) -> Dict[str, str]:
    card = {"name": str(pending.get("name") or ""), "size": str(pending.get("size") or ""),
            "variant_filter": str(pending.get("variant_filter") or ""),
            "priority": str(pending.get("priority") or QUICK_ADD_PRIORITY)}
    priorities = {label: key for key, label in PRIORITY_LABELS.items()}
    if chosen.get("priority") and chosen["priority"][0] in priorities:
        card["priority"] = priorities[chosen["priority"][0]]
    if chosen.get("model"):
        card["name"] = chosen["model"][0]
    if chosen.get("size"):
        card["size"] = "; ".join(chosen["size"])
    variants = parse_variant_filter(card["variant_filter"])
    for key, values in chosen.items():
        if key.startswith("variant:"):
            variants[key.split(":", 1)[1]] = values
    card["variant_filter"] = format_variant_filter(variants)
    return card


def _choice_text(card: Dict[str, Any]) -> str:
    parts = [f"Beden={card['size']}"] if card.get("size") else []
    if card.get("variant_filter"):
        parts.append(str(card["variant_filter"]))
    return "; ".join(parts)


QUICK_ADD_DONE = "Takip kaydı eklendi"


def _quick_add_watch(url: str, target_price, name: str = "", size: str = "", variant_filter: str = "",
                     priority: str = QUICK_ADD_PRIORITY) -> str:
    """Append a Saved Messages watch through the same options path as the UI."""
    with OPTIONS_LOCK:
        return _quick_add_watch_locked(url, target_price, name, size, variant_filter, priority)


def _quick_add_watch_locked(url: str, target_price, name: str, size: str = "", variant_filter: str = "",
                            priority: str = QUICK_ADD_PRIORITY) -> str:
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
    # The same card the settings page saves for a new watch.
    item: Dict[str, Any] = {
        "id": uuid.uuid4().hex,
        "name": normalized_name,
        "target_price": float(target_price),
        "include_variations": bool(variant_filter),
        "priority": priority if priority in PRIORITY_LABELS else QUICK_ADD_PRIORITY,
        "official_seller_only": False,
        "active": True,
    }
    if size:
        item["size"] = size
    if variant_filter:
        item["variant_filter"] = variant_filter
    item["url_1"] = normalized_url
    item["check_now_token"] = utc_now()
    options["takip_edilenler"] = watches + [item]
    save_options_and_restart(options)
    return QUICK_ADD_DONE


def _latest_pending(event, pending_items: Iterable[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    chat_id = str(getattr(event, "chat_id", ""))
    waiting = [item for item in pending_items if str(item.get("chat_id", "")) == chat_id and item.get("stage") in ANSWER_STAGES]
    return waiting[-1] if waiting else None


async def _handle_saved_message_quick_add(event, providers=None) -> bool:
    """Run the short Saved Messages conversation for adding a Hermes watch."""
    text = (event.raw_text or "").strip()
    if text.startswith(BOT_PREFIX):
        return True  # Hermes's own answers in Saved Messages
    payload = _load_quick_adds()
    pending_items = _prune_pending_quick_adds(payload["pending"])
    pending = _pending_for_reply(event, pending_items)
    if pending is None and _reply_to_message_id(event) is None and PRICE_ONLY_PATTERN.match(text):
        pending = _latest_pending(event, pending_items)

    if pending and pending.get("stage") == "poll":
        await event.reply("Hermes: Önce açık anketteki seçimini gönder; hedef fiyatı bütün sorulardan sonra soracağım.")
        return True

    if pending and pending.get("stage") == "target_price":
        target_price = _parse_target_price(text)
        if target_price is None:
            await event.reply("Hermes: hedef fiyatı sayı olarak yaz.")
            payload["pending"] = pending_items
            save_json(TELEGRAM_QUICK_ADD_PATH, payload)
            return True
        pending.update(_apply_poll_choices(pending, pending.get("choices") or {}))
        result = _quick_add_watch(str(pending["url"]), target_price, pending["name"], pending["size"], pending["variant_filter"],
                                  pending["priority"])
        pending_items.remove(pending)
        payload["pending"] = pending_items
        save_json(TELEGRAM_QUICK_ADD_PATH, payload)
        if result == QUICK_ADD_DONE:
            choice = _choice_text(pending)
            await event.reply(
                f"Hermes: ✅ {result}: {pending['name'] or 'ürün'}. Hedef fiyat: {format_tl(target_price, with_currency=True)}."
                + (f" Seçim: {choice}." if choice else "")
                + f" Tarama: {PRIORITY_LABELS.get(pending['priority'], pending['priority'])}."
            )
            log(f"Telegram Kayıtlı Mesajlar ile takip eklendi: {pending['url']}")
        else:
            await event.reply(f"Hermes: ❌ Takip eklenmedi. {result}")
        return True

    if not URL_PATTERN.search(text):
        return False
    prompt = await event.reply("Hermes: bağlantı çözülüyor, ürün ve seçenekler kontrol ediliyor…")
    url = await asyncio.to_thread(_extract_supported_url, text)
    if not url and URL_PATTERN.search(text):
        url = await _preview_url(event)
        if not url:
            payload["pending"] = pending_items
            save_json(TELEGRAM_QUICK_ADD_PATH, payload)
            await prompt.edit("Hermes: bu bağlantıdan desteklenen bir ürün sayfasına ulaşılamadı; takip eklenmedi.")
            return True
    if not url:
        payload["pending"] = pending_items
        save_json(TELEGRAM_QUICK_ADD_PATH, payload)
        return False
    log("Telegram paylaşım bağlantısı çözüldü: " + urlparse(url)._replace(query="", fragment="").geturl())
    card = await asyncio.to_thread(_quick_add_card, url, providers)
    if card.get("error"):
        await prompt.edit(f"Hermes: {card['error']}. Takip eklenmedi; bağlantıyı Hermes Ayarlar ekranında Kontrol et ile ekleyebilirsin.")
        log(f"Telegram Kayıtlı Mesajlar bağlantısı okunamadı: {url} | {card['error']}")
        return True
    choice = _choice_text(card)
    await prompt.edit(f"Hermes: {card.get('title') or 'Ürün'}" + (f"\nLinkteki seçim: {choice}" if choice else ""))
    pending = {
        "chat_id": str(getattr(event, "chat_id", "")),
        "source_message_id": getattr(event, "id", None),
        "prompt_message_id": getattr(prompt, "id", None),
        "url": url,
        "name": card.get("name", ""),
        "size": card.get("size", ""),
        "variant_filter": card.get("variant_filter", ""),
        "steps": _poll_steps(card),
        "choices": {},
        "stage": "poll",
        "created_at": local_now().isoformat(),
    }
    await _ask_next(event.client, pending)
    payload = _load_quick_adds()  # the page read can take minutes; keep answers saved meanwhile
    pending_items = _prune_pending_quick_adds(payload["pending"])
    pending_items.append(pending)
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
    notifier.send("Telegram keyword alarmı", _message_preview(text), message_url, url_title="Telegram'da aç",
                  event_id="telegram:" + _message_key(event),
                  metadata={"telegram": [channel_name, keyword, message_url, _message_preview(text, 180)], "quiet": True})
    log(f"Telegram bildirimi kuyruğa alındı: kanal={channel_name} | keyword={keyword}")


def _is_flame_message(event) -> bool:
    """A message that is only a flame: plain emoji text or a sticker whose emoji is the flame."""
    text = re.sub(r"[\s\ufe0f]", "", event.raw_text or "")
    if text:
        return set(text) == {FLAME}
    return getattr(getattr(event.message, "file", None), "emoji", "") == FLAME


async def _send_flame_notification(notifier: Pushover, event, channel_name: str) -> None:
    previous = (await event.client.get_messages(event.chat_id, limit=1, max_id=event.id) or [None])[0]
    if previous is None:
        return
    text = _message_preview(previous.raw_text) or "(Metinsiz paylaşım, Telegram'da aç)"
    message_url = _telegram_message_link(previous)
    notifier.send(f"{FLAME} {channel_name}", text, message_url, url_title="Telegram'da aç",
                  event_id="telegram:flame:" + _message_key(event),
                  metadata={"telegram": [channel_name, FLAME, message_url, text[:180]], "quiet": True})
    log(f"Telegram alev bildirimi kuyruğa alındı: kanal={channel_name}")


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
        raise WaitingForUser() from None
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


async def _listen(config: HermesConfig, notifier: Pushover, providers=None) -> None:
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

        poll_lock = asyncio.Lock()

        async def handle_message(event) -> None:
            text = event.raw_text or ""
            _update_status(last_check=_now_text(), telegram_state="Dinleniyor")
            if _message_key(event) in load_json(TELEGRAM_SEEN_MESSAGES_PATH, {}):
                return
            if saved_messages_chat is not None and str(getattr(event, "chat_id", "")) == str(getattr(saved_messages_chat, "id", "")):
                # Only messages the user writes in Saved Messages start or answer a flow.
                if getattr(event, "out", False):
                    try:
                        async with poll_lock:
                            await _handle_saved_message_quick_add(event, providers)
                        _first_time_seen(_message_key(event))
                    except Exception as exc:  # noqa: BLE001
                        record_telegram_error(f"Kayıtlı Mesajlar ile takip eklenemedi: {exc}", "Telegram hızlı ekleme")
                        try:
                            await event.reply(f"Hermes: ❌ Takip eklenmedi: {exc}")
                        except Exception:  # noqa: BLE001
                            pass
                return
            if _is_flame_message(event):
                try:
                    await _send_flame_notification(notifier, event, _channel_label(event))
                    _first_time_seen(_message_key(event))
                except Exception as exc:  # noqa: BLE001
                    record_telegram_error(f"Alev bildirimi gönderilemedi: {exc}", "Telegram bildirim")
                return
            keyword = _matching_keyword(text, telegram_config.keywords)
            if not keyword:
                return
            if _has_exclude_keyword(text, telegram_config.exclude_keywords):
                log(f"Telegram mesajı exclude keyword nedeniyle atlandı: kanal={_channel_label(event)} | keyword={keyword}")
                return
            try:
                _send_keyword_notification(notifier, event, _channel_label(event), keyword, text)
                _first_time_seen(_message_key(event))
            except Exception as exc:  # noqa: BLE001
                record_telegram_error(f"Pushover bildirimi gönderilemedi: {exc}", "Telegram bildirim")

        client.add_event_handler(handle_message, events.NewMessage(chats=listened_chats))
        if saved_messages_chat is not None:
            from telethon.tl.types import UpdateMessagePoll

            async def handle_poll_vote(update) -> None:
                try:
                    async with poll_lock:
                        await _handle_poll_vote(client, update)
                except Exception as exc:  # noqa: BLE001
                    record_telegram_error(f"Kayıtlı Mesajlar anket cevabı işlenemedi: {exc}", "Telegram hızlı ekleme")

            client.add_event_handler(handle_poll_vote, events.Raw(UpdateMessagePoll))
            poll_watch = asyncio.ensure_future(_watch_open_polls(client, poll_lock))
        else:
            poll_watch = None
        _update_status(telegram_state="Dinleniyor", last_error="")
        saved_note = " | Kayıtlı Mesajlar hızlı ekleme aktif" if saved_messages_chat else ""
        log(f"Telegram kanal dinleme aktif: kanal={len(resolved_channels)} | keyword={len(telegram_config.keywords)}{saved_note}")
        heartbeat = asyncio.ensure_future(_heartbeat())
        try:
            await client.run_until_disconnected()
        finally:
            heartbeat.cancel()
            if poll_watch is not None:
                poll_watch.cancel()
        raise ConnectionError("Telegram bağlantısı kapandı.")
    finally:
        await client.disconnect()


def run_telegram_listener(config: HermesConfig, stop: Optional[threading.Event] = None, notifier=None, providers=None) -> None:
    stop = stop or threading.Event()
    if not config.telegram.enabled:
        _update_status(telegram_enabled=False, telegram_state="Pasif", telegram_channels=0, telegram_keywords=0,
                       last_check=_now_text())
        log("Telegram dinleme pasif.")
        return
    if TelegramClient is None or events is None:
        record_telegram_error("Telethon paketi bulunamadı. Add-on imajı yeniden kurulmalı.")
        return
    if notifier is None:
        raise RuntimeError("Telegram bildirim kuyruğu yapılandırılmadı.")
    while not stop.is_set():
        try:
            asyncio.run(_listen(config, notifier, providers))
        except WaitingForUser:
            log("Telegram kullanıcı işlemi bekliyor; ayarlar kaydedilince yeniden denenecek.")
            return
        except Exception as exc:  # noqa: BLE001
            record_telegram_error(f"{exc} | {RECONNECT_DELAY_SECONDS} sn sonra yeniden bağlanılacak.")
        stop.wait(RECONNECT_DELAY_SECONDS)


def start_telegram_listener(config: HermesConfig, stop: Optional[threading.Event] = None, notifier=None,
                            providers=None) -> Optional[threading.Thread]:
    if not config.telegram.enabled:
        run_telegram_listener(config, stop, notifier)
        return None
    thread = threading.Thread(target=run_telegram_listener, args=(config, stop, notifier, providers), name="telegram-listener", daemon=True)
    thread.start()
    return thread
