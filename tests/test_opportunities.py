import json
import time
import unittest
from decimal import Decimal

from support import APP_PATH, TempData  # noqa: F401

from hermes.database import Database
from hermes.web import opportunities


class OpportunityHistoryTests(unittest.TestCase):
    def setUp(self):
        self.data = TempData()

    def tearDown(self):
        self.data.cleanup()

    def put(self, identity, payload, state="sent", ago_days=0):
        stamp = time.time() - ago_days * 86400
        Database.at(self.data.files.database).transaction([(
            "INSERT INTO outbox(id,payload,state,due,created,delivered) VALUES (?,?,?,?,?,?)",
            (identity, json.dumps(payload, ensure_ascii=False), state, 0, stamp, stamp if state == "sent" else None))])

    def test_only_sent_product_alerts_in_the_last_30_days_newest_first(self):
        alert = {"title": "Amazon fiyat alarmı", "message": "Site: Amazon\nKulaklık\nGüncel fiyat: 900 TL", "url": "https://a/1",
                 "price": "900", "target": "1000"}
        self.put("old", {**alert, "url": "https://a/old"}, ago_days=40)
        self.put("new", alert, ago_days=1)
        self.put("newest", {**alert, "kind": "low90", "url": "https://a/2"}, ago_days=0)
        self.put("failed", alert, state="failed")
        self.put("system", {"title": "Hermes günlük özet", "message": "özet"})
        self.put("telegram", {"title": "Kanal", "message": "m", "url": "https://t/1", "telegram": ["c", "k", "u", "t"]})
        items = opportunities.read_sent(self.data.files.database)
        self.assertEqual([item.url for item in items], ["https://a/2", "https://a/1"])
        self.assertEqual((items[0].kind, items[1].kind), ("low90", "target"))
        self.assertEqual(items[1].product, "Kulaklık")
        self.assertEqual(items[1].price, Decimal("900"))

    def test_status_compares_with_the_current_price(self):
        item = opportunities.SentOpportunity(__import__("datetime").datetime.now().astimezone(), "t", "p", "u", Decimal("900"), Decimal("1000"), "target")
        self.assertIn("hâlâ hedefte", opportunities.status_text(item, {("u", False): Decimal("950")}))
        self.assertIn("yükseldi", opportunities.status_text(item, {("u", False): Decimal("1200")}))
        self.assertIn("listede yok", opportunities.status_text(item, {}))
        self.assertIn("Son 30 günde", opportunities.render_rows([], {}))

    def test_duration_lasts_until_the_price_rises_above_the_target(self):
        from datetime import datetime, timedelta
        from hermes.history import History
        now = datetime.now().astimezone()
        history = History.at(self.data.files.database)
        history.record_price("k1", "amazon", "Kulaklık", Decimal("1200"), now - timedelta(hours=5))
        history.record_price("k1", "amazon", "Kulaklık", Decimal("900"), now - timedelta(hours=3))
        history.record_price("k1", "amazon", "Kulaklık", Decimal("950"), now - timedelta(hours=2))
        history.record_price("k1", "amazon", "Kulaklık", Decimal("1100"), now - timedelta(minutes=105))
        history.record_price("k2", "trendyol", "Saat", Decimal("500"), now - timedelta(minutes=50))

        def sent(key, price):
            at = now - timedelta(minutes=40) if key == "k2" else now - timedelta(hours=3) + timedelta(seconds=5)
            return opportunities.SentOpportunity(at, "t", "p", "u", Decimal(price), Decimal("1000"), "target", key)
        history.record_price("k3", "amazon", "Depo", Decimal("700"), now - timedelta(hours=4))
        current = {("u", False): Decimal("950")}
        state = {"k3": {"last_price_checked_at": (now - timedelta(hours=1)).isoformat()}}
        ended, ongoing, unknown = opportunities.add_durations([sent("k1", "900"), sent("k2", "500"), sent("none", "1")],
                                                             current, self.data.files.database, state, now)
        gone = opportunities.add_durations([opportunities.SentOpportunity(now - timedelta(hours=4) + timedelta(seconds=5), "t", "p", "u",
                                            Decimal("700"), Decimal("1000"), "target", "k3", warehouse=True)],
                                           current, self.data.files.database, state, now)[0]
        self.assertEqual((gone.ongoing, gone.at_least), (False, True))
        self.assertAlmostEqual(gone.held.total_seconds(), 3 * 3600, delta=1)
        self.assertIn("en az 3 sa", opportunities.held_html(gone))
        self.assertEqual((ended.held, ended.ongoing), (timedelta(minutes=75), False))
        self.assertTrue(ongoing.ongoing)
        self.assertAlmostEqual(ongoing.held.total_seconds(), 3000, delta=1)
        self.assertIsNone(unknown.held)
        self.assertEqual(opportunities.duration_label(timedelta(minutes=75)), "1 sa 15 dk")
        self.assertEqual(opportunities.duration_label(timedelta(days=2, hours=3, minutes=4)), "2 gün 3 sa")

    def test_site_and_seller_come_from_the_alert(self):
        self.put("a", {"title": "Amazon Depo fırsatı", "message": "Site: Amazon\nKulaklık", "url": "https://a/1", "price": "900",
                       "target": "1000", "offer_key": "k", "opportunity": [{}, {"seller": "Amazon Depo", "is_warehouse": True}, "K", "u"]})
        item = opportunities.read_sent(self.data.files.database)[0]
        self.assertEqual((item.site, item.seller, item.warehouse, item.offer_key), ("Amazon", "Amazon Depo", True, "k"))
        html = opportunities.render_rows([item], {})
        self.assertIn("site-amazon", html)
        self.assertIn("DEPO", html)
        self.assertIn("satıcı Amazon Depo", html)


if __name__ == "__main__":
    unittest.main()
