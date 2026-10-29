"""Togg configurator: a model listed on the page is in stock, a missing one is a stock state."""

import json
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch

from support import watch

from hermes.errors import HermesError, HttpStatusHermesError, OutOfStockHermesError
from hermes.providers.base import ReadContext, WatchRead
from hermes.providers.togg import ToggProvider, extract_offers
from hermes.utils import detect_site_from_url
from test_monitor import CycleTestCase, config, key

URL = "https://configurator.togg.com.tr/"
V1 = "T10X V1 RWD Uzun Menzil"
V2 = "T10X V2 RWD Uzun Menzil"


def product(name, price="2193999.99"):
    return {"id": "prod_x", "name": name, "price": {"without_taxes": 1462666.66, "sct_25": {"with_all_taxes": price},
                                                    "sct_65": {"with_all_taxes": "2896079.9868"}}}


def payload(*names):
    return json.dumps({"products": [product(name) for name in names]})


class ToggParserTests(unittest.TestCase):
    def test_site_detection(self):
        self.assertEqual(detect_site_from_url(URL), "togg")

    def test_night_mode_pauses_reads_between_its_times_even_across_midnight(self):
        from datetime import datetime
        from hermes.config import prepare_watches
        card = {"name": V2, "target_price": 3000000, "url_1": URL, "night_mode": True, "night_start": "23:00", "night_end": "07:00"}
        rule = prepare_watches([card])[0]
        provider = ToggProvider()
        at = lambda hour, minute=0: datetime(2026, 10, 29, hour, minute).astimezone()  # noqa: E731
        with patch("hermes.providers.base.local_now") as now:
            for hour, minute, due in ((22, 59, True), (23, 0, False), (3, 30, False), (6, 59, False), (7, 0, True), (12, 0, True)):
                now.return_value = at(hour, minute)
                self.assertEqual(provider.read_due(rule), due, (hour, minute))
        off = prepare_watches([{**card, "night_mode": False}])[0]
        self.assertTrue(provider.read_due(off))
        daytime = prepare_watches([{**card, "night_start": "09:00", "night_end": "17:00"}])[0]
        with patch("hermes.providers.base.local_now", return_value=at(12)):
            self.assertFalse(provider.read_due(daytime))
        for bad in ({"night_start": "25:00"}, {"night_start": "08:00", "night_end": "08:00"}):
            self.assertEqual(prepare_watches([{**card, **bad}])[0].night_start, "")

    def test_link_check_lists_every_model_to_choose_from(self):
        session = Mock()
        session.get.return_value = SimpleNamespace(status_code=200, text=payload(V1, V2))
        preview = ToggProvider().inspect(URL, ReadContext(timeout=5, session=session))
        self.assertEqual(preview.dimensions, {"Model": [V1, V2]})
        self.assertEqual([(variant.label, variant.price) for variant in preview.variants],
                         [(V1, Decimal("2193999.99")), (V2, Decimal("2193999.99"))])

    def test_listed_model_is_an_offer_with_the_lowest_tax_inclusive_price(self):
        offers = extract_offers(payload(V1, "T10X V2 4More Obsidiyen", V2), V2, URL)
        self.assertEqual([offer.title for offer in offers], [V2])
        self.assertEqual(offers[0].price, Decimal("2193999.99"))
        self.assertEqual(offers[0].url, URL)

    def test_price_is_the_models_own_tax_bracket_with_dot_decimals(self):
        four_more = {"name": "T10X V2 4More Obsidiyen", "variants": [{"sct_rate": 65}],
                     "price": {"sct_25": {"with_all_taxes": 2399242.425}, "sct_65": {"with_all_taxes": 3167000.001}}}
        offers = extract_offers(json.dumps({"products": [four_more]}), "4More", URL)
        self.assertEqual(offers[0].price, Decimal("3167000.001"))

    def test_model_name_match_ignores_case_and_turkish_letters(self):
        self.assertEqual(len(extract_offers(payload(V2), "t10x v2 rwd uzun menzil", URL)), 1)

    def test_other_versions_do_not_count(self):
        with self.assertRaises(OutOfStockHermesError) as caught:
            extract_offers(payload(V1, "T10X V2 4More Obsidiyen"), V2, URL)
        self.assertEqual(caught.exception.product_title, V2)
        self.assertEqual(caught.exception.product_url, URL)

    def test_empty_list_is_a_stock_state_not_an_error(self):
        with self.assertRaises(OutOfStockHermesError):
            extract_offers(payload(), V2, URL)

    def test_missing_price_still_counts_as_in_stock(self):
        offers = extract_offers(json.dumps({"products": [{"name": V2}]}), V2, URL)
        self.assertEqual(offers[0].price, Decimal(0))

    def test_blank_model_name_is_an_error(self):
        with self.assertRaises(HermesError) as caught:
            extract_offers(payload(V2), "", URL)
        self.assertNotIsInstance(caught.exception, OutOfStockHermesError)

    def test_unreadable_list_is_an_error_not_a_stock_state(self):
        for body in ("<html>blocked</html>", "[]", json.dumps({"products": "x"})):
            with self.subTest(body=body):
                with self.assertRaises(HermesError) as caught:
                    extract_offers(body, V2, URL)
                self.assertNotIsInstance(caught.exception, OutOfStockHermesError)


class ToggProviderTests(unittest.TestCase):
    def ctx(self, *responses):
        session = Mock()
        session.get.side_effect = list(responses)
        return ReadContext(timeout=5, session=session)

    def test_one_request_per_cycle_serves_every_card(self):
        provider = ToggProvider()
        ctx = self.ctx(SimpleNamespace(status_code=200, text=payload(V1, V2)))
        for model in (V1, V2):
            offers = provider.read(watch(model, URL), ctx, WatchRead())
            self.assertEqual(offers[0].title, model)
        self.assertEqual(ctx.session.get.call_count, 1)
        provider.begin_cycle()
        ctx.session.get.side_effect = [SimpleNamespace(status_code=200, text=payload(V1))]
        with self.assertRaises(OutOfStockHermesError):
            provider.read(watch(V2, URL), ctx, WatchRead())
        self.assertEqual(ctx.session.get.call_count, 2)

    def test_http_error_is_an_error_and_is_not_cached(self):
        provider = ToggProvider()
        ctx = self.ctx(SimpleNamespace(status_code=503, text=""), SimpleNamespace(status_code=200, text=payload(V2)))
        with self.assertRaises(HttpStatusHermesError):
            provider.read(watch(V2, URL), ctx, WatchRead())
        self.assertEqual(provider.read(watch(V2, URL), ctx, WatchRead())[0].title, V2)

    def test_only_the_read_path_is_site_specific(self):
        self.assertTrue(ToggProvider.notifies_stock_return)
        self.assertFalse(ToggProvider.backs_off_on_protection)


class ToggCycleTests(CycleTestCase):
    def test_stock_arrival_sends_one_alert_and_shows_the_model(self):
        rule = watch(V2, URL, target="99999999")
        with patch.object(ToggProvider, "_products_payload", side_effect=[payload(V1), payload(V1, V2), payload(V1, V2)]):
            state = self.run_cycle(config([rule]))
            self.assertTrue(state[key(rule)]["last_out_of_stock_at"])
            self.assertEqual(len(self.published_stock()), 1)
            self.assertEqual(self.published_rows(), [])
            state = self.run_later(config([rule]))
            self.run_later(config([rule]), seconds=10)
        self.assertEqual(self.titles_sent(), ["Togg stok alarmı"])
        self.assertNotIn("last_out_of_stock_at", state[key(rule)])
        self.assertEqual([row["product_title"] for row in self.published_rows()], [V2])
        self.assertEqual(self.published_stock(), [])


if __name__ == "__main__":
    unittest.main()
