"""Durable, single-consumer notification delivery shared by watches and Telegram."""

import hashlib
import json
import threading
import time
from decimal import Decimal

from .database import Database
from .diagnostics import Diagnostics
from .logging_utils import log, redact

MAX_ATTEMPTS = 6
RETRY_SECONDS = (5, 15, 60, 180, 600, 1800)
MAX_OPPORTUNITY_AGE = 5 * 60


class DeliveryQueue:
    def __init__(self, path, transport, on_sent=None):
        self.db = Database.at(path)
        self.transport = transport
        self.on_sent = on_sent
        self.diagnostics = Diagnostics(path)
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._send_lock = threading.Lock()
        self.thread = None
        self.failed_since = None

    @property
    def configured(self):
        return self.transport.configured

    def enqueue(self, payload, identity=None, state=None, statements=()):
        now = time.time()
        identity = identity or hashlib.sha256(json.dumps(
            [payload, int(now // 3600)], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        statements = list(statements) + [("INSERT INTO outbox(id,payload,due,created) VALUES (?,?,?,?) "
                       "ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,state='pending',attempts=0,"
                       "due=excluded.due,created=excluded.created,error='' WHERE outbox.state='expired'",
                       (identity, json.dumps(payload, ensure_ascii=False), now, now))]
        if state is not None:
            statements.insert(0, ("INSERT OR REPLACE INTO snapshots VALUES (?,?)",
                                  ("state.json", json.dumps(state, ensure_ascii=False))))
        self.db.transaction(statements)
        self._wake.set()
        return identity

    def send(self, title, message, url="", url_title="Ürünü aç", *, event_id=None, metadata=None):
        self.enqueue({"title": title, "message": message, "url": url, "url_title": url_title,
                      **(metadata or {})}, event_id)

    def start(self):
        if self.thread is None:
            self.thread = threading.Thread(target=self._run, name="hermes-delivery", daemon=True)
            self.thread.start()

    def _run(self):
        while not self._stop.is_set():
            try:
                while not self._stop.is_set() and self.deliver_one():
                    pass
                self.failed_since = None
            except Exception as exc:  # noqa: BLE001 - queue remains durable, never lose the job
                if self.failed_since is None:
                    self.failed_since = time.monotonic()
                    log(f"Bildirim kuyruğu bekliyor: {type(exc).__name__}")
            self._wake.wait(1)
            self._wake.clear()

    def _valid(self, payload, created):
        key = payload.get("offer_key")
        if not key:
            return time.time() - created < 24 * 3600
        with self.db.lock:
            row = self.db.connect().execute("SELECT payload FROM snapshots WHERE name='state.json'").fetchone()
        state = json.loads(row[0]) if row else {}
        offer = state.get(key, {})
        if state.get("_meta", {}).get("notification_generation") != payload.get("generation"):
            return False
        if offer.get("last_error") or str(offer.get("last_price")) != payload.get("price"):
            return False
        checked = offer.get("last_price_checked_at", "")
        from .utils import parse_iso_datetime
        at = parse_iso_datetime(checked)
        return bool(at and time.time() - at.timestamp() <= MAX_OPPORTUNITY_AGE
                    and (payload.get("stock_return") or Decimal(payload["price"]) <= Decimal(payload["target"])))

    def deliver_one(self):
        with self._send_lock:
            if not self.configured:
                return False
            with self.db.lock:
                row = self.db.connect().execute(
                    "SELECT id,payload,attempts,created FROM outbox WHERE state='pending' AND due<=? "
                    "ORDER BY created,id LIMIT 1", (time.time(),)).fetchone()
            if row is None:
                return False
            identity, raw, attempts, created = row
            payload = json.loads(raw)
            if not self._valid(payload, created):
                self._complete(identity, payload, False, "expired")
                return True
            try:
                self.transport.send(payload["title"], payload["message"], payload.get("url", ""),
                                    url_title=payload.get("url_title", "Ürünü aç"))
            except Exception as exc:  # noqa: BLE001 - persist attempts, classify permanent errors
                status = getattr(getattr(exc, "response", None), "status_code", None)
                permanent = isinstance(status, int) and (status == 200 or 400 <= status < 500)
                attempts += 1
                stopped = permanent or attempts >= MAX_ATTEMPTS
                self.db.transaction([("UPDATE outbox SET attempts=?,state=?,due=?,error=? WHERE id=?",
                                      (attempts, "failed" if stopped else "pending",
                                       time.time() + RETRY_SECONDS[min(attempts - 1, len(RETRY_SECONDS) - 1)],
                                       redact(str(exc))[:500], identity))])
                self.diagnostics.incident("delivery", "send", type(exc).__name__,
                                          "Müdahale gerekli" if stopped else "Sınırlı yeniden deneme")
                if stopped:
                    self._complete(identity, payload, False, "failed")
                return True
            self._complete(identity, payload, True, "sent")
            with self.db.lock:
                failed = self.db.connect().execute("SELECT 1 FROM outbox WHERE state='failed' LIMIT 1").fetchone()
            if not failed:
                self.diagnostics.recover("delivery", "Pushover bildirimi kabul etti")
            if payload.get("telegram"):
                from .telegram.listener import _record_recent_notification
                channel, keyword, url, text = payload["telegram"]
                _record_recent_notification(channel, keyword, url, text)
            return True

    def _complete(self, identity, payload, delivered, status):
        if self.on_sent:
            self.on_sent(payload, delivered, identity, status)
        else:
            self.db.transaction([("UPDATE outbox SET state=?,delivered=? WHERE id=?",
                                  (status, time.time() if delivered else None, identity))])

    def retry_failed(self):
        self.db.transaction([("UPDATE outbox SET state='pending',attempts=0,due=? WHERE state='failed'", (time.time(),))])
        self._wake.set()

    def drain(self, max_seconds=25):
        deadline = time.monotonic() + max_seconds
        while time.monotonic() < deadline and self.deliver_one():
            pass

    def close(self):
        self._stop.set()
        self._wake.set()
        if self.thread is not None:
            self.thread.join(timeout=25)

    def prune(self):
        self.db.transaction([("DELETE FROM outbox WHERE state!='pending' AND created<?",
                              (time.time() - 30 * 86400,))])
