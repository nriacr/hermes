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
        self.assertIn("hâlâ hedefte", opportunities.status_text(item, {"u": Decimal("950")}))
        self.assertIn("yükseldi", opportunities.status_text(item, {"u": Decimal("1200")}))
        self.assertIn("listede yok", opportunities.status_text(item, {}))
        self.assertIn("Son 30 günde", opportunities.render_rows([], {}))


if __name__ == "__main__":
    unittest.main()
