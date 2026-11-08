"""Each site's app icon, shown on the product cards instead of a letter.

The icons ship with Hermes (web/static/site-icons/<site>.png, 156 px, the
sites' iOS app icons chosen by Nuri; Togg is drawn from its site icon in the
same shape). Nothing is fetched at run time; a new icon is a new file here.
A site without a file keeps its letter.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

from .constants import APP_VERSION

ICON_DIR = Path(__file__).parent / "web" / "static" / "site-icons"
_SITE_KEY = re.compile(r"^[a-z0-9]+$")


@lru_cache(maxsize=None)
def icon_png(site: str) -> Optional[bytes]:
    if not _SITE_KEY.match(site or ""):
        return None
    try:
        return (ICON_DIR / f"{site}.png").read_bytes()
    except OSError:
        return None


def has_icon(site: str) -> bool:
    return icon_png(site) is not None


def icon_version() -> str:
    """Icons change only with a release, so the version refreshes browser caches."""
    return APP_VERSION
