"""Bot transport, owner checks, multi-select and single target regression tests."""

import asyncio
import unittest
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from hermes.config import prepare_telegram_config
from hermes.telegram import bot
from hermes.web import settings


def group(key, values):
    return {"key": key, "title": key, "values": values, "out": [], "chosen": []}


class PriceTests(unittest.TestCase):
    def test_five_targets_round_up_to_fifty(self):
        self.assertEqual(bot.target_prices("3039"), ["2900", "2750", "2600", "2300", "1850"])
        self.assertEqual(bot.target_prices("3000")[1], "2700")

    def test_small_prices_have_no_duplicates_or_targets_at_or_above_price(self):
        self.assertEqual(bot.target_prices("100"), [])
        self.assertEqual(bot.target_prices("151"), ["150", "100"])
        for value in ("", None, "NaN", "Infinity", "-10"):
            self.assertEqual(bot.target_prices(value), [])

    def test_only_selected_in_stock_variants_supply_target_basis_with_full_precision(self):
        pending = {"choices": {"variant:Renk": ["Siyah"], "size": ["M", "L"]}, "prices": [
            {"attributes": {"Renk": "Beyaz", "Beden": "M"}, "price": "10", "in_stock": True},
            {"attributes": {"Renk": "Siyah", "Beden": "S"}, "price": "20", "in_stock": True},
            {"attributes": {"Renk": "Siyah", "Beden": "M"}, "price": "3039.000", "in_stock": True},
            {"attributes": {"Renk": "Siyah", "Beden": "L"}, "price": "2000", "in_stock": False},
            {"attributes": {"Renk": "Siyah", "Beden": "M"}, "price": "NaN", "in_stock": True}]}
        self.assertEqual(bot.selected_price(pending), Decimal("3039"))
        pending["choices"]["size"] = ["XL"]
        self.assertIsNone(bot.selected_price(pending))


class ConversationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "bot.json"
        self.client = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(id=50)), edit_message=AsyncMock())
        self.conversation = bot.Conversation(self.client, 7, state_path=self.path)
        self.pending = {"flow": "test", "step": 0, "choices": {}, "name": "Ürün", "url": "https://www.trendyol.com/a-p-123",
                        "groups": [group("variant:Renk", ["Siyah", "Beyaz"]), group("size", ["M", "L"]),
                                   group("priority", ["15 dk", "3 saat"])],
                        "prices": [{"price": "3039", "in_stock": True, "attributes": {"Renk": "Siyah", "Beden": "M"}}],
                        "created_at": bot.local_now().isoformat()}

    def event(self, text="", owner=7, private=True, data=None, message_id=50):
        return SimpleNamespace(raw_text=text, sender_id=owner, chat_id=owner, is_private=private, message_id=message_id,
                               data=data, reply=AsyncMock(), answer=AsyncMock())

    async def click(self, action, **fields):
        pending = self.conversation.load()
        event = self.event(data=f"h:{pending['flow']}:{pending['step']}:{action}".encode(), **fields)
        await self.conversation.callback(event)
        return event

    async def test_multi_color_multi_size_single_interval_and_single_target(self):
        await self.conversation.ask(self.pending)
        await self.click("pick0")
        await self.click("pick1")
        self.assertEqual(self.conversation.load()["step"], 0)
        await self.click("done")
        await self.click("pick0")
        await self.click("pick1")
        await self.click("done")
        await self.click("pick1")  # single interval advances immediately
        pending = self.conversation.load()
        self.assertEqual(pending["stage"], "target_price")
        self.assertEqual(pending["targets"], ["2900", "2750", "2600", "2300", "1850"])
        with patch.object(bot.shared, "_quick_add_watch", return_value=bot.shared.QUICK_ADD_DONE) as add:
            event = await self.click("price1")
            await self.conversation.callback(event)  # duplicate delivery/stale click
        add.assert_called_once_with(self.pending["url"], Decimal("2750"), "Ürün", "M; L", "Renk=Siyah, Beyaz", "3h")
        self.assertIsNone(self.conversation.load())

    async def test_manual_price_without_button_is_accepted_only_at_price_stage(self):
        await self.conversation.ask(self.pending)
        with patch.object(bot.shared, "_quick_add_watch", return_value=bot.shared.QUICK_ADD_DONE) as add:
            await self.conversation.message(self.event("2450"))
            add.assert_not_called()
            self.pending["step"] = len(self.pending["groups"])
            await self.conversation.ask(self.pending)
            await self.conversation.message(self.event("2.450 TL"))
        self.assertEqual(add.call_args.args[1], Decimal("2450"))

    async def test_empty_multi_selection_does_not_advance_and_toggle_can_uncheck(self):
        await self.conversation.ask(self.pending)
        await self.click("pick0")
        await self.click("pick0")
        event = await self.click("done")
        self.assertEqual(self.conversation.load()["step"], 0)
        event.answer.assert_awaited_once_with("En az bir seçenek işaretle.")

    async def test_pagination_retains_all_selections_and_restart_resumes(self):
        self.pending["groups"] = [group("size", [str(n) for n in range(30)])]
        await self.conversation.ask(self.pending)
        await self.click("pick0")
        await self.click("next")
        self.conversation = bot.Conversation(self.client, 7, state_path=self.path)
        await self.click("pick13")
        await self.click("next")
        await self.click("pick29")
        await self.click("done")
        self.assertEqual(self.conversation.load()["choices"]["size"], ["0", "13", "29"])

    async def test_other_users_group_chats_and_stale_callbacks_cannot_change_state(self):
        await self.conversation.ask(self.pending)
        before = self.path.read_text()
        with patch.object(bot.shared, "_quick_add_card") as inspect:
            await self.conversation.message(self.event(self.pending["url"], owner=8))
            await self.conversation.message(self.event(self.pending["url"], private=False))
            inspect.assert_not_called()
        await self.click("pick0", owner=8)
        await self.click("pick0", private=False)
        await self.click("pick0", message_id=49)
        await self.conversation.callback(self.event(data=b"h:old:0:pick0"))
        self.assertEqual(self.path.read_text(), before)

    async def test_failed_send_keeps_old_callback_inactive(self):
        self.client.send_message.side_effect = RuntimeError("offline")
        with self.assertRaises(RuntimeError):
            await self.conversation.ask(self.pending)
        await self.click("pick0")
        self.assertEqual(self.conversation.load()["selected"], [])

    async def test_manual_button_and_invalid_callbacks_do_not_save_a_watch(self):
        self.pending["step"] = len(self.pending["groups"])
        await self.conversation.ask(self.pending)
        with patch.object(bot.shared, "_quick_add_watch") as add:
            await self.click("manual")
            await self.click("price999")
            await self.conversation.message(self.event("-10"))
            await self.conversation.message(self.event("beden"))
        add.assert_not_called()
        self.assertEqual(self.conversation.load()["stage"], "target_price")

    async def test_shared_provider_inspection_starts_button_flow_without_polls(self):
        prompt = SimpleNamespace(id=10, edit=AsyncMock())
        event = self.event(self.pending["url"])
        event.reply.return_value = prompt
        card = {"title": "Ürün", "name": "Ürün", "groups": self.pending["groups"], "prices": self.pending["prices"]}
        with (patch.object(bot.shared, "_extract_supported_url", return_value=self.pending["url"]),
              patch.object(bot.shared, "_quick_add_card", return_value=card) as inspect):
            await self.conversation.message(event)
        inspect.assert_called_once_with(self.pending["url"], None)
        self.assertEqual(self.conversation.load()["stage"], "choices")
        self.assertTrue(self.client.send_message.call_args.kwargs["buttons"])


class BotSettingsTests(unittest.TestCase):
    def test_token_is_secret_not_echoed_and_blank_preserves_it(self):
        token = "123456:" + "x" * 32
        html = settings._telegram_section({"telegram_bot_token": token})
        self.assertNotIn(token, html)
        self.assertIn("type='password'", html)
        options = {"telegram_bot_token": token}
        settings._update_telegram_options(options, {"telegram_bot_token": [""]})
        self.assertEqual(options["telegram_bot_token"], token)
        self.assertNotIn(token, repr(prepare_telegram_config(options)))
        with self.assertRaisesRegex(ValueError, "Bot erişim anahtarı geçersiz"):
            settings._update_telegram_options(options, {"telegram_bot_token": ["wrong"]})

    def test_new_token_is_saved_without_changing_other_options(self):
        token = "123456:" + "x" * 32
        options = {"takip_edilenler": [{"id": "kept"}]}
        settings._update_telegram_options(options, {"telegram_bot_token": [token]})
        self.assertEqual(options["telegram_bot_token"], token)
        self.assertEqual(options["takip_edilenler"], [{"id": "kept"}])


class BotTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_login_uses_bot_token_and_memory_session_and_disconnects_on_shutdown(self):
        config = prepare_telegram_config({"api_id": "123", "api_hash": "hash", "telegram_bot_token": "secret-token"})
        client = SimpleNamespace(start=AsyncMock(), get_me=AsyncMock(return_value=SimpleNamespace(username="Hermes_takip_bot")),
                                 add_event_handler=Mock(), run_until_disconnected=AsyncMock(side_effect=asyncio.CancelledError),
                                 disconnect=AsyncMock())
        with (patch.object(bot, "TelegramClient", return_value=client) as factory,
              patch.object(bot.shared, "_update_status") as status):
            with self.assertRaises(asyncio.CancelledError):
                await bot.run_bot(config, 7)
        self.assertIsInstance(factory.call_args.args[0], bot.MemorySession)
        client.start.assert_awaited_once_with(bot_token="secret-token")
        client.disconnect.assert_awaited_once()
        self.assertEqual(client.add_event_handler.call_count, 2)
        status.assert_called_once_with(telegram_bot_state="Dinleniyor", telegram_bot_username="Hermes_takip_bot")

    async def test_failed_bot_login_never_logs_exception_text_or_token(self):
        config = prepare_telegram_config({"telegram_bot_token": "secret-token"})
        client = SimpleNamespace(start=AsyncMock(side_effect=RuntimeError("secret-token")), disconnect=AsyncMock())
        with (patch.object(bot, "TelegramClient", return_value=client), patch.object(bot.shared, "_update_status"),
              patch.object(bot.shared, "record_telegram_error") as error,
              patch.object(bot.asyncio, "sleep", side_effect=asyncio.CancelledError)):
            with self.assertRaises(asyncio.CancelledError):
                await bot.run_bot(config, 7)
        self.assertNotIn("secret-token", str(error.call_args))
        client.disconnect.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
