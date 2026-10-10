"""Statistics page: how often products are checked, per-site health, error types and error spells.

Everything comes from the `reads` table of `hermes.db` (one row per watch
read). The central number is the check frequency: the typical time between two
reads of the same high-priority watch. One period switch (24 hours / 7 days)
drives the whole page.
"""

import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from html import escape
from typing import Dict, List, Optional, Tuple

from ..constants import DATABASE_PATH, DEFAULT_PRIORITY, SUMMARY_PATH, STATE_PATH
from ..history import BLOCKED_OUTCOMES, SUCCESS_OUTCOMES, Read, read_reads, read_site_requests
from ..storage import load_json
from ..diagnostics import Diagnostics
from ..utils import SystemLoad, site_label
from .dashboard import compact_error_row, incident_errors, clean_error_message, duration_text, live_region, live_script_tag, relative_time_text, site_theme_class
from .pages import CONFIRM_SCRIPT, link, render_notice, render_page

PERIODS = {"24h": ("Son 24 saat", timedelta(hours=24)), "7d": ("Son 7 gün", timedelta(days=7))}
DEFAULT_PERIOD = "24h"
CURRENT = " aria-current='page'"
# A longer gap is a pause (restart, settings change), not the check rhythm.
MAX_CHECK_GAP = timedelta(hours=6)
# Failures of one site closer together than this are one spell ("hata dönemi").
SPELL_GAP = timedelta(minutes=15)
MAX_LISTED_SPELLS = 8
ERROR_LABELS = {
    "captcha": "Captcha (bot koruması)",
    "http_503": "503 · site meşgul",
    "http_429": "429 · çok fazla istek",
    "timeout": "Zaman aşımı",
    "connection": "Bağlantı hatası",
    "unreadable": "Sayfa okunamadı",
    "error": "Diğer",
    "partial": "Kısmen okundu",
    "interrupted": "Durduruldu",
}


def period_key(params: Dict[str, List[str]]) -> str:
    value = (params or {}).get("p", [DEFAULT_PERIOD])[0]
    return value if value in PERIODS else DEFAULT_PERIOD


def error_label(outcome: str) -> str:
    if outcome in ERROR_LABELS:
        return ERROR_LABELS[outcome]
    if outcome.startswith("http_"):
        return f"HTTP {outcome[5:]}"
    return ERROR_LABELS["error"]


def is_blocked(outcome: str) -> bool:
    return outcome in BLOCKED_OUTCOMES


def is_failure(outcome: str) -> bool:
    return outcome not in SUCCESS_OUTCOMES


# -- figures ----------------------------------------------------------------------------


@dataclass
class SiteFigures:
    site: str
    reads: int = 0
    ok: int = 0
    blocked: int = 0
    errors: int = 0
    partial: int = 0
    durations: List[int] = field(default_factory=list)
    gaps: List[float] = field(default_factory=list)
    last_read: Optional[datetime] = None

    @property
    def typical_ms(self) -> Optional[float]:
        return statistics.median(self.durations) if self.durations else None

    @property
    def check_seconds(self) -> Optional[float]:
        return statistics.median(self.gaps) if self.gaps else None


@dataclass
class Bucket:
    start: datetime
    reads: int = 0
    failures: int = 0
    gaps: List[float] = field(default_factory=list)

    @property
    def check_seconds(self) -> Optional[float]:
        return statistics.median(self.gaps) if self.gaps else None


def check_gaps(reads: List[Read], since: datetime) -> List[Tuple[Read, float]]:
    """(later read, seconds since the previous read of the same high-priority watch)."""
    previous: Dict[str, Read] = {}
    gaps = []
    for item in reads:
        # "high" rows were recorded before 3.12, when every-cycle watches were called high priority.
        if not item.watch_key or item.priority not in (DEFAULT_PRIORITY, "high"):
            continue
        before = previous.get(item.watch_key)
        previous[item.watch_key] = item
        if before and item.at >= since and item.at - before.at <= MAX_CHECK_GAP:
            gaps.append((item, (item.at - before.at).total_seconds()))
    return gaps


def site_figures(reads: List[Read], gaps: List[Tuple[Read, float]]) -> List[SiteFigures]:
    sites: Dict[str, SiteFigures] = {}
    for item in reads:
        figures = sites.setdefault(item.site, SiteFigures(item.site))
        figures.reads += 1
        figures.last_read = item.at
        if not is_failure(item.outcome):
            figures.ok += 1
            figures.durations.append(item.duration_ms)
        elif item.outcome == "partial":
            figures.partial += 1
        elif is_blocked(item.outcome):
            figures.blocked += 1
        else:
            figures.errors += 1
    for item, seconds in gaps:
        sites[item.site].gaps.append(seconds)
    return sorted(sites.values(), key=lambda figures: (-figures.reads, figures.site))


def buckets(reads: List[Read], gaps: List[Tuple[Read, float]], since: datetime, period: str) -> List[Bucket]:
    if period == "24h":
        first = since.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        result = [Bucket(first + timedelta(hours=index)) for index in range(24)]
        size = timedelta(hours=1)
    else:
        first = since.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        result = [Bucket(first + timedelta(days=index)) for index in range(7)]
        size = timedelta(days=1)

    def slot(moment: datetime) -> Optional[Bucket]:
        index = int((moment - first) // size) if moment >= first else -1
        return result[index] if 0 <= index < len(result) else None

    for item in reads:
        target = slot(item.at)
        if target:
            target.reads += 1
            target.failures += is_failure(item.outcome)
    for item, seconds in gaps:
        target = slot(item.at)
        if target:
            target.gaps.append(seconds)
    return result


# -- rendering --------------------------------------------------------------------------


def _short_duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return "-"
    if seconds < 90:
        return f"{round(seconds)} sn"
    if seconds < 90 * 60:
        return f"{round(seconds / 60)} dk"
    return f"{seconds / 3600:.1f} sa".replace(".", ",")


def _ms_text(value: Optional[float]) -> str:
    return "-" if value is None else f"{value / 1000:.1f} sn".replace(".", ",")


def _percent(part: int, whole: int) -> str:
    return (f"%{min(99.9, 100 * part / whole):.1f}".replace(".", ",") if part != whole else "%100") if whole else "-"


def render_period_switch(base: str, period: str) -> str:
    target = escape(link(base, "statistics"), quote=True)
    items = "".join(
        f"<a class='button secondary' href='{target}?p={key}'{CURRENT if key == period else ''}>{escape(label)}</a>"
        for key, (label, _span) in PERIODS.items()
    )
    return f"<nav class='actions period-switch' aria-label='Dönem'>{items}</nav>"


def render_tiles(reads: List[Read], gaps) -> str:
    summary = load_json(SUMMARY_PATH, {})
    summary = summary if isinstance(summary, dict) else {}
    failures = sum(is_failure(item.outcome) for item in reads)
    blocked = sum(is_blocked(item.outcome) for item in reads)
    partial = sum(item.outcome == "partial" for item in reads)
    check = statistics.median([seconds for _item, seconds in gaps]) if gaps else None
    last_cycle = relative_time_text(summary.get("checked_at"))
    cycle_length = duration_text(summary.get("cycle_duration_seconds"), "-")
    tiles = (
        ("Kontrol sıklığı", _short_duration(check), "yüksek öncelikli bir ürün tipik olarak bu aralıkla okunuyor"),
        ("Son tur", last_cycle, f"süresi {cycle_length}"),
        ("Başarı", _percent(len(reads) - failures, len(reads)), f"{len(reads)} okumadan {len(reads) - failures} başarılı"),
        ("Engel ve hata", str(failures), f"{partial} kısmi · {blocked} engel · {failures - blocked - partial} hata"),
    )
    alert = " stat-tile-alert" if failures else ""
    return "<div class='stat-tiles'>" + "".join(
        f"<section class='stat-tile{alert if index == 3 else ''}'><span>{escape(title)}</span><strong>{escape(value)}</strong>"
        f"<small>{escape(note)}</small></section>"
        for index, (title, value, note) in enumerate(tiles)
    ) + "</div>"


def render_chart(slots: List[Bucket], period: str) -> str:
    values = [slot.check_seconds for slot in slots]
    if not any(value is not None for value in values):
        return ("<p class='statistics-empty'>Bu dönem için henüz kontrol sıklığı ölçümü yok. "
                "Yüksek öncelikli ürünler iki kez okunduktan sonra grafik dolmaya başlar.</p>")
    top = max(60.0, *(value for value in values if value is not None))
    width, left, bottom, height = 1000, 54, 196, 160
    step = (width - left - 10) / len(slots)
    parts = []
    for fraction in (1, 0.5):
        y = bottom - height * fraction
        parts.append(f"<line class='grid-line' x1='{left}' x2='{width - 10}' y1='{y:.0f}' y2='{y:.0f}'/>"
                     f"<text x='{left - 8}' y='{y + 4:.0f}' text-anchor='end'>{escape(_short_duration(top * fraction))}</text>")
    parts.append(f"<line class='axis-line' x1='{left}' x2='{width - 10}' y1='{bottom}' y2='{bottom}'/>")
    for index, slot in enumerate(slots):
        x = left + index * step
        label = slot.start.strftime("%H:%M") if period == "24h" else slot.start.strftime("%d.%m")
        moment = slot.start.strftime("%H:00") if period == "24h" else slot.start.strftime("%d.%m.%Y")
        tip = (f"{moment}: kontrol sıklığı {_short_duration(slot.check_seconds)}, {slot.reads} okuma, "
               f"{slot.failures} engel/hata")
        if slot.check_seconds is not None:
            bar = max(2.0, height * slot.check_seconds / top)
            parts.append(f"<rect class='check-bar' x='{x + step * 0.18:.1f}' y='{bottom - bar:.1f}' width='{step * 0.64:.1f}' "
                         f"height='{bar:.1f}' rx='3'><title>{escape(tip)}</title></rect>")
        if slot.failures:
            parts.append(f"<text class='failure-count' x='{x + step / 2:.1f}' y='{bottom - height - 10}' text-anchor='middle'>"
                         f"{slot.failures}<title>{escape(tip)}</title></text>")
        if period == "7d" or index % 3 == 0:
            parts.append(f"<text x='{x + step / 2:.1f}' y='{bottom + 20}' text-anchor='middle'>{escape(label)}</text>")
    legend = ("<p class='chart-legend'><span class='legend-item'><span class='legend-bar'></span>Kontrol sıklığı: çubuk ne kadar "
              "kısaysa ürünler o kadar sık okunuyor</span><span class='legend-item'><span class='legend-count'>3</span>O "
              f"{'saatteki' if period == '24h' else 'gündeki'} engel ve hata sayısı</span></p>")
    return (f"<div class='chart-wrap'><svg class='statistics-chart' viewBox='0 0 {width} 226' role='img' "
            f"aria-label='Kontrol sıklığı, {'saat' if period == '24h' else 'gün'} bazında'>{''.join(parts)}</svg></div>{legend}")


def render_health_bar(figures: SiteFigures) -> str:
    segments = ((figures.ok, "ok", "başarılı"), (figures.blocked, "blocked", "engel"), (figures.errors, "error", "hata"), (figures.partial, "partial", "kısmi"))
    bars = "".join(f"<i class='health-{css}' style='flex-grow:{count}' title='{count} {label}'></i>"
                   for count, css, label in segments if count)
    return f"<div class='health-bar' aria-hidden='true'>{bars}</div>"


def render_sites(figures_list: List[SiteFigures], requests_by_site: Dict[str, str]) -> str:
    if not figures_list:
        return "<p class='statistics-empty'>Bu dönemde okuma yok.</p>"
    cards = []
    for figures in figures_list:
        extra = requests_by_site.get(figures.site, "")
        note = f"<p class='site-note'>{escape(extra)}</p>" if extra else ""
        cards.append(
            f"<article class='site-health {site_theme_class(site_label(figures.site))}'>"
            f"<header><strong>{escape(site_label(figures.site))}</strong>"
            f"<span>{figures.reads} okuma · {_percent(figures.ok, figures.reads)} başarılı</span></header>"
            f"{render_health_bar(figures)}"
            "<dl>"
            f"<div><dt>Kontrol sıklığı</dt><dd>{escape(_short_duration(figures.check_seconds))}</dd></div>"
            f"<div><dt>Okuma süresi</dt><dd>{escape(_ms_text(figures.typical_ms))}</dd></div>"
            f"<div><dt>Engel</dt><dd class='{'bad' if figures.blocked else 'zero'}'>{figures.blocked}</dd></div>"
            f"<div><dt>Kısmi okuma</dt><dd>{figures.partial}</dd></div>"
            f"<div><dt>Hata</dt><dd class='{'bad' if figures.errors else 'zero'}'>{figures.errors}</dd></div>"
            f"<div><dt>Son okuma</dt><dd>{escape(relative_time_text(figures.last_read.isoformat()) if figures.last_read else '-')}</dd></div>"
            "</dl>"
            f"{note}"
            "</article>"
        )
    return "<div class='site-health-list'>" + "".join(cards) + "</div>"


def render_error_types(reads: List[Read]) -> str:
    failures = [item for item in reads if is_failure(item.outcome)]
    if not failures:
        return "<p class='statistics-empty statistics-good'>Bu dönemde engel veya hata yok.</p>"
    sites = sorted({item.site for item in failures}, key=lambda site: site_label(site))
    table: Dict[str, Dict[str, int]] = {}
    last_seen: Dict[str, datetime] = {}
    for item in failures:
        label = error_label(item.outcome)
        table.setdefault(label, {}).setdefault(item.site, 0)
        table[label][item.site] += 1
        last_seen[label] = item.at
    order = list(dict.fromkeys(ERROR_LABELS.values()))
    labels = sorted(table, key=lambda label: (order.index(label) if label in order else len(order), label))
    head = "".join(f"<th>{escape(site_label(site))}</th>" for site in sites)
    rows = "".join(
        f"<tr><td>{escape(label)}</td>"
        + "".join(f"<td class='{'bad' if table[label].get(site) else 'zero'}'>{table[label].get(site, 0)}</td>" for site in sites)
        + f"<td>{escape(relative_time_text(last_seen[label].isoformat()))}</td></tr>"
        for label in labels
    )
    return ("<div class='table-wrap measure-wrap'><table class='statistics-table measure-table'>"
            f"<thead><tr><th>Tür</th>{head}<th>En son</th></tr></thead><tbody>{rows}</tbody></table></div>")


@dataclass
class Spell:
    """Consecutive failures of one site: one row instead of one per read."""

    site: str
    start: datetime
    end: datetime
    labels: Counter = field(default_factory=Counter)
    details: Counter = field(default_factory=Counter)
    cpu: List[int] = field(default_factory=list)
    memory: List[int] = field(default_factory=list)

    @property
    def count(self) -> int:
        return sum(self.labels.values())

    def add(self, item: Read) -> None:
        self.end = item.at
        self.labels[error_label(item.outcome)] += 1
        if item.detail:
            self.details[clean_error_message(item.detail)] += 1
        if item.load.cpu_percent is not None:
            self.cpu.append(item.load.cpu_percent)
        if item.load.memory_mb is not None:
            self.memory.append(item.load.memory_mb)

    @property
    def worst_load(self) -> SystemLoad:
        """Highest CPU use and lowest free memory seen during the spell."""
        return SystemLoad(max(self.cpu) if self.cpu else None, min(self.memory) if self.memory else None)


def failure_spells(reads: List[Read]) -> List[Spell]:
    """Failures grouped per site into spells, newest first."""
    spells: List[Spell] = []
    open_by_site: Dict[str, Spell] = {}
    for item in sorted((item for item in reads if is_failure(item.outcome)), key=lambda item: item.at):
        spell = open_by_site.get(item.site)
        if spell is None or item.at - spell.end > SPELL_GAP:
            spell = open_by_site[item.site] = Spell(item.site, item.at, item.at)
            spells.append(spell)
        spell.add(item)
    return sorted(spells, key=lambda spell: spell.end, reverse=True)


def _spell_time(spell: Spell) -> str:
    start = spell.start.strftime("%d.%m %H:%M")
    if spell.end - spell.start < timedelta(minutes=1):
        return start
    end = spell.end.strftime("%H:%M" if spell.end.date() == spell.start.date() else "%d.%m %H:%M")
    return f"{start}–{end}"


def render_spells(reads: List[Read]) -> str:
    spells = failure_spells(reads)
    if not spells:
        return ""
    items = []
    for spell in spells[:MAX_LISTED_SPELLS]:
        kinds = ", ".join(f"{label} {count}" if len(spell.labels) > 1 else label
                          for label, count in spell.labels.most_common())
        notes = []
        if spell.details:
            notes.append(spell.details.most_common(1)[0][0])
        if spell.worst_load.text:
            notes.append(f"Pi: {spell.worst_load.text}")
        note = f"<small>{escape(' · '.join(notes))}</small>" if notes else ""
        items.append(
            f"<li><div><strong>{escape(_spell_time(spell))} · "
            f"{escape(site_label(spell.site))}</strong><span>{spell.count} okuma · {escape(kinds)}</span></div>{note}</li>"
        )
    more = len(spells) - MAX_LISTED_SPELLS
    extra = f"<p class='site-note'>ve {more} dönem daha</p>" if more > 0 else ""
    return ("<h3 class='spell-title'>Hata dönemleri</h3><p class='site-note'>Birbirine 15 dakikadan yakın engel ve hatalar "
            f"tek satırda; en yeni üstte.</p><ul class='error-spells'>{''.join(items)}</ul>{extra}")


def render_daily_history(reads: List[Read], now: datetime) -> str:
    since = now - timedelta(days=7)
    days = buckets(reads, check_gaps(reads, since), since, "7d")
    rows = "".join(
        f"<tr><td>{slot.start.strftime('%d.%m.%Y')}</td><td>{slot.reads}</td>"
        f"<td>{escape(_short_duration(slot.check_seconds))}</td>"
        f"<td>{escape(_percent(slot.reads - slot.failures, slot.reads))}</td>"
        f"<td class='{'bad' if slot.failures else 'zero'}'>{slot.failures}</td></tr>"
        for slot in reversed(days)
    )
    return ("<details class='daily-history' data-key='daily-history'><summary>Günlük geçmiş (son 7 gün)</summary>"
            "<div class='table-wrap measure-wrap'><table class='statistics-table measure-table'><thead><tr><th>Gün</th>"
            "<th>Okuma</th><th>Kontrol sıklığı</th><th>Başarı</th><th>Engel / hata</th></tr></thead>"
            f"<tbody>{rows}</tbody></table></div></details>")


def statistics_live_html(base: str, params: Optional[Dict[str, List[str]]] = None) -> str:
    period = period_key(params or {})
    label, span = PERIODS[period]
    # Whole minutes keep the block identical between refreshes when nothing changed.
    now = datetime.now().astimezone().replace(second=0, microsecond=0)
    since = now - span
    week = read_reads(DATABASE_PATH, now - timedelta(days=7) - MAX_CHECK_GAP)
    week = [item for item in week if item.outcome != "interrupted"]
    reads = [item for item in week if item.at >= since]
    gaps = check_gaps(week, since)
    requests_by_site = {
        report.site: (f"{report.total} ağ isteği · {report.captcha} captcha · {report.http_503} adet 503 · "
                      f"{report.http_429} adet 429 · {report.browser} tarayıcıyla")
        for report in read_site_requests(DATABASE_PATH, since)
    }
    return (
        f"{render_tiles(reads, gaps)}"
        f"<section class='summary-panel'><div class='summary-head'><h2>Kontrol sıklığı</h2><span>{escape(label)}</span></div>"
        f"{render_chart(buckets(reads, gaps, since, period), period)}</section>"
        f"<section class='summary-panel'><div class='summary-head'><h2>Siteler</h2><span>{escape(label)}</span></div>"
        f"{render_sites(site_figures(reads, gaps), requests_by_site)}</section>"
        f"<section class='summary-panel'><div class='summary-head'><h2>Engel ve hata türleri</h2><span>{escape(label)}</span></div>"
        f"{render_error_types(reads)}{render_spells(reads)}</section>"
        f"{render_daily_history(week, now)}"
        f"{render_problem_history(span)}"
    )


def render_statistics_page(base: str, params: Optional[Dict[str, List[str]]] = None) -> bytes:
    period = period_key(params or {})
    intro = ("<p class='statistics-intro'>Kontrol sıklığı, yüksek öncelikli bir ürünün iki okuması arasındaki tipik süredir. "
             "Engel; captcha, 503 ve 429 yanıtlarıdır. Sayfa açıkken veriler dakikada bir güncellenir.</p>")
    params = params or {}
    notice = render_notice(params.get("saved", [""])[0], params.get("msg", [""])[0])
    body = (f"<div class='statistics-top'><h2 class='page-heading'>İstatistik</h2>{render_period_switch(base, period)}</div>"
            f"{notice}{intro}"
            + live_region(base, f"live/statistics?p={period}", statistics_live_html(base, params))
            + render_reset_errors(base))
    return render_page(base, "statistics", "Hermes İstatistik", body, body_class="public ov", refresh_seconds=60,
                       scripts=CONFIRM_SCRIPT + live_script_tag(base))


def render_reset_errors(base: str) -> str:
    """Confirmed delete of every failed read; the counters start again from zero."""
    return (f"<div class='actions tool-actions statistics-reset'><form class='inline-form' method='post' "
            f"action='{escape(link(base, 'reset-errors'), quote=True)}' data-confirm='İstatistikteki tüm engel ve hata "
            "kayıtları kalıcı olarak silinecek; sayaçlar sıfırdan başlayacak. Başarılı okumalar kalır. Devam etmek istiyor musun?'>"
            "<button class='button secondary' type='submit'>Hata kayıtlarını sıfırla</button></form></div>")


def render_problem_history(span):
    state = load_json(STATE_PATH, {})
    records = Diagnostics(DATABASE_PATH).recent(span.total_seconds()/3600)
    items = []
    for item in records:
        status = "Düzeldi" if item["resolved"] is not None else "Açık"
        first = datetime.fromtimestamp(item["opened"]).astimezone().strftime("%d.%m %H:%M")
        last = datetime.fromtimestamp(item["resolved"] or item["updated"]).astimezone().strftime("%d.%m %H:%M")
        successful = item.get("last_successful_read")
        success = datetime.fromtimestamp(successful).astimezone().strftime("%d.%m %H:%M") if successful else "Henüz kayıt yok"
        rows = "".join(compact_error_row(detail) for detail in incident_errors(item, state))
        items.append("<section class='ov-errors'><ul>" + rows + "</ul></section>" +
                     f"<p>{status} · {item['count']} tekrar · {first} → {last} · Son tam okuma: {success}</p>")
    return ("<details class='summary-panel problem-history' data-key='problem-history'><summary>Sorun geçmişi</summary>"
            + ("".join(items) or "<p>Bu dönemde kayıt yok.</p>") + "</details>")
