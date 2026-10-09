from typing import Optional

import requests

from .constants import DEFAULT_REQUEST_TIMEOUT_SECONDS, PUSHOVER_URL


class Pushover:
    """Pushover delivery for opportunity, stock, Telegram and warning messages."""

    def __init__(self, user_key: str, api_token: str, timeout: int = DEFAULT_REQUEST_TIMEOUT_SECONDS,
                 session: Optional[requests.Session] = None) -> None:
        self.user_key = str(user_key or "").strip()
        self.api_token = str(api_token or "").strip()
        self.timeout = timeout
        self.session = session or requests.Session()

    @property
    def configured(self) -> bool:
        return bool(self.user_key and self.api_token)

    def send(self, title: str, message: str, url: str = "", url_title: str = "Ürünü aç") -> None:
        response = self.session.post(
            PUSHOVER_URL,
            data={
                "token": self.api_token,
                "user": self.user_key,
                "title": title,
                "message": message,
                "url": url,
                "url_title": url_title,
                "priority": "0",
                "sound": "pushover",
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        if response.json().get("status") != 1:
            raise requests.HTTPError("Pushover bildirimi kabul etmedi.", response=response)

    def close(self):
        self.session.close()
