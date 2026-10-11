"""PIN verifier, bounded server sessions and restart-safe guessing limits.

No plaintext PIN, cookie or form data is persisted or logged. A missing or
unreadable verifier never enables management access.
"""

import hashlib
import hmac
import json
import math
import re
import secrets
import threading
import time
from dataclasses import dataclass
from http.cookies import CookieError, SimpleCookie
from pathlib import Path
from typing import Callable, Optional

from ..constants import DATA_DIR
from ..storage import save_json

ACCESS_PATH = DATA_DIR / "web_access.json"
COOKIE_NAME = "hermes_session"
IDLE_SECONDS = 15 * 60
CHALLENGE_SECONDS = 5 * 60
MAX_SESSIONS = 128
MAX_FAILURES = 5
LOCK_SECONDS = 60
HASH_ITERATIONS = 600_000
PIN_PATTERN = re.compile(r"[0-9]{4}\Z")


@dataclass
class Session:
    csrf: str
    scope: str
    authenticated: bool
    deadline: float


class WebAccess:
    def __init__(self, path: Optional[Path] = None, clock: Callable[[], float] = time.time):
        self.path = path or ACCESS_PATH
        self.clock = clock
        self.lock = threading.RLock()
        self.sessions: dict[str, Session] = {}
        self.state: dict = {}
        self.error = False
        try:
            if self.path.exists():
                if self.path.stat().st_size > 8192:
                    raise ValueError("oversized verifier")
                self.state = json.loads(self.path.read_text(encoding="utf-8"))
                self._validate_state()
        except (ValueError, TypeError, KeyError, OSError):
            self.error = True

    def _validate_state(self):
        state = self.state
        if not isinstance(state, dict):
            raise ValueError("invalid verifier")
        if state.get("version") != 1 or state.get("iterations") != HASH_ITERATIONS:
            raise ValueError("invalid verifier")
        if len(bytes.fromhex(state["salt"])) != 16 or len(bytes.fromhex(state["verifier"])) != 32:
            raise ValueError("invalid verifier")
        failures = state["failures"]
        if not isinstance(failures, list) or len(failures) > MAX_FAILURES:
            raise ValueError("invalid attempts")
        if any(not isinstance(value, (int, float)) or not math.isfinite(value) for value in failures):
            raise ValueError("invalid attempts")
        if not isinstance(state["locked_until"], (int, float)) or not math.isfinite(state["locked_until"]):
            raise ValueError("invalid lock")

    @property
    def configured(self) -> bool:
        return bool(self.state) and not self.error

    def _save(self, state: dict):
        save_json(self.path, state)
        self.state = state
        self.error = False

    def configure(self, pin: str) -> bool:
        if not PIN_PATTERN.fullmatch(pin):
            return False
        salt = secrets.token_bytes(16)
        verifier = hashlib.pbkdf2_hmac("sha256", pin.encode("ascii"), salt, HASH_ITERATIONS)
        with self.lock:
            self._save({"version": 1, "iterations": HASH_ITERATIONS, "salt": salt.hex(),
                        "verifier": verifier.hex(), "failures": [], "locked_until": 0})
            self.sessions.clear()
        return True

    def retry_after(self) -> int:
        with self.lock:
            if not self.configured:
                return 0
            return max(0, math.ceil(self.state.get("locked_until", 0) - self.clock()))

    def verify(self, pin: str) -> bool:
        with self.lock:
            if not self.configured or self.retry_after():
                return False
            now = self.clock()
            valid = False
            if PIN_PATTERN.fullmatch(pin):
                value = hashlib.pbkdf2_hmac("sha256", pin.encode("ascii"), bytes.fromhex(self.state["salt"]), HASH_ITERATIONS)
                valid = hmac.compare_digest(value.hex(), self.state["verifier"])
            state = dict(self.state)
            if valid:
                state.update(failures=[], locked_until=0)
            else:
                failures = [value for value in state["failures"] if now - value < LOCK_SECONDS]
                failures.append(now)
                state.update(failures=failures[-MAX_FAILURES:],
                             locked_until=now + LOCK_SECONDS if len(failures) >= MAX_FAILURES else 0)
            # Commit the attempt before granting access; an unwritable store fails closed.
            self._save(state)
            return valid

    @staticmethod
    def _key(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def cookie_token(header: str) -> str:
        try:
            cookies = SimpleCookie()
            cookies.load(header)
            return cookies[COOKIE_NAME].value if COOKIE_NAME in cookies else ""
        except CookieError:
            return ""

    def session(self, cookie: str, scope: str, *, touch: bool = False) -> Optional[Session]:
        with self.lock:
            key = self._key(self.cookie_token(cookie))
            session = self.sessions.get(key)
            if not session or session.scope != scope:
                return None
            if session.deadline <= self.clock():
                del self.sessions[key]
                return None
            if touch and session.authenticated:
                session.deadline = self.clock() + IDLE_SECONDS
            return session

    def create_session(self, scope: str, *, authenticated: bool = False) -> tuple[str, Session]:
        with self.lock:
            now = self.clock()
            self.sessions = {key: value for key, value in self.sessions.items() if value.deadline > now}
            if len(self.sessions) >= MAX_SESSIONS:
                # Anonymous traffic cannot evict an authenticated owner.
                anonymous = next((key for key, value in self.sessions.items() if not value.authenticated), None)
                if anonymous is None:
                    raise RuntimeError("session capacity")
                del self.sessions[anonymous]
            token = secrets.token_urlsafe(32)
            session = Session(secrets.token_urlsafe(32), scope, authenticated,
                              now + (IDLE_SECONDS if authenticated else CHALLENGE_SECONDS))
            self.sessions[self._key(token)] = session
            return token, session

    def discard(self, cookie: str):
        with self.lock:
            self.sessions.pop(self._key(self.cookie_token(cookie)), None)

    @staticmethod
    def valid_csrf(session: Optional[Session], value: str) -> bool:
        return bool(session and value and hmac.compare_digest(session.csrf.encode(), value.encode()))

    @staticmethod
    def cookie(token: str, path: str, secure: bool = False) -> str:
        return (f"{COOKIE_NAME}={token}; Path={path.rstrip('/')}/; HttpOnly; SameSite=Strict"
                + ("; Secure" if secure else "") + ("; Max-Age=0" if not token else ""))
