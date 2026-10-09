"""Stylesheet, fonts and scripts shared by every page of the ingress and public panel."""

from functools import lru_cache
from pathlib import Path

FONT_DIR = Path(__file__).with_name("fonts")
FONT_FILES = ("sora-latin.woff2", "sora-latin-ext.woff2", "inter-latin.woff2", "inter-latin-ext.woff2")


@lru_cache(maxsize=None)
def read_font(name: str) -> bytes:
    """The Özet Tablo typefaces ship inside the add-on, so the panel needs no outside connection."""
    return (FONT_DIR / name).read_bytes()


APP_CSS = """
:root { color-scheme:dark; --bg:#111315; --panel:#1a1c1f; --card:#24272b; --line:#3c4147; --text:#f5f7fa; --muted:#b0b6be; --accent:#e4e5e3; --accent2:#b8bbba; --ok:#86dfb7; --warn:#ffd07a; --bad:#ff9caf; --head:#30343a; }
* { box-sizing:border-box; } body { margin:0; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; background:radial-gradient(circle at top left,#373b40,var(--bg) 58%); color:var(--text); font-size:14px; }
main { max-width:1060px; margin:0 auto; padding:28px 18px 44px; } .hero { border:1px solid var(--line); border-radius:22px; padding:22px; background:var(--panel); box-shadow:0 18px 42px rgba(0,0,0,.35); }
p { margin:0; color:var(--muted); line-height:1.5; font-size:13px; }
.badge { display:inline-flex; margin-bottom:12px; color:#17191b; background:linear-gradient(135deg,#ffd166,#f0ad61); border-radius:18px; padding:9px 15px; font-size:clamp(24px,5vw,42px); line-height:1; letter-spacing:-.04em; font-weight:900; }
.actions { display:flex; flex-wrap:wrap; gap:10px; margin-top:18px; align-items:center; } .inline-form { margin:0; } .button { display:inline-flex; align-items:center; justify-content:center; min-height:40px; padding:0 14px; border-radius:13px; border:1px solid transparent; text-decoration:none; font-weight:800; font-size:13px; cursor:pointer; }
.button.primary { color:#181a1c; background:linear-gradient(135deg,var(--accent),var(--accent2)); } .button.secondary { color:var(--text); background:#35393d; border-color:var(--line); } .button.test { color:#f5f7fa; background:linear-gradient(135deg,#54595e,#35393d); border-color:#6b7075; }
.notice { margin-top:14px; padding:11px 13px; border-radius:12px; font-weight:700; font-size:13px; } .notice-ok { color:#c6f7e6; background:rgba(127,220,184,.14); border:1px solid rgba(127,220,184,.38); } .notice-fail { color:#ffd8e3; background:rgba(255,156,181,.14); border:1px solid rgba(255,156,181,.38); }
.grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:11px; margin-top:16px; } .card { border:1px solid var(--line); border-radius:15px; padding:14px; background:var(--card); min-height:82px; } .card span { display:block; color:var(--muted); font-size:12px; margin-bottom:8px; } .card strong { display:block; font-size:19px; line-height:1.18; overflow-wrap:anywhere; }
.card.status-ok { border-color:rgba(127,220,184,.38); background:linear-gradient(135deg,rgba(127,220,184,.12),var(--card) 62%); } .card.status-ok strong { color:var(--ok); } .card.status-warn strong { color:var(--warn); } .card.status-error strong { color:var(--bad); }
.summary-panel { margin-top:18px; border:1px solid var(--line); border-radius:18px; padding:16px; background:var(--card); } .summary-head { display:flex; align-items:flex-end; justify-content:space-between; gap:12px; margin-bottom:12px; } .summary-head h2 { font-size:18px; margin:0; } .summary-head span { color:var(--muted); font-size:12px; white-space:nowrap; } .table-section + .table-section { margin-top:18px; } .table-section h3 { margin:0 0 9px; font-size:14px; color:#f0f1f0; } .deals-section h3 { color:#b7f0dc; }
.table-wrap { overflow-x:auto; border:1px solid var(--line); border-radius:14px; } table { width:100%; border-collapse:collapse; min-width:930px; } th,td { padding:8px 8px; border-bottom:1px solid var(--line); text-align:right; white-space:nowrap; } th { color:#e1e3e3; background:var(--head); font-size:11px; text-transform:uppercase; letter-spacing:.035em; } td { color:var(--text); font-size:13px; font-variant-numeric:tabular-nums; } tr:last-child td { border-bottom:none; } th:nth-child(1),td:nth-child(1) { width:104px; } th:nth-child(1),td:nth-child(1),th:nth-child(2),td:nth-child(2) { text-align:left; } th:not(:nth-child(2)),td:not(:nth-child(2)) { width:100px; } th:nth-child(6),td:nth-child(6) { width:148px; } th:nth-child(7),td:nth-child(7) { width:92px; } .empty-row td { color:var(--muted); text-align:left; background:rgba(255,255,255,.025); }
.priority-dot { display:inline-block; flex:none; width:8px; height:8px; margin:0 7px 1px 0; border-radius:50%; vertical-align:middle; } .priority-cycle { background:#ff5c64; } .priority-30m { background:#ff9548; } .priority-60m { background:#f2c94c; } .priority-3h { background:#c4dc4a; } .priority-6h { background:#3fbf6a; }
.public main { max-width:1180px; } .public .hero { padding:18px; } .public .badge { font-size:clamp(22px,4vw,36px); }
.public-actions { margin:16px 0 6px; } .public-actions .button { min-width:132px; }
@media (max-width:720px) {
  body { font-size:13px; background:#111315; }
  main { padding:10px 8px 26px; }
  .hero { border-radius:18px; padding:14px; }
  .public main { padding:0; }
  .public .hero { min-height:100vh; border-width:0; border-radius:0; padding:14px 10px 24px; box-shadow:none; }
  .badge { margin-bottom:8px; padding:8px 13px; font-size:28px; }
  p { font-size:12px; }
  .actions { gap:8px; }
  .public-actions { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); }
  .public-actions .button, .public-actions .inline-form { width:100%; min-width:0; }
  .public-actions .button { width:100%; min-width:0; min-height:44px; padding:0 10px; font-size:12px; }
  .grid { grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; }
  .card { min-height:70px; padding:11px; border-radius:13px; }
  .card span { font-size:11px; margin-bottom:6px; }
  .card strong { font-size:15px; }
  .summary-panel { margin-top:12px; padding:11px; border-radius:15px; }
  .summary-head { align-items:flex-start; flex-direction:column; gap:4px; margin-bottom:10px; }
  .summary-head h2 { font-size:16px; }
  .summary-head span { white-space:normal; font-size:11px; }
  .public .summary-head span { font-size:16px; line-height:1.3; color:#ffd166; font-weight:800; }
  .table-wrap { overflow:visible; border:0; border-radius:0; }
  table { min-width:0; }
  thead { display:none; }
  table, tbody, td { display:block; width:100%; }
  .note, .footer { font-size:11px; }
}

.statistics-intro { color:var(--muted); margin:10px 0 18px; }
.statistics-chart { width:100%; height:auto; display:block; border:1px solid var(--line); border-radius:14px; background:#202327; }
.statistics-chart text { fill:var(--muted); font-size:13px; font-family:inherit; }
.statistics-chart .grid-line { stroke:#41464b; stroke-width:1; }
.statistics-table { min-width:0; }
.statistics-table th,.statistics-table td { width:auto !important; text-align:left !important; }
.statistics-table th:last-child,.statistics-table td:last-child { text-align:right !important; }
.statistics-table thead { position:sticky; top:0; z-index:1; }
.statistics-empty { padding:14px; color:var(--muted); }
.statistics-top { display:flex; align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap; margin-top:16px; }
.statistics-top .page-heading { margin:0; }
.period-switch { margin:0; }
.period-switch .button[aria-current='page'] { color:#181a1c; background:linear-gradient(135deg,var(--accent),var(--accent2)); }
.stat-tiles { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:10px; margin-top:4px; }
.stat-tile { min-width:0; padding:13px 14px; border:1px solid var(--line); border-radius:15px; background:var(--card); }
.stat-tile span { display:block; color:var(--muted); font-size:11px; font-weight:800; letter-spacing:.035em; text-transform:uppercase; }
.stat-tile strong { display:block; margin:6px 0 4px; font-size:26px; line-height:1.05; font-variant-numeric:tabular-nums; }
.stat-tile small { display:block; color:var(--muted); font-size:11px; line-height:1.35; }
.stat-tile-alert strong { color:var(--bad); }
.statistics-chart .axis-line { stroke:#5a6067; stroke-width:1; }
.statistics-chart .check-bar { fill:var(--warn); }
.statistics-chart .failure-count { fill:var(--bad); font-weight:800; font-size:14px; }
.chart-legend { display:flex; flex-wrap:wrap; align-items:center; gap:6px 10px; margin-top:8px; font-size:11px; }
.legend-bar { display:inline-block; width:12px; height:12px; border-radius:3px; background:var(--warn); vertical-align:middle; }
.legend-count { margin-right:6px; color:var(--bad); font-weight:800; }
.legend-item { display:inline-flex; align-items:center; gap:6px; }
.chart-wrap { overflow-x:auto; }
.site-health dl div { display:flex; flex-direction:column; justify-content:space-between; }
.site-health-list { display:grid; gap:10px; }
.site-health { padding:12px 14px; border:1px solid var(--line); border-radius:14px; background:#202327; }
.site-health header { display:flex; justify-content:space-between; align-items:baseline; gap:10px; }
.site-health header strong { font-size:15px; }
.site-health header span { color:var(--muted); font-size:12px; }
.health-bar { display:flex; height:8px; margin:9px 0 10px; border-radius:6px; overflow:hidden; background:#33373c; }
.health-bar i { display:block; min-width:4px; }
.health-ok { background:var(--ok); } .health-blocked { background:var(--warn); } .health-error { background:var(--bad); }
.site-health dl { display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:8px; margin:0; }
.site-health dt { color:var(--muted); font-size:10px; font-weight:800; letter-spacing:.03em; text-transform:uppercase; }
.site-health dd { margin:3px 0 0; font-size:15px; font-weight:750; font-variant-numeric:tabular-nums; }
.site-health dd.bad,.measure-table td.bad { color:var(--bad); } .site-health dd.zero { color:var(--muted); }
.site-note { margin-top:9px; font-size:11px; }
.statistics-good { color:var(--ok); }
.daily-history { margin-top:18px; border:1px solid var(--line); border-radius:18px; padding:12px 16px; background:var(--card); }
.daily-history summary { cursor:pointer; font-weight:800; }
.daily-history .table-wrap { margin-top:12px; }
.measure-wrap { border:1px solid var(--line); border-radius:14px; }
.measure-table th:not(:first-child),.measure-table td:not(:first-child) { text-align:right !important; }
.measure-table td.zero { color:var(--muted); }
.spell-title { margin:18px 0 0; font-size:14px; }
.error-spells { list-style:none; margin:10px 0 0; padding:0; display:grid; gap:8px; }
.error-spells li { padding:10px 12px; border:1px solid var(--line); border-left:3px solid var(--bad); border-radius:12px; background:#202327; }
.error-spells li div { display:flex; flex-wrap:wrap; justify-content:space-between; gap:4px 12px; }
.error-spells li strong { font-size:14px; font-variant-numeric:tabular-nums; }
.error-spells li span { color:var(--bad); font-size:13px; font-weight:700; }
.error-spells li small { display:block; margin-top:5px; color:var(--muted); font-size:12px; overflow-wrap:anywhere; }
@media (max-width:720px) {
  .stat-tiles { grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; }
  .stat-tile strong { font-size:22px; }
  .site-health dl { grid-template-columns:repeat(3,minmax(0,1fr)); }
  .statistics-chart { min-width:640px; }
  .statistics-table { display:table; min-width:0; table-layout:fixed; }
  .statistics-table thead { display:table-header-group; }
  .statistics-table tbody { display:table-row-group; }
  .statistics-table tr { display:table-row; }
  .statistics-table th,.statistics-table td { display:table-cell; width:auto; padding:7px 5px; white-space:normal; font-size:11px; }
}

/* Settings page: generic form rules stay scoped to the settings page. */
.page-heading { margin:18px 0 4px; font-size:20px; }
.settings-page .settings-section { margin-top:18px; border:1px solid var(--line); border-radius:18px; padding:16px; background:var(--card); }
.settings-page .settings-section h2 { margin:0 0 10px; font-size:18px; }
.settings-page details { border:1px solid var(--line); border-radius:14px; background:#1e2125; margin:9px 0; overflow:hidden; }
.settings-page summary { cursor:pointer; padding:13px 14px; font-weight:900; color:#f5f7fa; list-style:none; }
.settings-page summary::-webkit-details-marker { display:none; }
.settings-page summary::before { content:'▸'; display:inline-block; margin-right:8px; color:#d6d8d7; }
.settings-page details[open] summary::before { transform:rotate(90deg); }
.settings-page .form-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:12px; padding:0 14px 14px; }
.settings-page label { display:grid; gap:6px; color:var(--muted); font-size:12px; font-weight:700; }
.settings-page input[type='text'], .settings-page input[type='number'], .settings-page input[type='url'], .settings-page select, .settings-page textarea { width:100%; min-height:40px; border-radius:11px; border:1px solid var(--line); background:#141619; color:var(--text); padding:10px 11px; font-size:13px; font-family:inherit; }
.settings-page textarea { resize:vertical; line-height:1.35; }
.settings-page .checkbox-row { display:flex; align-items:center; gap:9px; min-height:40px; color:var(--text); }
.settings-page .danger { color:#ffd8e3; }
.button.danger { color:#fff5f7; background:#b9364d; border-color:#ed7288; } .button.danger:hover { background:#cf465f; } .button.is-submitting { pointer-events:none; opacity:.7; }
.footer-note { margin-top:14px; border-left:4px solid #a9adaf; padding:12px 14px; background:rgba(169,173,175,.14); border-radius:10px; font-size:13px; }
.watch-tools { display:grid; gap:12px; } .watch-search { display:grid; gap:6px; max-width:440px; margin:0; color:var(--muted); font-size:12px; font-weight:750; } .watch-search input { width:100%; min-height:40px; border:1px solid var(--line); border-radius:11px; padding:10px 11px; background:#141619; color:var(--text); font:inherit; }
.watch-group-filters { display:flex; flex-wrap:wrap; gap:8px; margin:0; } .watch-group-filter { min-height:34px; border:1px solid var(--line); border-radius:999px; padding:0 12px; background:#34383d; color:var(--text); font:700 12px inherit; cursor:pointer; } .watch-group-filter[aria-pressed='false'] { color:var(--muted); background:#181a1d; opacity:.72; text-decoration:line-through; } .watch-group-filter:hover { border-color:#d6d8d7; }
.watch-actions { display:flex; align-items:center; gap:8px; min-height:40px; }
.saving-overlay { position:fixed; inset:0; z-index:20; display:grid; place-items:center; padding:20px; background:rgba(7,8,9,.78); backdrop-filter:blur(5px); } .saving-overlay[hidden] { display:none; } .saving-dialog { width:min(100%,430px); border:1px solid rgba(214,216,215,.45); border-radius:18px; padding:22px; background:#24272b; box-shadow:0 22px 50px rgba(0,0,0,.5); } .saving-dialog h2 { margin:0 0 9px; font-size:20px; } .saving-dialog p { font-size:14px; } .saving-spinner { width:28px; height:28px; margin:0 0 14px; border:4px solid rgba(214,216,215,.22); border-top-color:#d6d8d7; border-radius:50%; animation:hermes-spin .8s linear infinite; } @keyframes hermes-spin { to { transform:rotate(360deg); } }
.watch-layout { grid-column:1 / -1; display:grid; gap:12px; } .watch-top { display:grid; grid-template-columns:minmax(105px,.7fr) minmax(210px,2fr) minmax(100px,.65fr) minmax(115px,.75fr) minmax(90px,.6fr); gap:12px; align-items:end; } .watch-links { display:grid; gap:10px; } .watch-bottom { display:flex; flex-wrap:wrap; align-items:end; gap:12px; } .watch-bottom > label:first-child { flex:0 1 205px; max-width:205px; } .watch-exclude { flex:1 1 240px; } .watch-bottom .checkbox-row { flex:0 0 auto; padding-bottom:1px; } .watch-bottom .watch-actions { margin-left:auto; } .watch-priority { flex:0 1 170px; max-width:170px; } .watch-priority select { width:100%; min-height:40px; border-radius:11px; border:1px solid var(--line); background:#141619; color:var(--text); padding:10px 11px; font-size:13px; font-family:inherit; } .watch-hint { flex:1 1 100%; margin:-4px 0 0; font-size:11px; }
.settings-page details.is-deleted { opacity:.55; } .settings-page details.is-deleted summary { color:var(--bad); }
.apply-bar { position:sticky; bottom:0; z-index:8; display:flex; flex-wrap:wrap; gap:12px; align-items:center; justify-content:space-between; margin-top:18px; padding:14px 0 4px; border-top:1px solid var(--line); background:var(--panel); } .apply-bar p { flex:1 1 220px; } .apply-bar .button { min-width:180px; }
.config-error { margin-top:14px; }
.topbar { display:flex; align-items:center; gap:12px; margin-bottom:12px; } .topbar .badge { margin-bottom:0; text-decoration:none; } .gear-button { display:inline-flex; align-items:center; justify-content:center; width:44px; height:44px; border:1px solid var(--line); border-radius:14px; color:var(--text); background:#34383d; text-decoration:none; } .gear-button:hover { border-color:#d6d8d7; } .gear-button[aria-current='page'] { color:#181a1c; background:linear-gradient(135deg,var(--accent),var(--accent2)); border-color:transparent; }
.tool-actions { margin-top:18px; }
.statistics-reset { margin-top:18px; }
@media (max-width:900px) { .watch-top { grid-template-columns:repeat(2,minmax(0,1fr)); } }
@media (max-width:720px) { .nav-actions, .tool-actions { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); } .nav-actions .button, .tool-actions .button, .tool-actions .inline-form { width:100%; min-width:0; } .tool-actions > :last-child:nth-child(odd) { grid-column:1 / -1; } }
@media (max-width:700px) { .watch-bottom > label:first-child { flex:1 1 100%; max-width:none; } .watch-bottom .watch-actions { width:100%; margin-left:0; } .watch-bottom .watch-actions .button { width:100%; } .apply-bar .button { width:100%; } }
@media (max-width:420px) { .watch-top { grid-template-columns:1fr; } }
"""

SETTINGS_SCRIPT = """
(() => {
  const storageKey = 'hermes-hidden-watch-groups';
  const hiddenGroups = new Set(JSON.parse(localStorage.getItem(storageKey) || '[]'));
  const normalize = (value) => String(value || '').toLocaleLowerCase('tr-TR');
  const watchSearch = document.getElementById('watch-search');
  const settingsForm = document.getElementById('settings-form');
  const watchesCount = document.getElementById('watches-count');
  const newWatchList = document.getElementById('new-watch-list');
  const newWatchTemplate = document.getElementById('new-watch-template');
  let dirty = false;

  const refreshWatchList = () => {
    const searchText = normalize(watchSearch?.value.trim());
    document.querySelectorAll('[data-watch-group]').forEach((item) => {
      const groupHidden = hiddenGroups.has(normalize(item.dataset.watchGroup || 'Diğer'));
      const productName = normalize(item.dataset.watchSearch);
      const searchHidden = Boolean(searchText) && !productName.includes(searchText);
      item.hidden = groupHidden || searchHidden;
    });
    document.querySelectorAll('[data-watch-group-filter]').forEach((button) => {
      const hidden = hiddenGroups.has(normalize(button.dataset.watchGroupFilter || 'Diğer'));
      button.setAttribute('aria-pressed', String(!hidden));
      button.title = hidden ? 'Grubu göster' : 'Grubu gizle';
    });
  };

  document.querySelectorAll('[data-watch-group-filter]').forEach((button) => {
    button.addEventListener('click', () => {
      const group = normalize(button.dataset.watchGroupFilter || 'Diğer');
      if (hiddenGroups.has(group)) hiddenGroups.delete(group); else hiddenGroups.add(group);
      localStorage.setItem(storageKey, JSON.stringify([...hiddenGroups]));
      refreshWatchList();
    });
  });
  watchSearch?.addEventListener('input', refreshWatchList);
  refreshWatchList();

  const nextWatchIndex = () => {
    const current = Number(watchesCount?.value || 0);
    const next = current;
    if (watchesCount) watchesCount.value = String(current + 1);
    return next;
  };

  document.getElementById('add-watch-card')?.addEventListener('click', () => {
    if (!newWatchTemplate || !newWatchList) return;
    const index = nextWatchIndex();
    newWatchList.insertAdjacentHTML(
      'beforeend',
      newWatchTemplate.innerHTML.replaceAll('__INDEX__', String(index)),
    );
    dirty = true;
  });

  document.addEventListener('click', (event) => {
    const removeButton = event.target.closest('[data-remove-new-watch]');
    if (removeButton) {
      const card = removeButton.closest('[data-watch-card]');
      card?.remove();
      dirty = true;
      return;
    }
    const deleteButton = event.target.closest('[data-delete-watch]');
    if (!deleteButton) return;
    const card = deleteButton.closest('[data-watch-card]');
    const flag = card?.querySelector('[data-delete-flag]');
    if (!card || !flag) return;
    const marked = flag.value === '1';
    if (marked) {
      flag.value = '0';
      card.classList.remove('is-deleted');
      deleteButton.textContent = 'Sil';
    } else {
      if (!window.confirm('Bu takip, değişiklikleri uygulayınca silinecek. Devam etmek istiyor musun?')) return;
      flag.value = '1';
      card.classList.add('is-deleted');
      deleteButton.textContent = 'Vazgeç';
    }
    dirty = true;
  });

  settingsForm?.addEventListener('input', () => { dirty = true; });
  settingsForm?.addEventListener('change', () => { dirty = true; });
  window.addEventListener('beforeunload', (event) => {
    if (!dirty) return;
    event.preventDefault();
    event.returnValue = '';
  });

  const savingOverlay = document.getElementById('saving-overlay');
  const savingTitle = document.getElementById('saving-title');
  const savingMessage = document.getElementById('saving-message');
  settingsForm?.addEventListener('submit', (event) => {
    const button = event.submitter;
    if (button) {
      button.classList.add('is-submitting');
      button.setAttribute('aria-disabled', 'true');
      button.textContent = 'Kaydediliyor...';
    }
    dirty = false;
    savingTitle.textContent = 'Ayarlar kaydediliyor';
    savingMessage.textContent = 'Tüm değişiklikler tek seferde Home Assistant’a yazılıyor. Hermes bir kez yeniden başlayacak; hazır olduğunda ayarlara otomatik dönülecek.';
    savingOverlay.hidden = false;
  });
})();
""".strip()

RESTART_SCRIPT = """
(() => {
  const script = document.getElementById('hermes-restart-script');
  const settingsPath = script?.dataset.settingsPath || '../settings';
  const returnPath = script?.dataset.returnPath || settingsPath;
  const healthPath = script?.dataset.healthPath || '../health';
  const statusBox = document.getElementById('restart-status');
  let attempts = 0;

  const waitForHermes = async () => {
    attempts += 1;
    statusBox.textContent = `Hermes kontrol ediliyor... Deneme ${attempts}`;
    try {
      const response = await fetch(`${healthPath}?ts=${Date.now()}`, { cache: 'no-store' });
      if (response.ok) {
        statusBox.textContent = 'Hermes hazır. Sayfa yenileniyor...';
        const separator = returnPath.includes('?') ? '&' : '?';
        window.location.href = `${returnPath}${separator}saved=ok&msg=${encodeURIComponent('Hermes hazır. Ayarlar güncellendi.')}`;
        return;
      }
    } catch (_error) {
      statusBox.textContent = 'Hermes yeniden başlıyor, bağlantı bekleniyor...';
    }
    window.setTimeout(waitForHermes, 2000);
  };

  window.setTimeout(waitForHermes, 6000);
})();
""".strip()

# Refreshes #live-region in place: keeps open groups and the scroll position,
# pauses while the tab is hidden; the server skips a block the page already shows.
LIVE_SCRIPT = """
(() => {
  const region = document.getElementById('live-region');
  if (!region || !window.fetch) return;
  const url = region.dataset.liveUrl;
  let busy = false;
  let version = '';
  const refresh = async () => {
    if (busy || document.hidden) return;
    busy = true;
    try {
      const query = `ts=${Date.now()}&v=${encodeURIComponent(version)}`;
      const response = await fetch(`${url}${url.includes('?') ? '&' : '?'}${query}`, { cache: 'no-store' });
      if (!response.ok) return;
      const data = await response.json();
      if (data.same || typeof data.html !== 'string') return;
      const open = new Set([...region.querySelectorAll('details[data-key][open]')].map((item) => item.dataset.key));
      const scroll = window.scrollY;
      region.innerHTML = data.html;
      document.dispatchEvent(new Event('hermes-live'));
      region.querySelectorAll('details[data-key]').forEach((item) => { if (open.has(item.dataset.key)) item.open = true; });
      window.scrollTo(0, scroll);
      version = data.v || '';
    } catch (_error) {
      // Hermes may be restarting; the next attempt retries.
    } finally {
      busy = false;
    }
  };
  window.setInterval(refresh, (Number(region.dataset.liveInterval) || 15) * 1000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
})();
""".strip()


# -- Özet Tablo home screen -------------------------------------------------------------

OVERVIEW_CSS = """
@font-face { font-family:'Sora'; font-weight:400 800; font-display:swap; src:url(fonts/sora-latin-ext.woff2) format('woff2'); unicode-range:U+0100-02BA,U+02BD-02C5,U+02C7-02CC,U+02CE-02D7,U+02DD-02FF,U+0304,U+0308,U+0329,U+1D00-1DBF,U+1E00-1E9F,U+1EF2-1EFF,U+2020,U+20A0-20AB,U+20AD-20C4,U+2113,U+2C60-2C7F,U+A720-A7FF; }
@font-face { font-family:'Sora'; font-weight:400 800; font-display:swap; src:url(fonts/sora-latin.woff2) format('woff2'); unicode-range:U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+0304,U+0308,U+0329,U+2000-206F,U+20AC,U+2122,U+2191,U+2193,U+2212,U+2215,U+FEFF,U+FFFD; }
@font-face { font-family:'Inter'; font-weight:400 600; font-display:swap; src:url(fonts/inter-latin-ext.woff2) format('woff2'); unicode-range:U+0100-02BA,U+02BD-02C5,U+02C7-02CC,U+02CE-02D7,U+02DD-02FF,U+0304,U+0308,U+0329,U+1D00-1DBF,U+1E00-1E9F,U+1EF2-1EFF,U+2020,U+20A0-20AB,U+20AD-20C4,U+2113,U+2C60-2C7F,U+A720-A7FF; }
@font-face { font-family:'Inter'; font-weight:400 600; font-display:swap; src:url(fonts/inter-latin.woff2) format('woff2'); unicode-range:U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+0304,U+0308,U+0329,U+2000-206F,U+20AC,U+2122,U+2191,U+2193,U+2212,U+2215,U+FEFF,U+FFFD; }
body.ov { --ink:#f4f2ff; --mute:#9a95b8; --faint:#5d5880; --glass:rgba(255,255,255,.055); --glass2:rgba(255,255,255,.09); --edge:rgba(255,255,255,.1); --lime:#b6ff3b; --pink:#ff3d9a; --violet:#8b5cff; --cyan:#27e1ff; --display:'Sora',system-ui,sans-serif; --body:'Inter',system-ui,sans-serif; background:#07060f; color:var(--ink); font:15px/1.45 var(--body); -webkit-font-smoothing:antialiased; overflow-x:hidden; }
body.ov::before, body.ov::after { content:""; position:fixed; z-index:-1; border-radius:50%; filter:blur(90px); pointer-events:none; animation:ov-drift 20s ease-in-out infinite alternate; }
body.ov::before { width:520px; height:520px; left:-120px; top:-100px; background:#8b5cff; opacity:.45; }
body.ov::after { width:460px; height:460px; right:-100px; top:260px; background:#ff3d9a; opacity:.28; animation-duration:26s; }
@keyframes ov-drift { to { transform:translate(90px,70px) scale(1.2); } }
body.ov main { max-width:1180px; padding:0 20px 70px; }
body.ov .hero, body.ov.public .hero { min-height:0; padding:0; border:0; border-radius:0; background:transparent; box-shadow:none; }
body.ov .topbar { margin:0; padding:18px 0; } body.ov .topbar .badge { position:relative; display:inline-flex; align-items:center; gap:11px; padding:0; margin:0; background:none; color:#fff; font:800 20px var(--display); letter-spacing:-.03em; }
body.ov .topbar .badge::before { content:""; flex:none; width:38px; height:38px; border-radius:13px; background:conic-gradient(from 210deg,#8b5cff,#ff3d9a,#b6ff3b,#8b5cff); animation:ov-spin 8s linear infinite; }
body.ov .topbar .badge::after { content:"H"; position:absolute; left:4px; top:50%; display:grid; place-items:center; width:30px; height:30px; margin-top:-15px; border-radius:10px; background:#07060f; color:#fff; font:800 18px var(--display); letter-spacing:0; }
@keyframes ov-spin { to { transform:rotate(360deg); } }
body.ov .topbar .badge::before { content:"H"; display:grid; place-items:center; width:38px; height:38px; border-radius:13px; color:#07060f; font:800 18px var(--display); letter-spacing:0; background:conic-gradient(from 210deg,#8b5cff,#ff3d9a,#b6ff3b,#8b5cff); }
body.ov .gear-button { width:40px; height:40px; margin-left:auto; border-radius:13px; border:1px solid var(--edge); background:var(--glass); color:var(--mute); transition:transform .3s,color .3s; } body.ov .gear-button:hover { transform:rotate(90deg); color:var(--ink); border-color:var(--edge); }
body.ov .notice { margin:0 0 14px; }
body.ov a { color:inherit; }
.ov .site-amazon { --c:#ffa31a; } .ov .site-hepsiburada { --c:#ff5a36; } .ov .site-trendyol { --c:#ff3d9a; } .ov .site-network { --c:#19e6c1; } .ov .site-beymenclub { --c:#e8b07a; } .ov .site-nordbron { --c:#4d9bff; } .ov .site-zara { --c:#9dff4d; } .ov .site-hm { --c:#c26bff; } .ov .site-togg { --c:#ff7b6b; } .ov .site-other { --c:#a7a3c8; }
.ov-status { display:flex; align-items:center; gap:8px; width:max-content; max-width:100%; margin:0 0 10px auto; padding:7px 14px; border-radius:99px; background:var(--glass); border:1px solid var(--edge); font-size:13px; color:var(--mute); } .ov-status b { color:var(--ink); font-weight:600; }
.ov-pulse { position:relative; flex:none; width:9px; height:9px; border-radius:50%; background:var(--lime); } .ov-pulse::after { content:""; position:absolute; inset:0; border-radius:50%; background:var(--lime); animation:ov-ping 1.8s ease-out infinite; } @keyframes ov-ping { to { transform:scale(3.2); opacity:0; } }
.ov-hero { display:flex; align-items:flex-end; justify-content:space-between; gap:18px; margin:4px 0 22px; } .ov-hero h1 { margin:0; font:800 clamp(28px,4vw,40px)/1.05 var(--display); letter-spacing:-.045em; } .ov-hero p { margin-top:8px; max-width:420px; color:var(--mute); font-size:14px; }
.ov-nums { display:flex; gap:10px; } .ov-num { min-width:104px; padding:14px 16px; border-radius:20px; background:var(--glass); border:1px solid var(--edge); backdrop-filter:blur(10px); } .ov-num b { display:block; font:800 30px var(--display); letter-spacing:-.04em; } .ov-num span { font-size:12px; color:var(--mute); } .ov-num.deal b { color:var(--lime); }
.ov-sec { display:flex; align-items:center; gap:12px; margin:8px 0 14px; font:700 14px var(--display); letter-spacing:.14em; text-transform:uppercase; color:var(--mute); } .ov-sec::after { content:""; flex:1; height:1px; background:linear-gradient(90deg,var(--edge),transparent); } .ov-sec small { font:500 12px var(--body); letter-spacing:0; text-transform:none; }
.ov-empty { padding:24px; text-align:center; color:var(--mute); border-radius:18px; border:1px dashed var(--edge); }
.ov-spot { display:grid; grid-template-columns:repeat(auto-fill,minmax(min(270px,100%),1fr)); gap:14px; margin-bottom:30px; }
.ov-deal { position:relative; display:flex; flex-direction:column; min-height:128px; padding:14px 16px; border-radius:20px; cursor:pointer; background:linear-gradient(160deg,color-mix(in srgb,var(--c) 20%,#0e0b1c),#0b0916 70%); border:1.5px solid color-mix(in srgb,var(--c) 55%,transparent); box-shadow:0 0 26px color-mix(in srgb,var(--c) 14%,transparent); transition:transform .3s cubic-bezier(.2,.9,.3,1.3); } .ov-deal:hover { transform:translateY(-4px); }
.ov-deal-top { display:flex; align-items:center; justify-content:space-between; gap:8px; }
.ov-deal-who { display:flex; align-items:center; gap:8px; min-width:0; } .ov-vars .ov-depo, .ov-deal-who .ov-depo { margin:0; padding:2px 8px; font-size:11px; } .ov-site { display:flex; align-items:center; gap:7px; font:700 13px var(--display); color:color-mix(in srgb,var(--c) 70%,#fff); } .ov-site::before { content:""; width:9px; height:9px; border-radius:50%; background:var(--c); box-shadow:0 0 10px var(--c); }
.ov-ago { font:600 12px var(--body); color:#c4bfe0; white-space:nowrap; } .ov-ago::before { content:"⟳ "; opacity:.7; }
.ov-deal h3 { margin:10px 0 auto; font:600 13.5px/1.3 var(--display); color:#e9e6ff; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }
.ov-deal-foot { display:flex; align-items:flex-end; justify-content:space-between; gap:10px; margin-top:10px; } .ov-deal .ov-price { font:800 30px/1 var(--display); letter-spacing:-.05em; } .ov-price small { margin-left:3px; font-size:.5em; font-weight:600; color:var(--mute); letter-spacing:0; }
.ov-deal-side { text-align:right; } .ov-deal-side .ov-spark { width:72px; height:26px; color:var(--lime); display:block; margin-left:auto; } .ov-off { font:800 14px var(--display); color:var(--lime); } .ov-off small { display:block; font:500 11px var(--body); color:var(--mute); }
.ov-tabs { display:inline-flex; max-width:100%; margin-bottom:18px; padding:5px; border-radius:99px; background:var(--glass); border:1px solid var(--edge); overflow-x:auto; } .ov-tab { padding:9px 18px; border:0; border-radius:99px; background:none; color:var(--mute); font:600 14px var(--display); cursor:pointer; white-space:nowrap; transition:background .25s,color .25s; } .ov-tab small { margin-left:4px; opacity:.7; font-weight:600; } .ov-tab[aria-selected='true'] { color:#0b0916; background:linear-gradient(95deg,var(--lime),var(--cyan)); }
.ov-pane[hidden], .ov-tile[hidden], .ov-group[hidden] { display:none; }
.ov-filters { display:flex; flex-wrap:wrap; gap:8px; margin-bottom:18px; } .ov-chip { display:inline-flex; align-items:center; gap:8px; padding:7px 14px; border-radius:99px; background:var(--glass); border:1px solid var(--edge); color:var(--ink); font:600 13px var(--display); cursor:pointer; transition:transform .25s,background .25s; } .ov-chip:hover { transform:translateY(-2px); } .ov-chip i { width:9px; height:9px; border-radius:50%; background:var(--c); box-shadow:0 0 12px var(--c); } .ov-chip[aria-pressed='true'] { border-color:var(--c,#fff); background:color-mix(in srgb,var(--c,#fff) 24%,transparent); }
.ov-group { margin-top:22px; } .ov-group-head { display:flex; align-items:center; gap:10px; margin:0 0 10px; font:700 14px var(--display); color:var(--mute); } .ov-group-head i { flex:none; width:8px; height:8px; border-radius:3px; background:var(--c); transform:rotate(45deg); } .ov-group-head b { min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:var(--ink); } .ov-group-head span { flex:none; font-weight:500; font-size:13px; color:var(--faint); }
.ov-tiles { display:grid; grid-template-columns:repeat(auto-fill,minmax(min(300px,100%),1fr)); gap:12px; }
.ov-tile { position:relative; display:flex; align-items:center; gap:12px; padding:14px; border-radius:20px; cursor:pointer; overflow:hidden; background:var(--glass); border:1px solid var(--edge); transition:transform .25s,border-color .25s; } .ov-tile:hover { transform:translateY(-4px); border-color:color-mix(in srgb,var(--c) 60%,transparent); } .ov-tile::after { content:""; position:absolute; inset:0; pointer-events:none; background:radial-gradient(240px 90px at 50% 0,color-mix(in srgb,var(--c) 26%,transparent),transparent); opacity:0; transition:opacity .3s; } .ov-tile:hover::after { opacity:1; }
.ov-av { flex:none; display:grid; place-items:center; width:44px; height:44px; border-radius:15px; color:#0b0916; font:800 17px var(--display); background:linear-gradient(135deg,var(--c),color-mix(in srgb,var(--c) 45%,#fff)); }
.ov-tx { position:relative; z-index:1; flex:1; min-width:0; } .ov-tx h4 { margin:0; color:#d9d5f5; font:500 13.5px/1.3 var(--body); display:-webkit-box; -webkit-line-clamp:3; -webkit-box-orient:vertical; overflow:hidden; } .ov-tile:has(.ov-vars) .ov-tx h4 { -webkit-line-clamp:2; } .ov-tx .ov-price { margin-top:3px; font:800 20px var(--display); letter-spacing:-.04em; }
.ov-vars { display:flex; flex-wrap:wrap; gap:5px; margin-top:5px; } .ov-vars em { max-width:100%; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; padding:2px 8px; border-radius:99px; font:600 11px var(--display); font-style:normal; background:#1d1b2b; border:1px solid rgba(255,255,255,.07); color:#a39fbe; }
.ov-side { position:relative; z-index:1; flex:none; text-align:right; } .ov-side .ov-spark { display:block; width:64px; height:30px; margin:0 0 5px auto; color:var(--c); } .ov-side .ov-ago { display:block; margin-top:5px; }
.ov-chg { display:inline-block; padding:3px 8px; border-radius:99px; font:700 12px var(--display); } .ov-chg.down { background:rgba(182,255,59,.14); color:var(--lime); } .ov-chg.up { background:rgba(255,61,154,.15); color:#ff7ab8; } .ov-chg.flat { background:var(--glass2); color:var(--mute); }
.ov-rows { display:grid; gap:10px; margin:0; padding:0; list-style:none; } .ov-row { display:flex; align-items:center; gap:14px; padding:14px 16px; border-radius:18px; background:var(--glass); border:1px solid var(--edge); } .ov-row .ov-tx h4 { -webkit-line-clamp:2; } .ov-row .ov-tx a:hover { text-decoration:underline; } .ov-row .ov-tx span { display:block; margin-top:2px; font-size:12.5px; color:var(--mute); } .ov-row .ov-tx p { margin-top:4px; font-size:12.5px; color:#c4bfe0; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }
.ov-ok { margin-top:30px; padding:14px 18px; border-radius:18px; background:rgba(182,255,59,.07); border:1px solid rgba(182,255,59,.2); color:var(--lime); font-weight:600; font-size:14px; }
.ov-errors { margin-top:30px; } .ov-errors ul { display:grid; gap:9px; margin:0; padding:0; list-style:none; } .ov-errors li { display:grid; gap:6px; padding:12px 14px; border-radius:16px; background:rgba(255,61,154,.08); border:1px solid rgba(255,61,154,.28); font-size:13px; overflow-wrap:anywhere; } .ov-errors li span { color:var(--mute); } .ov-errors li em { color:#ffb3d3; font-style:normal; } .ov-errors li a { width:max-content; font-weight:700; text-decoration:underline; } .ov-failed { display:grid; gap:3px; padding:8px 10px; border-radius:10px; background:rgba(255,255,255,.06); } .ov-failed span { font-size:11px; font-weight:700; color:var(--mute); } .ov-failed em { font-size:12px; }
.ov-modal { position:fixed; inset:0; z-index:50; display:flex; align-items:flex-end; justify-content:center; background:rgba(4,3,10,.6); backdrop-filter:blur(8px); } .ov-modal[hidden] { display:none; }
.ov-sheet { width:min(800px,100%); max-height:94vh; overflow-y:auto; padding:18px 20px 16px; border-radius:26px 26px 0 0; background:linear-gradient(170deg,color-mix(in srgb,var(--c) 16%,#0f0c1f),#0a0814 55%); border:1px solid var(--edge); animation:ov-sheet-in .45s cubic-bezier(.2,1.1,.3,1); } @keyframes ov-sheet-in { from { transform:translateY(60px) scale(.98); opacity:.4; } }
@media (min-width:820px) { .ov-modal { align-items:center; } .ov-sheet { border-radius:26px; } }
.ov-d-top { display:flex; align-items:center; gap:14px; } .ov-d-top .ov-av { width:38px; height:38px; border-radius:13px; font-size:15px; } .ov-d-top h3 { flex:1; min-width:0; margin:0; font:700 17px/1.25 var(--display); display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }
.ov-depo { margin-left:8px; padding:3px 10px; border-radius:99px; background:rgba(255,200,60,.16); color:#ffd35c; font:700 11.5px var(--display); vertical-align:middle; }
.ov-d-link { flex:none; display:grid; place-items:center; height:38px; padding:0 14px; border-radius:12px; background:linear-gradient(95deg,var(--lime),var(--cyan)); color:#07110a !important; font:800 13px var(--display); white-space:nowrap; text-decoration:none; }
.ov-x { flex:none; width:38px; height:38px; border-radius:50%; background:var(--glass2); border:1px solid var(--edge); color:#fff; font-size:20px; cursor:pointer; }
.ov-d-price { display:flex; flex-wrap:wrap; align-items:center; gap:8px; margin:12px 0 4px; } .ov-d-price b { font:800 38px/1 var(--display); letter-spacing:-.05em; } .ov-d-price small { color:var(--mute); }
.ov-meter { margin-top:10px; } .ov-meter-track { height:10px; border-radius:99px; background:var(--glass2); overflow:hidden; } .ov-meter-fill { height:100%; border-radius:99px; background:linear-gradient(90deg,var(--c),var(--lime)); animation:ov-fill 1.2s .3s cubic-bezier(.2,1,.3,1) backwards; } @keyframes ov-fill { from { width:0; } } .ov-meter p { display:flex; justify-content:space-between; margin-top:7px; font-size:12.5px; color:var(--mute); }
.ov-facts { display:grid; grid-template-columns:repeat(4,1fr); gap:8px; margin:12px 0; } .ov-fact { padding:8px 11px; border-radius:16px; background:var(--glass); border:1px solid var(--edge); } .ov-fact span { display:block; font-size:11px; color:var(--mute); } .ov-fact b { font:700 14px var(--display); letter-spacing:-.02em; } .ov-fact .priority-dot { margin-right:6px; }
.ov-chartbox { position:relative; padding:8px; border-radius:20px; background:rgba(0,0,0,.28); border:1px solid var(--edge); color:var(--c); } .ov-chart { display:block; width:100%; height:auto; max-height:calc(94vh - 340px); min-height:40px; } .ov-note { padding:18px; text-align:center; color:var(--mute); }
.ov-first .ov-tile { opacity:0; transform:translateY(16px); animation:ov-rise .6s cubic-bezier(.2,.9,.3,1) forwards; } .ov-first .ov-tile:nth-child(n) { animation-delay:calc(var(--n,0) * 35ms); } @keyframes ov-rise { to { opacity:1; transform:none; } }
.ov-sheet .ov-draw, .ov-first .ov-draw { stroke-dasharray:1; stroke-dashoffset:1; animation:ov-draw 1.4s .15s ease forwards; } @keyframes ov-draw { to { stroke-dashoffset:0; } }
.ov-sheet .ov-fade, .ov-first .ov-fade { opacity:0; animation:ov-fi .8s .9s forwards; } @keyframes ov-fi { to { opacity:1; } }
.ov-chart { touch-action:pan-y; cursor:crosshair; } .ov-tip { position:absolute; z-index:2; pointer-events:none; padding:7px 11px; border-radius:12px; background:rgba(10,8,20,.94); border:1px solid var(--edge); box-shadow:0 8px 24px rgba(0,0,0,.5); font:600 13px var(--display); white-space:nowrap; transform:translate(-50%,-100%); } .ov-tip small { display:block; font:500 11px var(--body); color:var(--mute); } .ov-tip[hidden] { display:none; }
@media (max-width:820px) { .ov-hero { flex-direction:column; align-items:flex-start; } .ov-facts { grid-template-columns:repeat(3,1fr); } }
@media (max-width:560px) { body.ov main { padding:0 14px 60px; } body.ov.public .hero { padding:0; } .ov-nums { gap:8px; width:100%; } .ov-num { flex:1; min-width:0; padding:12px; } .ov-num b { font-size:26px; } .ov-deal .ov-price { font-size:28px; } .ov-sheet { padding:16px 14px 14px; } .ov-d-price b { font-size:32px; } .ov-fact b { font-size:13px; } .ov-chart { max-height:calc(94vh - 400px); } .ov-status { margin-left:0; } }
@media (prefers-reduced-motion:reduce) { body.ov *, body.ov::before, body.ov::after { animation-duration:.01s !important; animation-delay:0s !important; } }
""".strip()

# Ayarlar wears the same skin as the home screen: only colors, shapes, fonts and motion change;
# the form markup and its scripts are untouched.
SETTINGS_SKIN_CSS = """
body.ov { --line:rgba(255,255,255,.1); --text:#f4f2ff; --muted:#9a95b8; --card:rgba(255,255,255,.055); --panel:transparent; --accent:#b6ff3b; --accent2:#27e1ff; --bad:#ff7ab8; --ok:#b6ff3b; }
body.ov .page-heading { margin:6px 0 14px; font:800 clamp(28px,4vw,40px)/1.05 var(--display); letter-spacing:-.045em; }
.settings-page .settings-section { margin-top:16px; padding:18px; border-radius:22px; border:1px solid var(--edge); background:var(--glass); backdrop-filter:blur(10px); }
.settings-page .settings-section h2 { margin:0 0 12px; font:700 13px var(--display); letter-spacing:.14em; text-transform:uppercase; color:var(--mute); }
.settings-page details { margin:10px 0; border-radius:18px; border:1px solid var(--edge); background:rgba(255,255,255,.045); transition:border-color .25s,background .25s; }
.settings-page details:hover { border-color:rgba(255,255,255,.2); } .settings-page details[open] { border-color:rgba(182,255,59,.45); background:rgba(255,255,255,.07); }
.settings-page summary { display:flex; align-items:center; padding:14px 16px; font:600 14px var(--display); letter-spacing:-.01em; color:var(--text); }
.settings-page summary::before { content:'▸'; flex:none; margin-right:10px; color:var(--mute); transition:transform .2s; }
.settings-page summary .priority-dot { width:10px; height:10px; margin:0 9px 0 0; box-shadow:0 0 10px currentColor; }
.settings-page .priority-cycle { color:#ff5c64; } .settings-page .priority-30m { color:#ff9548; } .settings-page .priority-60m { color:#f2c94c; } .settings-page .priority-3h { color:#c4dc4a; } .settings-page .priority-6h { color:#3fbf6a; }
.settings-page .form-grid { padding:2px 16px 16px; }
.settings-page label { font:500 12px var(--body); color:var(--mute); }
.settings-page input[type='text'], .settings-page input[type='number'], .settings-page input[type='url'], .settings-page input[type='search'], .settings-page select, .settings-page textarea, .watch-search input, .watch-priority select { min-height:42px; border-radius:13px; border:1px solid var(--edge); background:rgba(0,0,0,.28); color:var(--text); font:14px var(--body); transition:border-color .2s,box-shadow .2s; }
.settings-page input:focus, .settings-page select:focus, .settings-page textarea:focus { outline:0; border-color:var(--cyan); box-shadow:0 0 0 3px rgba(39,225,255,.18); }
.settings-page input::placeholder { color:var(--faint); }
.settings-page input[type='checkbox'] { width:18px; height:18px; accent-color:var(--lime); }
.settings-page .checkbox-row { color:var(--text); font-size:13.5px; }
body.ov .button, body.ov .button.test { min-height:42px; padding:0 16px; border-radius:14px; border:1px solid var(--edge); background:var(--glass2); color:var(--text); font:700 13px var(--display); transition:transform .2s,border-color .2s; }
body.ov .button:hover { transform:translateY(-2px); border-color:rgba(255,255,255,.28); }
body.ov .button.primary, .period-switch .button[aria-current='page'] { border-color:transparent; background:linear-gradient(95deg,var(--lime),var(--cyan)); color:#07110a; }
body.ov .button.danger { border-color:transparent; background:linear-gradient(95deg,#ff3d9a,#ff6b6b); color:#fff; }
body.ov .notice { margin:0 0 12px; border-radius:16px; }
body.ov .notice-ok { color:var(--lime); background:rgba(182,255,59,.08); border-color:rgba(182,255,59,.25); } body.ov .notice-fail { color:#ffb3d3; background:rgba(255,61,154,.1); border-color:rgba(255,61,154,.3); }
.settings-page .watch-search { color:var(--mute); font-weight:500; } .settings-page .watch-search input { background:rgba(0,0,0,.28); }
.settings-page .watch-group-filter { min-height:36px; padding:0 14px; border-radius:99px; border:1px solid var(--edge); background:var(--glass); color:var(--text); font:600 13px var(--display); transition:transform .2s,background .2s; } .settings-page .watch-group-filter:hover { transform:translateY(-2px); border-color:rgba(255,255,255,.28); }
.settings-page .watch-group-filter[aria-pressed='true'] { background:rgba(182,255,59,.14); border-color:rgba(182,255,59,.45); } .settings-page .watch-group-filter[aria-pressed='false'] { background:transparent; color:var(--faint); opacity:.8; }
.settings-page .watch-hint, .settings-page .apply-bar p { color:var(--mute); }
.settings-page .apply-bar { bottom:10px; margin-top:20px; padding:12px 14px; border:1px solid var(--edge); border-radius:20px; background:rgba(14,11,28,.82); backdrop-filter:blur(14px); box-shadow:0 12px 40px rgba(0,0,0,.45); }
.settings-page .tool-actions { margin-top:22px; }
body.ov .footer-note { border-left:0; border-radius:16px; background:var(--glass); border:1px solid var(--edge); color:var(--mute); }
.settings-page .saving-dialog { border-color:var(--edge); border-radius:24px; background:linear-gradient(170deg,#161233,#0a0814); } .settings-page .saving-spinner { border-color:rgba(255,255,255,.14); border-top-color:var(--lime); }
.settings-page details.is-deleted summary { color:#ff7ab8; }
""".strip()

APP_CSS = APP_CSS + "\n" + OVERVIEW_CSS

# Every other page (İstatistik, the restart screen) wears the same skin.
STATISTICS_SKIN_CSS = """
body.ov { --ok:#b6ff3b; --warn:#ffc857; --head:rgba(255,255,255,.07); }
.ov .summary-panel { margin-top:16px; padding:18px; border-radius:22px; border:1px solid var(--edge); background:var(--glass); backdrop-filter:blur(10px); }
.ov .summary-head { align-items:center; } .ov .summary-head h2 { margin:0; font:700 13px var(--display); letter-spacing:.14em; text-transform:uppercase; color:var(--mute); } .ov .summary-head span { color:var(--mute); }
.ov .statistics-intro { max-width:640px; font-size:14px; }
.ov .statistics-top { margin-top:6px; } .ov .statistics-top .page-heading { margin:0; }
.ov .period-switch .button[aria-current='page'] { border-color:transparent; }
.ov .stat-tile { padding:14px 16px; border-radius:20px; border:1px solid var(--edge); background:var(--glass); backdrop-filter:blur(10px); }
.ov .stat-tile span { font:600 11px var(--body); letter-spacing:.08em; } .ov .stat-tile strong { font:800 28px/1.05 var(--display); letter-spacing:-.04em; } .ov .stat-tile small { color:var(--mute); }
.ov .stat-tile-alert strong { color:#ff7ab8; }
.ov .statistics-chart { border:1px solid var(--edge); border-radius:18px; background:rgba(0,0,0,.28); }
.ov .statistics-chart .grid-line { stroke:rgba(255,255,255,.08); } .ov .statistics-chart .axis-line { stroke:rgba(255,255,255,.2); }
.ov .statistics-chart .check-bar { fill:#7be0ff; } .ov .statistics-chart .failure-count { fill:#ff7ab8; }
.ov .legend-bar { background:#7be0ff; } .ov .legend-count { color:#ff7ab8; }
.ov .site-health { padding:14px 16px; border-radius:18px; border:1px solid var(--edge); background:rgba(255,255,255,.045); }
.ov .site-health header strong { font:700 15px var(--display); } .ov .health-bar { height:9px; border-radius:99px; background:var(--glass2); }
.ov .health-ok { background:linear-gradient(95deg,var(--lime),var(--cyan)); } .ov .health-blocked { background:var(--warn); } .ov .health-error { background:#ff3d9a; }
.ov .site-health dd { font-family:var(--display); } .ov .site-health dd.bad, .ov .measure-table td.bad { color:#ff7ab8; } .ov .statistics-good { color:var(--lime); }
.ov .table-wrap, .ov .measure-wrap { border:1px solid var(--edge); border-radius:18px; } .ov th { background:rgba(255,255,255,.07); color:var(--mute); font-family:var(--body); } .ov td { border-bottom-color:var(--edge); }
.ov .daily-history { margin-top:16px; padding:14px 18px; border-radius:22px; border:1px solid var(--edge); background:var(--glass); } .ov .daily-history summary { font:700 14px var(--display); }
.ov .error-spells li { border-radius:16px; border:1px solid rgba(255,61,154,.28); border-left:3px solid #ff3d9a; background:rgba(255,61,154,.07); } .ov .error-spells li span { color:#ff7ab8; }
.ov .spell-title { font:700 13px var(--display); letter-spacing:.14em; text-transform:uppercase; color:var(--mute); }
.ov .statistics-empty { color:var(--mute); }
""".strip()
APP_CSS = APP_CSS + "\n" + SETTINGS_SKIN_CSS + "\n" + STATISTICS_SKIN_CSS

OVERVIEW_SCRIPT = """
(() => {
  const state = { tab: 'watch', filter: 'all' };
  const modal = document.getElementById('ov-modal');
  const sheet = document.getElementById('ov-sheet');
  const region = document.getElementById('live-region');
  if (!modal || !sheet || !region) return;
  const apply = () => {
    region.querySelectorAll('[data-tab]').forEach((tab) => tab.setAttribute('aria-selected', String(tab.dataset.tab === state.tab)));
    region.querySelectorAll('[data-pane]').forEach((pane) => { pane.hidden = pane.dataset.pane !== state.tab; });
    region.querySelectorAll('[data-filter]').forEach((chip) => chip.setAttribute('aria-pressed', String(chip.dataset.filter === state.filter)));
    region.querySelectorAll('.ov-tile').forEach((tile) => { tile.hidden = state.filter !== 'all' && tile.dataset.site !== state.filter; });
    region.querySelectorAll('.ov-group').forEach((group) => { group.hidden = !group.querySelector('.ov-tile:not([hidden])'); });
  };
  const close = () => { modal.hidden = true; sheet.innerHTML = ''; document.documentElement.style.overflow = ''; };
  const open = (card) => {
    const detail = card.querySelector('.ov-detail');
    if (!detail) return;
    sheet.style.setProperty('--c', getComputedStyle(card).getPropertyValue('--c'));
    sheet.innerHTML = detail.innerHTML;
    modal.hidden = false;
    document.documentElement.style.overflow = 'hidden';
  };
  document.addEventListener('click', (event) => {
    const target = event.target;
    if (target === modal || target.closest('[data-close]')) return close();
    if (target.closest('#ov-sheet')) return;
    const tab = target.closest('[data-tab]');
    if (tab) { state.tab = tab.dataset.tab; return apply(); }
    const chip = target.closest('[data-filter]');
    if (chip) { state.filter = chip.dataset.filter; return apply(); }
    const card = target.closest('[data-open]');
    if (card && !target.closest('a')) open(card);
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !modal.hidden) close();
    if ((event.key === 'Enter' || event.key === ' ') && event.target.matches && event.target.matches('[data-open]')) { event.preventDefault(); open(event.target); }
  });
  const fmtPrice = (value) => `${Math.round(value).toLocaleString('tr-TR')} TL`;
  const fmtDate = (seconds) => new Date(seconds * 1000).toLocaleString('tr-TR', { day: 'numeric', month: 'long', year: 'numeric', hour: '2-digit', minute: '2-digit' });
  // Hovering (or dragging a finger over) the chart shows the price that held at that moment.
  const probe = (event) => {
    const chart = event.target.closest && event.target.closest('.ov-chart');
    const box = sheet.querySelector('.ov-chartbox');
    const tip = box && box.querySelector('.ov-tip');
    if (!chart || !box) { if (tip) tip.hidden = true; sheet.querySelectorAll('.ov-cross,.ov-cross-dot').forEach((item) => item.setAttribute('opacity', '0')); return; }
    const points = JSON.parse(chart.dataset.points || '[]');
    if (!points.length) return;
    const rect = chart.getBoundingClientRect();
    const scale = chart.viewBox.baseVal.width / rect.width;
    const x = (event.clientX - rect.left) * scale;
    let hit = points[0];
    for (const point of points) { if (point[0] <= x) hit = point; }
    const cross = chart.querySelector('.ov-cross');
    const dot = chart.querySelector('.ov-cross-dot');
    const moment = Math.max(points[0][2], Math.min(points[points.length - 1][2], points[0][2] + (points[points.length - 1][2] - points[0][2]) * ((x - points[0][0]) / ((points[points.length - 1][0] - points[0][0]) || 1))));
    cross.setAttribute('x1', x); cross.setAttribute('x2', x);
    cross.setAttribute('y1', chart.dataset.top); cross.setAttribute('y2', chart.dataset.bottom);
    dot.setAttribute('cx', x); dot.setAttribute('cy', hit[1]);
    cross.setAttribute('opacity', '1'); dot.setAttribute('opacity', '1');
    let bubble = tip;
    if (!bubble) { bubble = document.createElement('div'); bubble.className = 'ov-tip'; box.appendChild(bubble); }
    bubble.hidden = false;
    bubble.innerHTML = `${fmtPrice(hit[3])}<small>${fmtDate(moment)}</small>`;
    const boxRect = box.getBoundingClientRect();
    const left = Math.max(70, Math.min(boxRect.width - 70, event.clientX - boxRect.left));
    bubble.style.left = `${left}px`;
    bubble.style.top = `${hit[1] / scale + (rect.top - boxRect.top) - 12}px`;
  };
  sheet.addEventListener('pointermove', probe);
  sheet.addEventListener('pointerdown', probe);
  sheet.addEventListener('pointerleave', () => probe({ target: document.body }));
  document.addEventListener('hermes-live', apply);
  apply();
  // The entrance animation plays once; later in-place refreshes must not replay it.
  window.setTimeout(() => document.body.classList.remove('ov-first'), 2500);
})();
""".strip()
