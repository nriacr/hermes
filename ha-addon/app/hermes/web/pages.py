"""Page shell shared by every screen: head, navigation and notices."""

from html import escape
from typing import Optional

from ..constants import APP_VERSION
from .icons import render_web_app_head

# The top bar carries only the logo (home) and the settings gear; İstatistik is
# reached from the button row at the bottom of the settings page.

GEAR_ICON = (
    "<svg viewBox='0 0 24 24' width='22' height='22' fill='none' stroke='currentColor' stroke-width='2' "
    "stroke-linecap='round' stroke-linejoin='round' aria-hidden='true'><circle cx='12' cy='12' r='3'/>"
    "<path d='M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 "
    "1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1"
    "-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 "
    "1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33h.01a1.65 1.65 0 0 0 "
    "1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51h.01a1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06"
    "a1.65 1.65 0 0 0-.33 1.82v.01a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z'/></svg>"
)


CONFIRM_SCRIPT = """<script>
document.querySelectorAll('form[data-confirm]').forEach((form) => {
  form.addEventListener('submit', (event) => {
    if (!window.confirm(form.getAttribute('data-confirm') || 'Bu işlemi yapmak istediğine emin misin?')) event.preventDefault();
  });
});
</script>"""


def link(base: str, target: str = "") -> str:
    """Address of a page below the surface base ("." for ingress, "/public/<token>" for public)."""
    base = str(base or ".").rstrip("/") or "."
    return f"{base}/{target}" if target else f"{base}/"


def render_topbar(base: str, current: str) -> str:
    gear_current = " aria-current='page'" if current == "settings" else ""
    return (
        f"<div class='topbar'><a class='badge' href='{escape(link(base), quote=True)}' aria-label='Özet Tablo'>Hermes</a>"
        f"<a class='gear-button' href='{escape(link(base, 'settings'), quote=True)}' aria-label='Ayarlar' "
        f"title='Ayarlar'{gear_current}>{GEAR_ICON}</a></div>"
    )


def render_tool_actions(base: str) -> str:
    """One row at the bottom of Ayarlar: Pushover test, the two confirmed resets, İstatistik and the confirmed restart."""
    return (
        "<div class='actions public-actions tool-actions'>"
        f"<form class='inline-form' method='post' action='{escape(link(base, 'test-pushover'), quote=True)}'>"
        "<button class='button test' type='submit'>Pushover testi</button></form>"
        f"<form class='inline-form' method='post' action='{escape(link(base, 'reset-notifications'), quote=True)}' "
        "data-confirm='Bildirim susturma hafızası sıfırlanacak ve hedef altında kalan fırsatlar için hemen yeni bir kontrol başlatılacak. Devam etmek istiyor musun?'>"
        "<button class='button secondary' type='submit'>Bildirim Sıfırla</button></form>"
        f"<form class='inline-form' method='post' action='{escape(link(base, 'reset-price-history'), quote=True)}' "
        "data-confirm='Min/maks fiyat geçmişi temizlenecek ve güncel fiyattan yeniden başlayacak. Devam etmek istiyor musun?'>"
        "<button class='button secondary' type='submit'>Min/Maks Sıfırla</button></form>"
        f"<a class='button secondary' href='{escape(link(base, 'statistics'), quote=True)}'>İstatistik</a>"
        f"<form class='inline-form' method='post' action='{escape(link(base, 'restart'), quote=True)}' "
        "data-confirm='Hermes, Home Assistant üzerinden uygulama olarak yeniden başlatılacak. Süren tarama yarıda kalır; "
        "ayarlar değişmez. Devam etmek istiyor musun?'>"
        "<button class='button secondary' type='submit'>Hermes'i yeniden başlat</button></form></div>"
    )


def render_notice(status: str, message: str) -> str:
    if status not in {"ok", "fail"} or not message:
        return ""
    return f"<p class='notice {'notice-ok' if status == 'ok' else 'notice-fail'}'>{escape(message)}</p>"


def render_page(base: str, current: str, title: str, body: str, *, body_class: str = "public",
                refresh_seconds: Optional[int] = None, scripts: str = "", after_main: str = "") -> bytes:
    # Pages with live data refresh in place; the full reload stays only as the
    # fallback for a browser without JavaScript.
    refresh = (f"<noscript><meta http-equiv='refresh' content='{int(refresh_seconds)}'></noscript>"
               if refresh_seconds else "")
    html = (
        "<!doctype html><html lang='tr'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1, viewport-fit=cover'>"
        "<meta name='theme-color' content='#111315'><meta name='apple-mobile-web-app-capable' content='yes'>"
        "<meta name='apple-mobile-web-app-title' content='Hermes'>"
        f"{render_web_app_head(base)}{refresh}<title>{escape(title)}</title>"
        f"<link rel='stylesheet' href='{escape(link(base, 'app.css'), quote=True)}?v={escape(APP_VERSION)}'></head>"
        f"<body class='{escape(body_class, quote=True)}'><main><div class='hero'>"
        f"{render_topbar(base, current)}{body}</div></main>{after_main}{scripts}</body></html>"
    )
    return html.encode("utf-8")
