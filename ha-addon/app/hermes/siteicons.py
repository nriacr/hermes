"""Each site's own browser-tab icon, shown on the product cards instead of a letter.

The icon is read at the start of the site's own queue (one site, one sequential
reader), at most weekly, through the same connection Hermes already uses for
that site. It is cleaned once here: the flat background is removed, a logo that
would vanish on the dark card box is painted in the site's colour, and every
logo is scaled to the same visual size on a fixed transparent canvas.
A site whose icon cannot be read keeps its letter.
"""

from __future__ import annotations

import io
import json
import os
import re
import threading
import time
from collections import Counter, deque
from pathlib import Path
from typing import Iterable, List, Optional, Tuple
from urllib.parse import urljoin

from .constants import DATA_DIR
from .logging_utils import log

ICON_DIR = DATA_DIR / "site_icons"
STATE_FILE = "state.json"
REFRESH_SECONDS = 7 * 24 * 3600
RETRY_SECONDS = 24 * 3600
MAX_DOWNLOADS = 4
MAX_BYTES = 512 * 1024
GOOD_SIZE = 96

# Card box and site colours (same values as overview.css).
BOX_RGB = (15, 13, 24)
SITE_COLORS = {
    "amazon": "#2f9bff", "hepsiburada": "#ff7a1a", "trendyol": "#9a7bff", "network": "#19d3d3",
    "beymenclub": "#d4b896", "zara": "#e8ebf5", "hm": "#e56bff", "togg": "#8d9bb5",
    "ikea": "#b9785a", "mediamarkt": "#e87f86",
}

CANVAS = 156          # 3x the 52 px card box
TARGET_AREA = 78 * 78  # equal visual weight for square and wide logos
MAX_W, MAX_H = 118, 96
_SITE_KEY = re.compile(r"^[a-z0-9]+$")
_lock = threading.Lock()


# -- reading -------------------------------------------------------------------


def _state_path(directory: Path) -> Path:
    return directory / STATE_FILE


def _load_state(directory: Path) -> dict:
    try:
        value = json.loads(_state_path(directory).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def is_due(site: str, now: Optional[float] = None, directory: Path = ICON_DIR) -> bool:
    entry = _load_state(directory).get(site) or {}
    checked = float(entry.get("checked") or 0)
    wait = REFRESH_SECONDS if entry.get("ok") else RETRY_SECONDS
    return (now if now is not None else time.time()) - checked >= wait


def icon_candidates(html: str, page_url: str) -> List[str]:
    """Icon links of a page, largest first, then the classic /favicon.ico."""
    from bs4 import BeautifulSoup

    scored: List[Tuple[int, str]] = []
    for tag in BeautifulSoup(html or "", "html.parser").find_all("link"):
        rel = " ".join(tag.get("rel") or []).lower()
        href = str(tag.get("href") or "").strip()
        if "icon" not in rel or not href or href.startswith("data:") or href.lower().split("?")[0].endswith(".svg"):
            continue
        sizes = [int(n) for n in re.findall(r"(\d+)x\d+", str(tag.get("sizes") or ""))]
        size = max(sizes) if sizes else (180 if "apple-touch" in rel else 32)
        scored.append((size, urljoin(page_url, href)))
    urls = [url for _size, url in sorted(scored, key=lambda item: -item[0])]
    urls.append(urljoin(page_url, "/favicon.ico"))
    return list(dict.fromkeys(urls))


def _plain_get(session, url: str, timeout: int):
    response = session.get(url, timeout=timeout, allow_redirects=True)
    return response.status_code, response.content


def _site_client_get(url: str, timeout: int):
    """The same browser-compatible client the site readers already use."""
    from curl_cffi import requests as curl_requests

    from .traffic import count_curl

    response = count_curl(curl_requests.get(url, timeout=timeout, allow_redirects=True, impersonate="chrome124"))
    return response.status_code, response.content


def _get(session, url: str, timeout: int, fallback=_site_client_get, accept=lambda body: True) -> Optional[bytes]:
    """Plain request first; the site client only when the plain answer is refused or unusable."""
    for client in (lambda: _plain_get(session, url, timeout), lambda: fallback(url, timeout)):
        try:
            status, body = client()
        except Exception:  # noqa: BLE001 - one failed client tries the next
            continue
        if status == 200 and body and len(body) <= MAX_BYTES * 8 and accept(body):
            return body
    return None


def decode_icon(raw: bytes):
    """A Pillow RGBA image of the largest size the file holds, or None."""
    from PIL import Image

    if not raw or len(raw) > MAX_BYTES or raw.lstrip()[:1] == b"<":
        return None
    try:
        image = Image.open(io.BytesIO(raw))
        if image.format == "ICO":
            image.size = max(image.ico.sizes())
        image.load()
        return image.convert("RGBA")
    except Exception:  # noqa: BLE001 - anything unreadable is simply not an icon
        return None


# -- cleaning ------------------------------------------------------------------


def _border(width: int, height: int) -> Iterable[Tuple[int, int]]:
    for x in range(width):
        yield x, 0
        yield x, height - 1
    for y in range(height):
        yield 0, y
        yield width - 1, y


def remove_background(image, tolerance: int = 60) -> None:
    """Clear the flat colour that surrounds the logo, softening its edge."""
    px = image.load()
    width, height = image.size
    colours = Counter(px[point][:3] for point in _border(width, height) if px[point][3] > 128)
    if not colours:
        return
    reference, count = colours.most_common(1)[0]
    if count < 0.5 * sum(colours.values()):  # no single background colour: leave the logo whole
        return

    def distance(pixel) -> int:
        return sum(abs(pixel[i] - reference[i]) for i in range(3))

    queue = deque(_border(width, height))
    seen = set()
    while queue:
        x, y = queue.popleft()
        if (x, y) in seen or not (0 <= x < width and 0 <= y < height):
            continue
        pixel = px[x, y]
        gap = distance(pixel)
        if pixel[3] > 128 and gap > tolerance * 2:
            continue
        seen.add((x, y))
        if pixel[3] <= 128 or gap <= tolerance:
            px[x, y] = (0, 0, 0, 0)
            queue.extend(((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)))
            continue
        # Edge pixel: keep it part-transparent and take the background tint out of it.
        alpha = (gap - tolerance) / tolerance
        colour = tuple(max(0, min(255, round((pixel[i] - (1 - alpha) * reference[i]) / alpha))) for i in range(3))
        px[x, y] = (*colour, round(pixel[3] * alpha))


def _luminance(rgb) -> float:
    def channel(value: int) -> float:
        value /= 255
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4
    return 0.2126 * channel(rgb[0]) + 0.7152 * channel(rgb[1]) + 0.0722 * channel(rgb[2])


def readable_share(image) -> float:
    """Share of the logo's visible pixels that stand out (contrast >= 3) on the card box."""
    box = _luminance(BOX_RGB)
    visible = [p for p in image.getdata() if p[3] > 128]
    if not visible:
        return 0.0
    good = sum((max(_luminance(p), box) + 0.05) / (min(_luminance(p), box) + 0.05) >= 3 for p in visible)
    return good / len(visible)


def _hex_rgb(value: str) -> Tuple[int, int, int]:
    return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))


def clean_icon(image, colour: str) -> Optional[bytes]:
    """The finished card icon as PNG bytes, or None when nothing of the logo is left."""
    from PIL import Image, ImageFilter

    image = image.copy()
    remove_background(image)
    box = image.getbbox()
    if not box:
        return None
    image = image.crop(box)
    if readable_share(image) < 0.3:
        rgb = _hex_rgb(colour)
        image.putdata([(*rgb, p[3]) for p in image.getdata()])
    width, height = image.size
    scale = min((TARGET_AREA / (width * height)) ** 0.5, MAX_W / width, MAX_H / height)
    size = (max(1, round(width * scale)), max(1, round(height * scale)))
    resized = image.resize(size, Image.LANCZOS)
    if scale > 3:
        resized = resized.filter(ImageFilter.UnsharpMask(1.5, 80, 2))
    canvas = Image.new("RGBA", (CANVAS, CANVAS))
    canvas.alpha_composite(resized, ((CANVAS - size[0]) // 2, (CANVAS - size[1]) // 2))
    out = io.BytesIO()
    canvas.save(out, "PNG", optimize=True)
    return out.getvalue()


# -- refresh and lookup --------------------------------------------------------


def fetch_best_icon(session, home_url: str, timeout: int, fallback=_site_client_get, icon_url: str = ""):
    """(image, source url) of the largest readable icon, at most MAX_DOWNLOADS requests.

    A provider that names its icon file (icon_url) skips the main page read.
    """
    if icon_url:
        candidates = [icon_url]
    else:
        page = _get(session, home_url, timeout, fallback)
        candidates = icon_candidates(page.decode("utf-8", "replace") if page else "", home_url)
    best, source = None, ""
    for downloads, url in enumerate(candidates, start=1 if icon_url else 2):
        image = decode_icon(_get(session, url, timeout, fallback, lambda body: decode_icon(body) is not None) or b"")
        if image is not None and (best is None or min(image.size) > min(best.size)):
            best, source = image, url
        if (best is not None and min(best.size) >= GOOD_SIZE) or downloads >= MAX_DOWNLOADS:
            break
    return best, source


def refresh(site: str, home_url: str, session, timeout: int, directory: Path = ICON_DIR,
            fallback=_site_client_get, now: Optional[float] = None, icon_url: str = "") -> bool:
    """Read and store the site's icon; True when a new icon was saved."""
    if not _SITE_KEY.match(site or "") or not (home_url or icon_url):
        return False
    image, source = fetch_best_icon(session, home_url, timeout, fallback, icon_url)
    data = clean_icon(image, SITE_COLORS.get(site, "#a7a3c8")) if image is not None else None
    with _lock:
        if data:
            _write_atomic(directory / f"{site}.png", data)
        state = _load_state(directory)
        state[site] = {"checked": now if now is not None else time.time(), "ok": bool(data), "source": source,
                       "size": list(image.size) if image is not None else None}
        _write_atomic(_state_path(directory), json.dumps(state, ensure_ascii=False, indent=1).encode("utf-8"))
    log(f"{site}: site ikonu {'alındı' if data else 'alınamadı, harf kalıyor'}")
    return bool(data)


def refresh_if_due(provider, timeout: int, directory: Path = ICON_DIR) -> None:
    """Called at the start of a site's queue; never disturbs the price reads."""
    site = getattr(provider, "site", "")
    home_url = getattr(provider, "home_url", "")
    icon_url = getattr(provider, "icon_url", "")
    if not (home_url or icon_url) or not is_due(site, directory=directory):
        return
    from .traffic import CountedSession

    try:
        with CountedSession() as session:
            refresh(site, home_url, session, timeout, directory, icon_url=icon_url)
    except Exception as exc:  # noqa: BLE001 - an icon must never stop a price round
        log(f"{site}: site ikonu okunamadı ({type(exc).__name__})")
        with _lock:
            state = _load_state(directory)
            state[site] = {"checked": time.time(), "ok": False}
            _write_atomic(_state_path(directory), json.dumps(state).encode("utf-8"))


def icon_version(site: str, directory: Optional[Path] = None) -> int:
    """The stored icon's change time (0 when the site has none); used to refresh browser caches."""
    if not _SITE_KEY.match(site or ""):
        return 0
    try:
        return int(((directory or ICON_DIR) / f"{site}.png").stat().st_mtime)
    except OSError:
        return 0


def icon_png(site: str, directory: Optional[Path] = None) -> Optional[bytes]:
    if not _SITE_KEY.match(site or ""):
        return None
    try:
        return ((directory or ICON_DIR) / f"{site}.png").read_bytes()
    except OSError:
        return None
