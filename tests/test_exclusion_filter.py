"""The exclusion filter applies to kept rows too, and the filter ignores letter case."""
import unittest
from decimal import Decimal

from support import TempData, config, key, monitor, watch

from hermes.models import PriceSummaryRow, StockSummaryRow
from hermes.monitor import summary
from hermes.providers.base import excluded_term_in_title
from hermes.storage import load_json

OLD = "2026-10-16 10:00:00"
PRODUCT = "https://www.hepsiburada.com/apple-iphone-17-pro-max-p-HBCV00009Z3XIO"
TB2 = "https://www.hepsiburada.com/apple-iphone-17-pro-max-2-tb-p-HBCV00009Z7YMA"
GB512 = "https://www.hepsiburada.com/apple-iphone-17-pro-max-512-gb-p-HBCV00009Z7YM1"


class CaseInsensitiveTests(unittest.TestCase):
    def test_filter_ignores_case_but_keeps_the_written_form(self):
        for term in ("2 TB", "2 tb", "2 Tb"):
            self.assertTrue(excluded_term_in_title(watch(excluded_terms=[term]), "iPhone 17 Pro Max 2 TB Gümüş"), term)
        self.assertFalse(excluded_term_in_title(watch(excluded_terms=["2 TB"]), "iPhone 17 Pro Max 2048 GB"))
        self.assertFalse(excluded_term_in_title(watch(excluded_terms=["2 TB"]), "iPhone 17 Pro Max 256 GB"))
        self.assertEqual(excluded_term_in_title(watch(excluded_terms=["kılıf"]), "Telefon KILIF seti"), "kılıf")


class KeptRowsTests(unittest.TestCase):
    def setUp(self):
        self.data = TempData()
        self.addCleanup(self.data.cleanup)
        self.rule = watch("iPhone 17 Pro Max", PRODUCT, target="90000", tracking_id="hb", include_variations=True,
                          excluded_terms=["2 TB"])
        offers = {}
        self.keys = []
        for number, (title, url, price) in enumerate((
                ("iPhone 17 Pro Max 2 TB Gümüş", TB2, "99000"),
                ("iPhone 17 Pro Max 512 GB Gümüş", GB512, "80000"))):
            item = f"offer{number}"
            self.keys.append(item)
            offers[item] = {"last_price": price, "title": title, "url": url, "tracking_id": "hb",
                            "last_checked_at": OLD, "last_price_checked_at": OLD}
        self.data.write_state({key(self.rule): {"offer_keys": self.keys, "tracking_id": "hb",
                                                "unavailable_variants": [
                                                    {"product_title": "iPhone 17 Pro Max 2 TB Siyah", "product_url": TB2 + "S"},
                                                    {"product_title": "iPhone 17 Pro Max 256 GB Siyah", "product_url": GB512 + "S"}],
                                                "unavailable_checked_at": OLD}, **offers})

    def test_cached_rows_skip_excluded_terms_but_ids_stay_removable(self):
        state = self.data.state()
        rows = summary.cached_summary_rows(self.rule, key(self.rule), state, "Hepsiburada")
        self.assertEqual([row.product_url for row in rows], [GB512])
        self.assertEqual(len(summary.cached_offer_ids(self.rule, key(self.rule), state, "Hepsiburada")), 2)
        stock = summary.cached_stock_rows(self.rule, state[key(self.rule)], "Hepsiburada")
        self.assertEqual([row.product_title for row in stock], ["iPhone 17 Pro Max 256 GB Siyah"])

    def test_removed_filter_brings_rows_back(self):
        plain = watch("iPhone 17 Pro Max", PRODUCT, target="90000", tracking_id="hb", include_variations=True)
        rows = summary.cached_summary_rows(plain, key(self.rule), self.data.state(), "Hepsiburada")
        self.assertEqual(len(rows), 2)
        self.assertEqual(self.data.state()["offer0"]["last_price"], "99000")

    def test_startup_drops_excluded_rows_from_the_published_table_only(self):
        def row(title, url, price):
            return PriceSummaryRow("Hepsiburada", title, url, Decimal(price), Decimal("90000"), Decimal(price),
                                   Decimal(price), tracking_id="hb", price_checked_at=OLD)
        summary.save_price_summary(self.data.files.summary, [
            row("iPhone 17 Pro Max 2 TB Gümüş", TB2, "99000"), row("iPhone 17 Pro Max 512 GB Gümüş", GB512, "80000")],
            [StockSummaryRow("Hepsiburada", "iPhone 17 Pro Max 2 TB Siyah", TB2 + "S", Decimal("90000"), "Stokta yok", OLD)])
        instance = monitor(config([self.rule]), self.data)
        self.addCleanup(instance.close)
        payload = load_json(self.data.files.summary, {})
        self.assertEqual([item["product_url"] for item in payload["rows"]], [GB512])
        self.assertEqual(payload["stock_rows"], [])
        self.assertEqual(self.data.state()["offer0"]["last_price"], "99000")


if __name__ == "__main__":
    unittest.main()
