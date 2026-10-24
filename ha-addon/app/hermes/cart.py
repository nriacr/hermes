"""A durable cart queue independent of notification delivery and its retries."""
import hashlib
import json
import threading
import time

from .logging_utils import log

CART_URL = 'https://www.amazon.com.tr/gp/cart/view.html'
# A job older than this is a missed moment, not a deal to chase.
MAX_JOB_AGE = 60
DAILY_LIMIT = 5
# A cart read every two hours keeps the Amazon session alive and finds a closed one early.
SESSION_CHECK_SECONDS = 2 * 3600
FIRST_SESSION_CHECK_SECONDS = 120


class CartQueue:
    def __init__(self, database, providers, watches, delivery=None):
        self.db = database
        self.providers = providers
        self.delivery = delivery
        self.enabled = {(w.site, w.tracking_id) for w in watches if w.auto_add_to_cart and w.active}
        self.stop = threading.Event()
        self.thread = None
        self.next_session_check = time.monotonic() + FIRST_SESSION_CHECK_SECONDS
        self.session_problem = ''

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
                if not self.run_one() and not self.check_session():
                    self.stop.wait(0.25)
            except Exception:
                # Do not expose response HTML, credentials or signed form tokens.
                log('Sepet kuyruğu bekliyor; mevcut bildirim akışı etkilenmedi.')
                self.stop.wait(5)

    def check_session(self, now=None):
        """Due session checks, one per site; a problem is announced once, until it clears."""
        now = time.monotonic() if now is None else now
        if now < self.next_session_check:
            return False
        self.next_session_check = now + SESSION_CHECK_SECONDS
        for site in sorted({site for site, _ in self.enabled}):
            try:
                self.providers[site].check_cart_session()
            except Exception as exc:  # noqa: BLE001 - only public provider messages are shown
                problem = getattr(exc, 'public_message', 'Hermes Tarayıcı oturumu doğrulanamadı')
                log(f'Sepet oturum kontrolü: {problem}')
                if problem != self.session_problem and self.delivery is not None:
                    self.delivery.send('Amazon oturumu sorunlu', problem + "\nOtomatik sepete ekleme bu sırada çalışmaz.",
                                       CART_URL, 'Sepeti aç')
                self.session_problem = problem
            else:
                if self.session_problem:
                    log('Sepet oturum kontrolü: oturum yeniden açık')
                self.session_problem = ''
        return True

    def _added_today(self):
        with self.db.lock:
            return self.db.connect().execute(
                "SELECT count(*) FROM cart_jobs WHERE state IN ('succeeded','partial') AND updated>?",
                (time.time() - 86400,)).fetchone()[0]

    def run_one(self):
        with self.db.lock:
            row = self.db.connect().execute(
                "SELECT id,site,tracking_id,payload,created FROM cart_jobs WHERE state='pending' ORDER BY created LIMIT 1"
            ).fetchone()
        if not row:
            return False
        identity, site, tracking_id, raw, created = row
        job = json.loads(raw)
        if (site, tracking_id) not in self.enabled or time.time() - created > MAX_JOB_AGE:
            self._finish(identity, 'expired', 'Eski veya devre dışı kart')
            return True
        if self._added_today() >= DAILY_LIMIT:
            self._finish(identity, 'skipped', 'Günlük otomatik sepet sınırı doldu')
            self._report(job, False, f'Son 24 saatte {DAILY_LIMIT} ürün eklendi; sınır doldu, sepet değişmedi.')
            return True
        self.db.transaction([("UPDATE cart_jobs SET state='processing',updated=? WHERE id=?",
                              (time.time(), identity))])
        started = time.monotonic()
        try:
            result = self.providers[site].add_to_cart(job)
            self._finish(identity, 'succeeded', result)
            log(f'Sepet: {result} | süre={time.monotonic() - started:.1f} sn')
        except Exception as exc:
            # Only provider-defined public codes are exposed; network exceptions may contain tokens.
            detail = getattr(exc, 'public_message', 'Sepete ekleme sonucu doğrulanamadı; otomatik tekrar yok')
            added = getattr(exc, 'added', False)
            self._finish(identity, 'partial' if added else 'failed', detail)
            log(f'Sepet: {detail} | süre={time.monotonic() - started:.1f} sn')
            self._report(job, added, detail)
        return True

    def _report(self, job, added, detail):
        """Only a problem is reported; a successful add needs no second notification (owner's rule)."""
        if self.delivery is None:
            return
        try:
            self.delivery.send('Sepete eklendi, ama dikkat' if added else 'Sepete eklenemedi',
                               f"{job.get('title') or job['asin']}\n{detail}", CART_URL, 'Sepeti aç')
        except Exception as exc:  # noqa: BLE001 - the cart result is already stored
            log(f'Sepet sonucu bildirilemedi: {type(exc).__name__}')

    def _finish(self, identity, state, detail):
        self.db.transaction([('UPDATE cart_jobs SET state=?,error=?,updated=? WHERE id=?',
                              (state, detail, time.time(), identity))])

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=2)
