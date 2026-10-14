"""Rows of deleted or paused watches must not come back after a restart."""
import unittest
from decimal import Decimal

from support import TempData, config, key, monitor, watch

from hermes.models import PriceSummaryRow, StockSummaryRow
from hermes.monitor import summary
from hermes.storage import load_json

OLD = "2026-10-14 10:00:00"


def price_row(title, url, tracking_id):
    return PriceSummaryRow("Amazon", title, url, Decimal("100"), Decimal("200"), Decimal("90"), Decimal("120"),
                           tracking_id=tracking_id, price_checked_at=OLD)


class DeletedWatchRowsTests(unittest.TestCase):
    def setUp(self):
        self.data = TempData()
        self.addCleanup(self.data.cleanup)
        self.kept = watch("Kalan", "https://www.amazon.com.tr/dp/B000000001", tracking_id="kept")
        self.paused = watch("Duran", "https://www.amazon.com.tr/dp/B000000002", tracking_id="paused", active=False)
        self.gone_url = "https://www.amazon.com.tr/dp/B000000003"
        self.old_variant = "https://www.amazon.com.tr/dp/B000000004"
        offer = {"last_price": "100", "title": "Kalan", "url": self.kept.url, "tracking_id": "kept", "last_checked_at": OLD}
        self.data.write_state({
            key(self.kept): {"offer_keys": [key(self.kept)], **offer,
                             "unavailable_variants": [{"product_title": "Stoksuz", "product_url": self.old_variant}],
                             "unavailable_checked_at": OLD},
            key(self.paused): {"last_price": "50", "title": "Duran", "url": self.paused.url, "last_checked_at": OLD},
        })
        summary.save_price_summary(self.data.files.summary, [
            price_row("Kalan", self.kept.url, "kept"),
            price_row("Duran", self.paused.url, "paused"),
            price_row("Silinen", self.gone_url, "deleted"),
        ], [
            StockSummaryRow("Amazon", "Stoksuz", self.old_variant, Decimal("200"), "Stokta yok", OLD),
            StockSummaryRow("Amazon", "Silinen stok", "https://www.amazon.com.tr/dp/B000000005", Decimal("200"), "Stokta yok", OLD),
        ])

    def test_startup_keeps_only_rows_of_active_watches(self):
        instance = monitor(config([self.kept, self.paused]), self.data)
        self.addCleanup(instance.close)
        payload = load_json(self.data.files.summary, {})
        self.assertEqual([row["product_title"] for row in payload["rows"]], ["Kalan"])
        self.assertEqual([row["product_title"] for row in payload["stock_rows"]], ["Stoksuz"])
        self.assertEqual((payload["row_count"], payload["stock_row_count"]), (1, 1))
        self.assertEqual(payload["rows"][0]["price_checked_at"], OLD)
        self.assertEqual(self.data.state()[key(self.paused)]["last_price"], "50")

    def test_unchanged_table_is_not_rewritten(self):
        summary.drop_unconfigured_summary_rows(self.data.files.summary, [self.kept, self.paused], self.data.state())
        before = self.data.files.summary.stat().st_mtime_ns
        summary.drop_unconfigured_summary_rows(self.data.files.summary, [self.kept, self.paused], self.data.state())
        self.assertEqual(self.data.files.summary.stat().st_mtime_ns, before)


if __name__ == "__main__":
    unittest.main()
