"""Shared PIN entry and Home Assistant-only PIN configuration screens."""

from html import escape

from ..constants import APP_VERSION
from .pages import link, render_page

LOCK_ICON = ("<svg width='25' height='25' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='1.7' "
             "stroke-linecap='round' aria-hidden='true'><rect x='5' y='10' width='14' height='11' rx='3'/>"
             "<path d='M8 10V7a4 4 0 0 1 8 0v3M12 14v3'/></svg>")


def csrf_field(token: str) -> str:
    return f"<input type='hidden' name='csrf_token' value='{escape(token, quote=True)}'>"


def render_pin_page(base: str, csrf: str, *, configured: bool, trusted: bool = False,
                    error: str = "", expired: bool = False, retry_after: int = 0) -> bytes:
    intro = "Oturumun kilitlendi. Devam etmek için PIN’ini gir." if expired else "Devam etmek için 4 haneli PIN’ini gir."
    if not configured:
        intro = "PIN henüz kullanıma hazır değil. Home Assistant üzerinden PIN’ini belirle."
    message = f"Çok fazla deneme. {retry_after} saniye sonra tekrar dene." if retry_after else error
    disabled = " disabled" if not configured or retry_after else ""
    recovery = (f"<a href='{escape(link(base, 'auth/configure'), quote=True)}'>PIN’i Home Assistant üzerinden değiştir</a>"
                if trusted else "PIN’i unuttuysan Home Assistant üzerinden değiştirebilirsin.")
    body = (f"<section class='pin-stage'><div class='pin-card'><div class='lock-symbol'>{LOCK_ICON}</div>"
            f"<h1>Ayarlar kilitli</h1><p>{intro}</p>"
            f"<form id='pin-form' method='post' action='{escape(link(base, 'auth/unlock'), quote=True)}' "
            f"data-pin-path='{escape(link(base, 'auth/pin'), quote=True)}' data-retry-after='{retry_after}'>"
            f"{csrf_field(csrf)}<label class='pin-label' for='pin'>PIN</label><div class='pin-entry'>"
            "<div class='pin-slots' aria-hidden='true'><span></span><span></span><span></span><span></span></div>"
            f"<input id='pin' name='pin' type='password' inputmode='numeric' autocomplete='off' pattern='[0-9]{{4}}' "
            f"maxlength='4' required aria-describedby='pin-message'{disabled}></div>"
            f"<p class='pin-message' id='pin-message' role='status' aria-live='polite'>{escape(message)}</p>"
            "<button class='button unlock' id='unlock' type='submit' disabled>Kilidi aç</button></form>"
            f"<a class='home-link' href='{escape(link(base), quote=True)}'>Anasayfaya dön</a>"
            "<div class='pin-foot'><p>15 dakika işlem yapmazsan ayarlar otomatik kilitlenir.</p>"
            f"<p>{recovery}</p></div></div></section>")
    script = f"<script src='{escape(link(base, 'pin.js'), quote=True)}?v={APP_VERSION}' defer></script>"
    return render_page(base, "settings", "Hermes • PIN", body, body_class="public ov settings-page", scripts=script)


def render_configure_page(base: str, csrf: str, *, configured: bool, error: str = "") -> bytes:
    title = "PIN’i değiştir" if configured else "PIN belirle"
    message = f"<p class='pin-message' role='alert'>{escape(error)}</p>" if error else ""
    fields = "".join(
        f"<label class='pin-label' for='{name}'>{label}</label>"
        f"<input class='pin-config-input' id='{name}' name='{name}' type='password' inputmode='numeric' "
        "autocomplete='new-password' pattern='[0-9]{4}' minlength='4' maxlength='4' required>"
        for name, label in (("pin", "Yeni PIN"), ("pin_confirm", "Yeni PIN tekrar"))
    )
    body = (f"<section class='pin-stage'><div class='pin-card'><div class='lock-symbol'>{LOCK_ICON}</div>"
            f"<h1>{title}</h1><p>4 haneli PIN’ini bu cihazdan belirle.</p>{message}"
            f"<form method='post' action='{escape(link(base, 'auth/configure'), quote=True)}'>"
            f"{csrf_field(csrf)}{fields}<button class='button unlock' type='submit'>PIN’i kaydet</button></form>"
            f"<a class='home-link' href='{escape(link(base), quote=True)}'>Anasayfaya dön</a>"
            "<div class='pin-foot'><p>PIN değişince açık ayar oturumları kapanır. Takiplerin ve fiyat geçmişin korunur.</p>"
            "</div></div></section>")
    return render_page(base, "settings", "Hermes • PIN belirle", body, body_class="public ov settings-page")


def render_session_bar(base: str, trusted: bool = False) -> str:
    recovery = (f"<a class='button secondary' href='{escape(link(base, 'auth/configure'), quote=True)}'>PIN’i değiştir</a>"
                if trusted else "")
    return ("<div class='session-bar'><div><strong>Ayarlar açık</strong>"
            "<p>15 dakika işlem yapılmazsa otomatik kilitlenir.</p></div><div class='session-actions'>"
            f"{recovery}<form method='post' action='{escape(link(base, 'auth/lock'), quote=True)}'>"
            "<button class='button secondary' type='submit'>Kilitle</button></form></div></div>")
