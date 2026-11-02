"""Owner-only bot conversations; provider inspection and watch persistence are shared."""

import asyncio
import uuid
from decimal import Decimal, InvalidOperation, ROUND_CEILING

from telethon import Button, TelegramClient, events
from telethon.sessions import MemorySession

from ..constants import DATA_DIR, PRIORITY_LABELS
from ..storage import load_json, save_json
from ..utils import format_tl, local_now
from . import listener as shared

STATE_PATH = DATA_DIR / "telegram_bot_quick_add.json"
PAGE_SIZE = 12
DISCOUNTS = (5, 10, 15, 25, 40)


def target_prices(price) -> list[str]:
    """Round each discounted price up to 50 TL, retaining unique lower targets."""
    try:
        base = Decimal(str(price))
    except Exception:
        return []
    if not base.is_finite() or base <= 0:
        return []
    targets = []
    for discount in DISCOUNTS:
        value = (base * (100 - discount) / 100 / 50).to_integral_value(rounding=ROUND_CEILING) * 50
        if 0 < value < base and str(value) not in targets:
            targets.append(str(value))
    return targets


def selected_price(pending):
    """Use only in-stock prices matching every chosen dimension, never an unselected sibling."""
    chosen = pending.get("choices") or {}
    filters = {}
    for key, values in chosen.items():
        if key == "size":
            filters["Beden"] = values
        elif key == "model":
            filters["Model"] = values
        elif key.startswith("variant:"):
            filters[key.split(":", 1)[1]] = values
    prices = []
    for row in pending.get("prices") or []:
        attributes = row.get("attributes") or {}
        if not row.get("in_stock") or any(attributes.get(key) not in values for key, values in filters.items()):
            continue
        try:
            price = Decimal(str(row.get("price") or ""))
        except InvalidOperation:
            continue
        if price.is_finite() and price > 0:
            prices.append(price)
    return min(prices) if prices else None


def authorized(event, owner_id):
    return bool(event.is_private and event.sender_id == owner_id and event.chat_id == owner_id)


class Conversation:
    def __init__(self, client, owner_id, providers=None, state_path=STATE_PATH):
        self.client, self.owner_id, self.providers, self.state_path = client, owner_id, providers, state_path
        self.lock = asyncio.Lock()

    def load(self):
        payload = load_json(self.state_path, {})
        pending = payload.get("pending") if isinstance(payload, dict) else None
        if isinstance(pending, dict) and shared._prune_pending_quick_adds([pending]):
            return pending
        return None

    def save(self, pending):
        save_json(self.state_path, {"pending": pending})

    def keyboard(self, pending):
        def button(label, action):
            return Button.inline(label, f"h:{pending['flow']}:{pending['step']}:{action}".encode())

        if pending["stage"] == "target_price":
            rows = [[button(format_tl(value, with_currency=True), f"price{index}")]
                    for index, value in enumerate(pending["targets"])]
            rows.append([button("✍️ Kendim fiyat gireceğim", "manual")])
            return rows
        group = pending["groups"][pending["step"]]
        page = pending.get("page", 0)
        values = group["values"]
        selected = pending.get("selected") or []
        rows = []
        for index in range(page * PAGE_SIZE, min((page + 1) * PAGE_SIZE, len(values))):
            value = values[index]
            label = ("✓ " if value in selected else "") + value + (" (tükendi)" if value in group["out"] else "")
            rows.append([button(label, f"pick{index}")])
        navigation = []
        if page:
            navigation.append(button("← Önceki", "prev"))
        if (page + 1) * PAGE_SIZE < len(values):
            navigation.append(button("Sonraki →", "next"))
        if navigation:
            rows.append(navigation)
        if group["key"] not in shared.SINGLE_CHOICE_KEYS:
            rows.append([button(f"Seçimi tamamla ({len(selected)})", "done")])
        return rows

    def question(self, pending):
        if pending["stage"] == "target_price":
            price = pending.get("base_price")
            basis = f"\nSeçili ürünlerin en düşük güncel fiyatı: {format_tl(price, with_currency=True)}." if price else ""
            return ("Hermes: Hangi fiyata düşünce haber vereyim?" + basis
                    + "\nTek bir hedef seç veya kendi fiyatını mesaj olarak yaz.")
        group = pending["groups"][pending["step"]]
        text = "Tek seçenek seç." if group["key"] in shared.SINGLE_CHOICE_KEYS else "Bir veya birden fazla seçenek işaretle, sonra Seçimi tamamla'ya bas."
        return f"Hermes: {group['title']}\n{text}"

    async def ask(self, pending):
        pending["page"] = 0
        pending["selected"] = []
        if pending["step"] < len(pending["groups"]):
            pending["stage"] = "choices"
        else:
            pending["stage"] = "target_price"
            price = selected_price(pending)
            pending["base_price"] = str(price) if price is not None else ""
            pending["targets"] = target_prices(price)
        # Persist before sending: a failed send must never leave the old question actionable.
        pending["message_id"] = None
        self.save(pending)
        message = await self.client.send_message(self.owner_id, self.question(pending), buttons=self.keyboard(pending))
        pending["message_id"] = message.id
        self.save(pending)

    async def finish(self, pending, price):
        if not price.is_finite() or price <= 0:
            await self.client.send_message(self.owner_id, "Hermes: Hedef fiyatı sıfırdan büyük bir sayı olarak yaz.")
            return
        card = shared._apply_poll_choices(pending, pending["choices"])
        result = await asyncio.to_thread(shared._quick_add_watch, pending["url"], price, card["name"], card["size"],
                                         card["variant_filter"], card["priority"])
        # Persistence succeeded (or duplicate was rejected). Old callbacks cannot create another card.
        self.save(None)
        try:
            await self.client.edit_message(self.owner_id, pending["message_id"], buttons=None)
        except Exception:
            shared.log("Telegram bot eski hedef düğmeleri temizlenemedi; kayıt sonucu korunuyor.")
        if result == shared.QUICK_ADD_DONE:
            text = (f"Hermes: ✅ {result}: {card['name'] or 'ürün'}. Hedef fiyat: {format_tl(price, with_currency=True)}."
                    f"\nSeçim: {shared._choice_text(card) or 'Varyant seçimi yok'}."
                    f" Tarama: {PRIORITY_LABELS[card['priority']]}.")
            shared.log("Telegram bot hızlı ekleme tamamlandı.")
        else:
            text = f"Hermes: Takip eklenmedi. {result}"
        await self.client.send_message(self.owner_id, text)

    async def message(self, event):
        if not authorized(event, self.owner_id):
            return
        async with self.lock:
            text = (event.raw_text or "").strip()
            if text in {"/start", "/help"}:
                await event.reply("Hermes: Ürün bağlantısını buraya paylaş. Renk ve bedeni çoklu, hedef fiyatı tekli seçebilirsin. "
                                  "Yeni bağlantı paylaşarak yeni seçim başlatabilirsin.")
                return
            pending = self.load()
            if shared.URL_PATTERN.search(text):
                # Reuse the exact Pi provider path used by Saved Messages and the settings page.
                prompt = await event.reply("Hermes: bağlantı çözülüyor, ürün ve seçenekler kontrol ediliyor…")
                url = await asyncio.to_thread(shared._extract_supported_url, text)
                if not url:
                    url = await shared._preview_url(event)
                if not url:
                    await prompt.edit("Hermes: Desteklenen bir ürün bağlantısına ulaşılamadı; takip eklenmedi.")
                    return
                card = await asyncio.to_thread(shared._quick_add_card, url, self.providers)
                if card.get("error"):
                    await prompt.edit(f"Hermes: {card['error']}. Takip eklenmedi.")
                    return
                pending = {**card, "flow": uuid.uuid4().hex[:12], "step": 0, "choices": {}, "url": url,
                           "created_at": local_now().isoformat()}
                await prompt.edit(f"Hermes: {card.get('title') or 'Ürün'}")
                await self.ask(pending)
                return
            if pending and pending["stage"] == "target_price":
                price = shared._parse_target_price(text)
                if price is None:
                    await event.reply("Hermes: Bir hedef fiyat düğmesine bas veya fiyatı sayı olarak yaz.")
                else:
                    await self.finish(pending, price)
            elif pending:
                await event.reply("Hermes: Önce renk, beden ve tarama aralığı seçimlerini tamamla.")
            else:
                await event.reply("Hermes: Takip eklemek için bir ürün bağlantısı paylaş.")

    async def callback(self, event):
        if not authorized(event, self.owner_id):
            await event.answer("Bu bot yalnız sahibine açıktır.", alert=True)
            return
        async with self.lock:
            pending = self.load()
            parts = event.data.decode(errors="replace").split(":")
            if (not pending or len(parts) != 4 or parts[:3] != ["h", pending["flow"], str(pending["step"])]
                    or event.message_id != pending.get("message_id")):
                await event.answer("Bu seçim artık geçerli değil. Son soruyu kullan.")
                return
            action = parts[3]
            if pending["stage"] == "target_price":
                if action == "manual":
                    await event.answer()
                    await self.client.send_message(self.owner_id, "Hermes: Hedef fiyatını sayı olarak yaz. Örneğin: 2450")
                elif action.startswith("price") and action[5:].isdigit() and int(action[5:]) < len(pending["targets"]):
                    await event.answer()
                    await self.finish(pending, Decimal(pending["targets"][int(action[5:])]))
                else:
                    await event.answer("Geçersiz seçim.")
                return
            group = pending["groups"][pending["step"]]
            if action.startswith("pick") and action[4:].isdigit() and int(action[4:]) < len(group["values"]):
                value = group["values"][int(action[4:])]
                if group["key"] in shared.SINGLE_CHOICE_KEYS:
                    pending["selected"] = [value]
                    action = "done"
                elif value in pending["selected"]:
                    pending["selected"].remove(value)
                else:
                    pending["selected"].append(value)
            elif action in {"prev", "next"}:
                last_page = (len(group["values"]) - 1) // PAGE_SIZE
                pending["page"] = max(0, min(last_page, pending["page"] + (1 if action == "next" else -1)))
            elif action != "done":
                await event.answer("Geçersiz seçim.")
                return
            if action == "done":
                if not pending["selected"]:
                    await event.answer("En az bir seçenek işaretle.")
                    return
                pending["choices"][group["key"]] = pending["selected"]
                await event.answer()
                await self.client.edit_message(self.owner_id, pending["message_id"],
                                               f"Hermes: {group['title']}: {', '.join(pending['selected'])}", buttons=None)
                pending["step"] += 1
                await self.ask(pending)
                return
            self.save(pending)
            await event.answer()
            await self.client.edit_message(self.owner_id, pending["message_id"], buttons=self.keyboard(pending))


async def run_bot(config, owner_id, providers=None):
    """Use an independent bot session; owner identity comes from the existing user login."""
    while True:
        client = TelegramClient(MemorySession(), config.api_id, config.api_hash)
        try:
            await client.start(bot_token=config.bot_token)
            me = await client.get_me()
            conversation = Conversation(client, owner_id, providers)

            async def on_message(event):
                try:
                    await conversation.message(event)
                except Exception as exc:
                    shared.record_telegram_error(f"Bot mesajı işlenemedi: {type(exc).__name__}", "Telegram bot")
                    if authorized(event, owner_id):
                        await event.reply("Hermes: İşlem tamamlanamadı. Son soruyu yeniden dene veya bağlantıyı tekrar paylaş.")

            async def on_callback(event):
                try:
                    await conversation.callback(event)
                except Exception as exc:
                    shared.record_telegram_error(f"Bot seçimi işlenemedi: {type(exc).__name__}", "Telegram bot")
                    await event.answer("İşlem tamamlanamadı. Son soruyu yeniden dene.", alert=True)

            client.add_event_handler(on_message, events.NewMessage(incoming=True))
            client.add_event_handler(on_callback, events.CallbackQuery())
            shared._update_status(telegram_bot_state="Dinleniyor", telegram_bot_username=me.username)
            shared.log(f"Telegram bot dinleme aktif: @{me.username}; yalnız hesap sahibi.")
            await client.run_until_disconnected()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Never include a transport exception's text: it may contain the token.
            shared._update_status(telegram_bot_state="Bağlanamadı")
            shared.record_telegram_error(f"Bot bağlantısı kurulamadı: {type(exc).__name__}. Bot anahtarını kontrol et.", "Telegram bot")
        finally:
            await client.disconnect()
        await asyncio.sleep(shared.RECONNECT_DELAY_SECONDS)
