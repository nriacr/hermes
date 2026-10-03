"""Page shell shared by every screen: head, navigation and notices."""

from html import escape
from typing import Optional

from ..constants import APP_VERSION
from .icons import render_web_app_head

NAV_ITEMS = (
    ("dashboard", "", "Özet Tablo"),
    ("statistics", "statistics", "İstatistik"),
    ("link-test", "link-test", "Test"),
    ("settings", "settings", "Ayarlar"),
)


def link(base: str, target: str = "") -> str:
    """Address of a page below the surface base ("." for ingress, "/public/<token>" for public)."""
    base = str(base or ".").rstrip("/") or "."
    return f"{base}/{target}" if target else f"{base}/"


def render_nav(base: str, current: str) -> str:
    current_marker = " aria-current='page'"
    items = "".join(
        f"<a class='button secondary' href='{escape(link(base, target), quote=True)}'"
        f"{current_marker if key == current else ''}>{escape(label)}</a>"
        for key, target, label in NAV_ITEMS
    )
    return f"<nav class='actions nav-actions' aria-label='Hermes sayfaları'>{items}</nav>"


def render_notice(status: str, message: str) -> str:
    if status not in {"ok", "fail"} or not message:
        return ""
    return f"<p class='notice {'notice-ok' if status == 'ok' else 'notice-fail'}'>{escape(message)}</p>"


def render_page(base: str, current: str, title: str, body: str, *, body_class: str = "public",
                refresh_seconds: Optional[int] = None, scripts: str = "", after_main: str = "") -> bytes:
    refresh = f"<meta http-equiv='refresh' content='{int(refresh_seconds)}'>" if refresh_seconds else ""
    html = (
        "<!doctype html><html lang='tr'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1, viewport-fit=cover'>"
        "<meta name='theme-color' content='#111315'><meta name='apple-mobile-web-app-capable' content='yes'>"
        "<meta name='apple-mobile-web-app-title' content='Hermes'>"
        f"{render_web_app_head(base)}{refresh}<title>{escape(title)}</title>"
        f"<link rel='stylesheet' href='{escape(link(base, 'app.css'), quote=True)}?v={escape(APP_VERSION)}'></head>"
        f"<body class='{escape(body_class, quote=True)}'><main><div class='hero'><div class='badge'>Hermes</div>"
        f"{render_nav(base, current)}{body}</div></main>{after_main}{scripts}</body></html>"
    )
    return html.encode("utf-8")
