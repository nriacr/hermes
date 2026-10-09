"""Stylesheet, fonts and scripts shared by every page of the ingress and public panel."""
from pathlib import Path
FONT_DIR = Path(__file__).with_name("fonts")
FONT_FILES = ("sora-latin.woff2", "sora-latin-ext.woff2", "inter-latin.woff2", "inter-latin-ext.woff2")
def read_font(name: str) -> bytes:
    """The Özet Tablo typefaces ship inside the add-on, so the panel needs no outside connection."""
    return (FONT_DIR / name).read_bytes()

STATIC_DIR = Path(__file__).with_name("static")

def _asset(name: str) -> str:
    return (STATIC_DIR / name).read_text(encoding="utf-8")

APP_CSS = "\n".join(_asset(name) for name in ("base.css", "overview.css", "settings.css", "statistics.css"))
SETTINGS_SCRIPT = _asset("settings.js")
RESTART_SCRIPT = _asset("restart.js")
LIVE_SCRIPT = _asset("live.js")
OVERVIEW_SCRIPT = _asset("overview.js")
