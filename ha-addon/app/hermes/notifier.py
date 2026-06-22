import requests

from .constants import PUSHOVER_URL


def send_pushover(
    session: requests.Session,
    user_key: str,
    api_token: str,
    title: str,
    message: str,
    url: str,
    timeout: int,
    url_title: str = "Ürünü aç",
) -> None:
    data = {
        "token": api_token,
        "user": user_key,
        "title": title,
        "message": message,
        "url": url,
        "url_title": url_title,
        "priority": "0",
        "sound": "pushover",
    }
    response = session.post(PUSHOVER_URL, data=data, timeout=timeout)
    response.raise_for_status()
