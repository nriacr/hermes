"""Stylesheet and scripts shared by every page of the ingress and public panel."""

APP_CSS = """
:root { color-scheme:dark; --bg:#111315; --panel:#1a1c1f; --card:#24272b; --line:#3c4147; --text:#f5f7fa; --muted:#b0b6be; --accent:#e4e5e3; --accent2:#b8bbba; --ok:#86dfb7; --warn:#ffd07a; --bad:#ff9caf; --head:#30343a; }
* { box-sizing:border-box; } body { margin:0; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; background:radial-gradient(circle at top left,#373b40,var(--bg) 58%); color:var(--text); font-size:14px; }
main { max-width:1060px; margin:0 auto; padding:28px 18px 44px; } .hero { border:1px solid var(--line); border-radius:22px; padding:22px; background:var(--panel); box-shadow:0 18px 42px rgba(0,0,0,.35); }
p { margin:0; color:var(--muted); line-height:1.5; font-size:13px; }
.badge { display:inline-flex; margin-bottom:12px; color:#17191b; background:linear-gradient(135deg,#ffd166,#f0ad61); border-radius:18px; padding:9px 15px; font-size:clamp(24px,5vw,42px); line-height:1; letter-spacing:-.04em; font-weight:900; }
.actions { display:flex; flex-wrap:wrap; gap:10px; margin-top:18px; align-items:center; } .inline-form { margin:0; } .button { display:inline-flex; align-items:center; justify-content:center; min-height:40px; padding:0 14px; border-radius:13px; border:1px solid transparent; text-decoration:none; font-weight:800; font-size:13px; cursor:pointer; }
.button.primary { color:#181a1c; background:linear-gradient(135deg,var(--accent),var(--accent2)); } .button.secondary { color:var(--text); background:#35393d; border-color:var(--line); } .button.test { color:#f5f7fa; background:linear-gradient(135deg,#54595e,#35393d); border-color:#6b7075; }
.notice { margin-top:14px; padding:11px 13px; border-radius:12px; font-weight:700; font-size:13px; } .notice-ok { color:#c6f7e6; background:rgba(127,220,184,.14); border:1px solid rgba(127,220,184,.38); } .notice-fail { color:#ffd8e3; background:rgba(255,156,181,.14); border:1px solid rgba(255,156,181,.38); }
.link-test-form { display:flex; align-items:end; flex-wrap:wrap; gap:10px; } .link-test-form label { flex:1 1 520px; display:grid; gap:7px; color:var(--muted); font-size:12px; font-weight:750; } .link-test-form .link-test-url { flex-basis:100%; } .link-test-form input { width:100%; min-height:42px; padding:10px 12px; color:var(--text); background:#151719; border:1px solid var(--line); border-radius:12px; font:inherit; } .link-test-form input:focus { outline:2px solid rgba(228,229,227,.38); outline-offset:1px; } .link-test-options { display:grid; grid-template-columns:2fr 1fr 2fr auto; gap:10px; width:100%; align-items:end; } .link-test-options label { min-width:0; flex:initial; } .link-test-options .link-test-checkbox { display:flex; align-items:center; gap:7px; min-height:42px; padding:0 12px; white-space:nowrap; border:1px solid var(--line); border-radius:12px; background:#25282b; color:var(--text); } .link-test-options .link-test-checkbox input { width:16px; min-height:16px; padding:0; accent-color:var(--accent); }
.grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:11px; margin-top:16px; } .card { border:1px solid var(--line); border-radius:15px; padding:14px; background:var(--card); min-height:82px; } .card span { display:block; color:var(--muted); font-size:12px; margin-bottom:8px; } .card strong { display:block; font-size:19px; line-height:1.18; overflow-wrap:anywhere; }
.card.status-ok { border-color:rgba(127,220,184,.38); background:linear-gradient(135deg,rgba(127,220,184,.12),var(--card) 62%); } .card.status-ok strong { color:var(--ok); } .card.status-warn strong { color:var(--warn); } .card.status-error strong { color:var(--bad); }
.error-card { grid-column:1 / -1; } .error-card ul { display:grid; gap:9px; margin:10px 0 0; padding:0; list-style:none; color:var(--text); } .error-card li { display:grid; gap:6px; padding:10px 12px; border:1px solid rgba(255,156,181,.28); border-radius:12px; background:rgba(255,156,181,.08); font-size:12px; line-height:1.35; overflow-wrap:anywhere; } .error-card li.empty-error { border-color:rgba(127,220,184,.26); background:rgba(127,220,184,.08); color:var(--muted); } .error-card li strong { font-size:13px; color:var(--text); } .error-card li span { margin:0; color:var(--muted); } .error-card li em { color:#ffd8e3; font-style:normal; } .error-card li a { color:#e5e7e8; font-weight:800; text-decoration:none; width:max-content; } .error-card li a:hover { color:#ffd166; text-decoration:underline; } .failed-link { display:grid; gap:3px; margin-top:4px; padding:8px 10px; border-radius:10px; background:rgba(220,223,224,.08); border:1px solid rgba(220,223,224,.20); } .failed-link span { color:#e0e2e3; font-weight:800; font-size:11px; } .failed-link strong { color:#f5f7fa; font-size:12px; } .failed-link em { color:#c8cccf; font-size:11px; }
.public-error-card { margin-top:18px; }
.summary-panel { margin-top:18px; border:1px solid var(--line); border-radius:18px; padding:16px; background:var(--card); } .summary-head { display:flex; align-items:flex-end; justify-content:space-between; gap:12px; margin-bottom:12px; } .summary-head h2 { font-size:18px; margin:0; } .summary-head span { color:var(--muted); font-size:12px; white-space:nowrap; } .table-section + .table-section { margin-top:18px; } .table-section h3 { margin:0 0 9px; font-size:14px; color:#f0f1f0; } .deals-section h3 { color:#b7f0dc; }
.telegram-recent { margin-top:14px; border:1px solid rgba(210,213,213,.22); border-radius:14px; padding:13px; background:rgba(20,22,24,.46); } .telegram-recent h3 { margin:0 0 10px; font-size:13px; color:#f0f1f0; } .telegram-recent p { color:var(--muted); } .telegram-recent ul { display:grid; gap:8px; margin:0; padding:0; list-style:none; } .telegram-recent li { display:grid; gap:4px; padding:10px 11px; border:1px solid rgba(210,213,213,.20); border-radius:12px; background:rgba(210,213,213,.06); } .telegram-recent li a,.telegram-recent li strong { color:#f1f3f3; font-size:13px; font-weight:850; text-decoration:none; overflow-wrap:anywhere; } .telegram-recent li a:hover { color:#ffd166; text-decoration:underline; } .telegram-recent li span { color:var(--muted); font-size:11px; } .telegram-recent li em { color:#d9dcdd; font-size:12px; font-style:normal; line-height:1.35; overflow-wrap:anywhere; }
.table-wrap { overflow-x:auto; border:1px solid var(--line); border-radius:14px; } table { width:100%; border-collapse:collapse; min-width:930px; } th,td { padding:8px 8px; border-bottom:1px solid var(--line); text-align:right; white-space:nowrap; } th { color:#e1e3e3; background:var(--head); font-size:11px; text-transform:uppercase; letter-spacing:.035em; } td { color:var(--text); font-size:13px; font-variant-numeric:tabular-nums; } tr:last-child td { border-bottom:none; } th:nth-child(1),td:nth-child(1) { width:104px; } th:nth-child(1),td:nth-child(1),th:nth-child(2),td:nth-child(2) { text-align:left; } th:not(:nth-child(2)),td:not(:nth-child(2)) { width:100px; } th:nth-child(6),td:nth-child(6) { width:148px; } th:nth-child(7),td:nth-child(7) { width:92px; } .empty-row td { color:var(--muted); text-align:left; background:rgba(255,255,255,.025); }
@media (min-width:721px) { .price-summary-table { min-width:918px; } .price-summary-table th { line-height:1.2; } .price-summary-table th:nth-child(3),.price-summary-table td:nth-child(3),.price-summary-table th:nth-child(4),.price-summary-table td:nth-child(4),.price-summary-table th:nth-child(5),.price-summary-table td:nth-child(5) { width:96px; } }
.search-result-group { margin:10px 0; overflow:hidden; border:1px solid rgba(210,213,213,.38); border-radius:14px; background:rgba(210,213,213,.06); } .search-result-group summary { display:flex; align-items:center; justify-content:space-between; gap:10px; padding:12px 14px; color:#f0f1f0; font-size:13px; font-weight:850; cursor:pointer; list-style:none; } .search-result-group summary::-webkit-details-marker { display:none; } .search-result-group summary::before { content:'▸'; display:inline-block; margin-right:8px; color:#d6d8d7; font-size:16px; transition:transform .16s ease; } .search-result-group[open] summary::before { transform:rotate(90deg); } .search-result-group summary strong { margin-right:auto; } .search-result-group summary span { color:var(--muted); font-size:11px; font-weight:750; white-space:nowrap; } .search-result-group[open] summary { border-bottom:1px solid rgba(210,213,213,.25); background:rgba(210,213,213,.09); } .search-result-group .table-wrap { border:0; border-radius:0; }
tbody tr.site-amazon { --site-bg:rgba(247,197,109,.13); --site-bg-strong:rgba(247,197,109,.24); --site-line:rgba(247,197,109,.84); --site-link:#ffd482; }
tbody tr.site-hepsiburada { --site-bg:rgba(255,154,111,.13); --site-bg-strong:rgba(255,154,111,.25); --site-line:rgba(255,154,111,.86); --site-link:#ffad82; }
tbody tr.site-trendyol { --site-bg:rgba(246,163,199,.13); --site-bg-strong:rgba(246,163,199,.25); --site-line:rgba(246,163,199,.84); --site-link:#f8b4d0; }
tbody tr.site-network { --site-bg:rgba(133,220,207,.13); --site-bg-strong:rgba(133,220,207,.25); --site-line:rgba(133,220,207,.84); --site-link:#a6ebe0; }
tbody tr.site-beymenclub { --site-bg:rgba(230,176,137,.13); --site-bg-strong:rgba(230,176,137,.25); --site-line:rgba(230,176,137,.84); --site-link:#f1c49f; }
tbody tr.site-nordbron { --site-bg:rgba(143,190,255,.13); --site-bg-strong:rgba(143,190,255,.25); --site-line:rgba(143,190,255,.84); --site-link:#b8d4ff; }
tbody tr.site-zara { --site-bg:rgba(176,218,139,.13); --site-bg-strong:rgba(176,218,139,.25); --site-line:rgba(176,218,139,.84); --site-link:#c9ec9f; }
tbody tr.site-hm { --site-bg:rgba(214,178,255,.13); --site-bg-strong:rgba(214,178,255,.25); --site-line:rgba(214,178,255,.84); --site-link:#dec4ff; }
tbody tr.site-other { --site-bg:rgba(183,177,222,.13); --site-bg-strong:rgba(183,177,222,.22); --site-line:rgba(183,177,222,.72); --site-link:#d1caff; } tbody tr[class*='site-'] td { background:linear-gradient(90deg,var(--site-bg),rgba(36,39,43,.40)); } tbody tr[class*='site-'] td:first-child { border-left:4px solid var(--site-line); color:var(--site-link); font-weight:800; } tbody tr[class*='site-'] .product-cell a { color:var(--site-link); } tbody tr[class*='site-']:hover td { background:linear-gradient(90deg,rgba(255,255,255,.055),var(--site-bg)); }
.product-cell { max-width:360px; white-space:normal; line-height:1.22; } .product-cell a { color:#e4e6e6; text-decoration:none; } .product-cell a:hover { color:#ffd166; text-decoration:underline; } .product-cell .product-title { display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; text-overflow:ellipsis; } .product-cell .warehouse-tag { display:inline-block; margin:0 8px 0 0; padding:0 7px; border-radius:5px; background:rgba(236,183,82,.20); color:#fff; font-size:13px; font-weight:900; letter-spacing:.05em; line-height:1.1; vertical-align:top; } .product-cell .priority-dot { display:inline-block; width:8px; height:8px; margin:0 7px 1px 0; border-radius:50%; vertical-align:middle; } .priority-high { background:#ff5c64; } .priority-medium { background:#f2c94c; } .priority-low { background:#57cc7a; } .deal-row td { color:#b7f0dc; } .deal-row td:first-child { color:var(--site-link); } .deal-row .product-cell a { color:#b7f0dc; } .note { margin-top:18px; border-left:4px solid #a9adaf; padding:12px 14px; background:rgba(169,173,175,.14); border-radius:10px; font-size:13px; } .footer { margin-top:18px; font-size:12px; color:var(--muted); }
.public main { max-width:1180px; } .public .hero { padding:18px; } .public .badge { font-size:clamp(22px,4vw,36px); }
.public-actions { margin:16px 0 6px; } .public-actions .button { min-width:132px; }
.public-cycle-row { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; margin:10px 0 4px; }
.public-cycle-pill { min-width:0; min-height:40px; padding:7px 10px; border:1px solid var(--line); border-radius:13px; background:#35393d; }
.public-cycle-pill span { display:block; color:var(--muted); font-size:10px; font-weight:800; letter-spacing:.035em; text-transform:uppercase; }
.public-cycle-pill strong { display:block; margin-top:2px; font-size:14px; line-height:1.1; color:var(--text); }
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
  .public-cycle-row { grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; margin:12px 0 4px; }
  .public-cycle-pill { min-height:66px; padding:12px 13px; border-radius:15px; }
  .public-cycle-pill span { font-size:11px; }
  .public-cycle-pill strong { margin-top:5px; font-size:20px; }
  .link-test-options { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .link-test-options .link-test-checkbox { min-height:42px; }
  .link-test-result tbody tr[class*='site-'] { grid-template-columns:1fr; }
  .link-test-result tbody tr[class*='site-'] .price-cell { grid-column:1 / -1; }
  .grid { grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; }
  .card { min-height:70px; padding:11px; border-radius:13px; }
  .card span { font-size:11px; margin-bottom:6px; }
  .card strong { font-size:15px; }
  .summary-panel { margin-top:12px; padding:11px; border-radius:15px; }
  .summary-head { align-items:flex-start; flex-direction:column; gap:4px; margin-bottom:10px; }
  .summary-head h2 { font-size:16px; }
  .summary-head span { white-space:normal; font-size:11px; }
  .public .summary-head span { font-size:16px; line-height:1.3; color:#ffd166; font-weight:800; }
  .table-section h3 { font-size:13px; }
  .table-wrap { overflow:visible; border:0; border-radius:0; }
  .search-result-group { margin:8px 0; border-radius:13px; }
  .search-result-group summary { min-height:48px; padding:12px; font-size:13px; }
  .search-result-group summary span { font-size:11px; }
  table { min-width:0; }
  thead { display:none; }
  table, tbody, td { display:block; width:100%; }
  tbody tr[class*='site-'] { position:relative; display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:6px 8px; margin:0 0 8px; border:1px solid var(--site-line); border-left:7px solid var(--site-line); border-radius:15px; padding:9px 10px 9px 12px; background:linear-gradient(135deg,var(--site-bg-strong),rgba(36,39,43,.92) 58%),rgba(36,39,43,.90); box-shadow:0 8px 20px rgba(0,0,0,.18); overflow:hidden; }
  tbody tr[class*='site-'] td { display:flex; width:100%; min-width:0; justify-content:flex-start; gap:4px; padding:0; border-bottom:0; background:transparent; text-align:left; white-space:normal; overflow-wrap:anywhere; font-size:13.5px; line-height:1.18; }
  tbody tr[class*='site-'] td:first-child { border-left:0; color:var(--site-link); }
  tbody tr[class*='site-'] td::before { content:attr(data-label); flex:0 0 auto; color:var(--muted); text-align:left; font-size:10px; font-weight:850; letter-spacing:.045em; text-transform:uppercase; }
  tbody tr[class*='site-'] .seller-cell { grid-column:1 / -1; align-items:center; gap:0; padding-bottom:0; color:var(--site-link); font-size:15px; font-weight:900; }
  tbody tr[class*='site-'] .seller-cell::before, tbody tr[class*='site-'] .product-cell::before { display:none; }
  tbody tr[class*='site-'] .product-cell { grid-column:1 / -1; max-width:none; display:block; padding-bottom:0; text-align:left; line-height:1.22; font-size:14px; }
  tbody tr[class*='site-'] .price-cell, tbody tr[class*='site-'] .target-cell, tbody tr[class*='site-'] .diff-cell, tbody tr[class*='site-'] .range-cell { min-height:33px; border:1px solid rgba(255,255,255,.06); border-radius:10px; padding:5px 7px; background:rgba(20,22,24,.42); flex-direction:column; justify-content:center; font-size:14.5px; }
  tbody tr[class*='site-'] .price-cell, tbody tr[class*='site-'] .target-cell, tbody tr[class*='site-'] .diff-cell { min-width:0; }
  tbody tr[class*='site-'] .range-cell { grid-column:1 / -1; min-height:31px; flex-direction:row; flex-wrap:wrap; align-items:center; justify-content:flex-start; gap:8px; white-space:normal; }
  tbody tr[class*='site-'] .range-cell::before { margin-right:3px; }
  .product-cell .product-title { -webkit-line-clamp:2; }
  .empty-row td { padding:10px; border:1px solid var(--line); border-radius:12px; }
  .note, .footer { font-size:11px; }
}

.statistics-intro { color:var(--muted); margin:10px 0 18px; }
.statistics-chart { width:100%; height:auto; display:block; border:1px solid var(--line); border-radius:14px; background:#202327; }
.statistics-chart text { fill:var(--muted); font-size:13px; font-family:inherit; }
.statistics-chart .grid-line { stroke:#41464b; stroke-width:1; }
.statistics-chart .cycle-line { fill:none; stroke:#ffd07a; stroke-width:3; stroke-linecap:round; stroke-linejoin:round; }
.statistics-chart .cycle-dot { fill:#ffd07a; }
.statistics-day-list { border:1px solid var(--line); border-radius:14px; overflow:hidden; }
.statistics-day-head,.statistics-day-grid { display:grid; grid-template-columns:1.1fr 1fr 1.5fr .7fr; gap:10px; align-items:center; }
.statistics-day-head { padding:9px 14px; background:var(--head); color:#e1e3e3; font-size:11px; font-weight:800; text-transform:uppercase; letter-spacing:.035em; }
.statistics-day { background:#202327; }
.statistics-day + .statistics-day { border-top:1px solid var(--line); }
.statistics-day summary { cursor:pointer; list-style:none; padding:11px 14px; }
.statistics-day summary::-webkit-details-marker { display:none; }
.statistics-day summary:hover { background:#2a2e32; }
.statistics-day[open] summary { background:#2a2e32; }
.statistics-day-grid > span { min-width:0; }
.statistics-day-grid strong { display:block; font-size:15px; font-variant-numeric:tabular-nums; white-space:nowrap; }
.statistics-day-grid .statistics-day-date strong { color:#ffd07a; }
.statistics-day-date .short-date { display:none; }
.statistics-day-grid small { display:block; margin-top:2px; color:var(--muted); font-size:10px; }
.statistics-day-grid .statistics-day-typical strong { color:#b7f0dc; }
.statistics-day-grid .statistics-day-slow strong:not(.zero) { color:#ff9caf; }
.statistics-day-grid .statistics-day-slow .zero { color:var(--muted); }
.statistics-day-detail { display:flex; flex-wrap:wrap; gap:7px 16px; padding:10px 14px; border-top:1px solid var(--line); color:var(--muted); font-size:12px; }
.statistics-day-detail strong { color:var(--text); font-variant-numeric:tabular-nums; }
.statistics-day-detail span:last-child { margin-left:auto; }
.statistics-table-wrap { max-height:340px; overflow:auto; border:0; border-top:1px solid var(--line); border-radius:0; }
.statistics-table { min-width:0; }
.statistics-table th,.statistics-table td { width:auto !important; text-align:left !important; }
.statistics-table th:last-child,.statistics-table td:last-child { text-align:right !important; }
.statistics-table thead { position:sticky; top:0; z-index:1; }
.statistics-empty { padding:14px; color:var(--muted); }
.measure-title { margin:16px 0 8px; font-size:14px; color:#e1e3e3; }
.measure-note { margin:12px 0 8px; }
.measure-wrap { border:1px solid var(--line); border-radius:14px; }
.measure-table th:not(:first-child),.measure-table td:not(:first-child) { text-align:right !important; }
.measure-table td.zero { color:var(--muted); }
.measure-table td.measure-alert { color:#ff9caf; }
.statistics-metrics { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:10px; margin-top:14px; }
.statistics-metrics .public-cycle-pill { min-height:64px; }
@media (max-width:720px) {
  .statistics-day-head,.statistics-day-grid { grid-template-columns:1.15fr .7fr 1.45fr .6fr; gap:4px; }
  .statistics-day-head { padding:8px 6px; font-size:10px; letter-spacing:0; }
  .statistics-day summary { padding:9px 6px; }
  .statistics-day-grid strong { font-size:13px; }
  .statistics-day-grid small { font-size:9px; }
  .statistics-day-detail span:last-child { margin-left:0; }
  .statistics-table { display:table; min-width:0; table-layout:fixed; }
  .statistics-table thead { display:table-header-group; }
  .statistics-table tbody { display:table-row-group; }
  .statistics-table tr { display:table-row; }
  .statistics-table th,.statistics-table td { display:table-cell; width:auto; padding:7px 5px; white-space:normal; font-size:11px; }
  .statistics-table-wrap { overflow:auto; }
  .statistics-metrics { grid-template-columns:1fr; gap:7px; }
  tbody tr[class*='site-'] .updated-cell { grid-column:1 / -1; min-height:29px; border:1px solid rgba(255,255,255,.06); border-radius:10px; padding:5px 7px; background:rgba(20,22,24,.42); align-items:center; }
}
@media (max-width:360px) {
  .statistics-day-date .full-date { display:none; }
  .statistics-day-date .short-date { display:inline; }
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
.nav-actions .button[aria-current='page'] { color:#181a1c; background:linear-gradient(135deg,var(--accent),var(--accent2)); border-color:transparent; }
.tool-actions { margin-top:10px; }
@media (max-width:900px) { .watch-top { grid-template-columns:repeat(2,minmax(0,1fr)); } }
@media (max-width:720px) { .nav-actions, .tool-actions { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); } .nav-actions .button, .tool-actions .button, .tool-actions .inline-form { width:100%; min-width:0; } }
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
  document.addEventListener('change', (event) => {
    const priority = event.target.closest('[data-watch-priority]');
    if (!priority) return;
    const card = priority.closest('[data-watch-card]');
    const legacyInterval = card?.querySelector('[data-legacy-check-interval]');
    if (legacyInterval) legacyInterval.value = '';
  });
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
