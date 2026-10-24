"""A durable cart queue independent of notification delivery and its retries."""
import hashlib
import json
import threading
import time

from .logging_utils import log


class CartQueue:
    def __init__(self, database, providers, watches):
        self.db = database
        self.providers = providers
        self.enabled = {(w.site, w.tracking_id) for w in watches if w.auto_add_to_cart and w.active}
        self.stop = threading.Event()
        self.thread = None

    @staticmethod
    def statement(site, tracking_id, payload):
        identity = hashlib.sha256(json.dumps([site, tracking_id, payload['asin'], payload['warehouse'], payload['price'], payload.get('appearance', '')],
                                            sort_keys=True).encode()).hexdigest()
        return ('INSERT OR IGNORE INTO cart_jobs(id,site,tracking_id,payload,state,created,updated) '
                "VALUES (?,?,?,?,'pending',?,?)",
                (identity, site, tracking_id, json.dumps(payload), time.time(), time.time()))

    def start(self):
        if self.enabled and self.thread is None:
            # An interrupted POST may have succeeded. Never automatically repeat it.
            self.db.transaction([("UPDATE cart_jobs SET state='uncertain',error='Yeniden başlatmada sonuç belirsiz' "
                                  "WHERE state='processing'", ())])
            self.thread = threading.Thread(target=self._run, name='hermes-cart', daemon=True)
            self.thread.start()

    def _run(self):
        while not self.stop.is_set():
            try:
                if not self.run_one():
                    self.stop.wait(0.25)
            except Exception:
                # Do not expose response HTML, credentials or signed form tokens.
                log('Sepet kuyruğu bekliyor; mevcut bildirim akışı etkilenmedi.')
                self.stop.wait(5)

    def run_one(self):
        with self.db.lock:
            row = self.db.connect().execute(
                "SELECT id,site,tracking_id,payload,created FROM cart_jobs WHERE state='pending' ORDER BY created LIMIT 1"
            ).fetchone()
        if not row:
            return False
        identity, site, tracking_id, raw, created = row
        if (site, tracking_id) not in self.enabled or time.time() - created > 60:
            self._finish(identity, 'expired', 'Eski veya devre dışı kart')
            return True
        self.db.transaction([("UPDATE cart_jobs SET state='processing',updated=? WHERE id=?",
                              (time.time(), identity))])
        try:
            result = self.providers[site].add_to_cart(json.loads(raw))
            self._finish(identity, 'succeeded', result)
            log('Sepet: ' + result)
        except Exception as exc:
            # Only provider-defined public codes are exposed; network exceptions may contain tokens.
            detail = getattr(exc, 'public_message', 'Sepete ekleme sonucu doğrulanamadı; otomatik tekrar yok')
            self._finish(identity, 'failed', detail)
            log('Sepet: ' + detail)
        return True

    def _finish(self, identity, state, detail):
        self.db.transaction([('UPDATE cart_jobs SET state=?,error=?,updated=? WHERE id=?',
                              (state, detail, time.time(), identity))])

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=2)
