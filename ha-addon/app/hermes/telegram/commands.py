"""Saved Messages commands: /durum, /duraklat <site> [saat], /devam <site>, /yardim.

Only messages the account owner writes to their own Saved Messages reach this module, so it needs no
sign-in of its own. A pause is temporary (default 6 hours, at most 72) and never touches watches or history.
"""

import re
from typing import Optional

from .. import sitepause
from ..constants import APP_VERSION, DATABASE_PATH, SITE_LABELS, SUMMARY_PATH
from ..monitor import daily
from ..storage import load_json
from ..utils import format_local_datetime

HELP = ("Hermes komutları:\n/durum – son 24 saatin özeti ve molalar\n/duraklat <site> [saat] – siteyi geçici durdur "
        f"(varsayılan {sitepause.DEFAULT_HOURS} saat, en çok {sitepause.MAX_HOURS})\n/devam <site> – molayı bitir\n"
        "Siteler: " + ", ".join(SITE_LABELS.values()))


def _normal(text: str) -> str:
    return re.sub(r"[^a-z0-9ğüşıöç]", "", str(text).casefold().replace("&", ""))


def resolve_site(text: str) -> Optional[str]:
    wanted = _normal(text)
    if not wanted:
        return None
    for site, label in SITE_LABELS.items():
        if wanted in (_normal(site), _normal(label)):
            return site
    return None


def reply_for(text: str) -> Optional[str]:
    """The answer to a command message, or None when the text is not a Hermes command."""
    parts = str(text or "").strip().split()
    if not parts or not parts[0].startswith("/"):
        return None
    command, args = parts[0].casefold().split("@", 1)[0], parts[1:]
    if command in {"/yardim", "/yardım", "/help"}:
        return HELP
    if command == "/durum":
        lines = [f"Hermes {APP_VERSION} çalışıyor."]
        summary = daily.build_daily_summary(DATABASE_PATH, load_json(SUMMARY_PATH, {}))
        if summary:
            lines.append(summary)
        for site, until in sitepause.active_pauses().items():
            lines.append(f"Mola: {SITE_LABELS.get(site, site)} – {format_local_datetime(until.astimezone())} saatine kadar.")
        return "\n".join(lines)
    if command in {"/duraklat", "/devam"}:
        site = resolve_site(args[0]) if args else None
        if not site:
            return "Hangi site? Örnek: " + command + " amazon\nSiteler: " + ", ".join(SITE_LABELS.values())
        label = SITE_LABELS[site]
        if command == "/devam":
            return f"{label} molası bitti; okuma devam ediyor." if sitepause.resume(site) else f"{label} zaten molada değildi."
        hours = None
        if len(args) > 1:
            try:
                hours = float(args[1].replace(",", "."))
            except ValueError:
                return "Saat bir sayı olmalı. Örnek: /duraklat amazon 3"
        until = sitepause.pause(site, hours)
        return (f"{label} okuması {format_local_datetime(until.astimezone())} saatine kadar durduruldu "
                f"(kartlar son fiyatlarını korur). Erken bitirmek için: /devam {args[0]}")
    return "Bilinmeyen komut.\n" + HELP
