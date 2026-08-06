import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .config_loader import tracking_id_for_watch
from .constants import OPTIONS_PATH, PUSHOVER_URL, STATE_PATH, SUMMARY_PATH, TELEGRAM_ERROR_EVENTS_PATH, TELEGRAM_STATUS_PATH
from .logging_utils import log
from .link_test_ui import render_link_test_from_request, render_link_test_page
from .providers import hepsiburada as hepsiburada_provider
from .storage import load_json
from .web_assets import HERMES_ICON_PNG, HERMES_ICON_SVG, render_web_app_head, render_web_manifest
from .utils import (
    canonical_tracking_url,
    detect_site_from_url,
    is_amazon_search_url,
    is_hepsiburada_search_url,
    normalize_item_key,
    parse_bool,
    parse_iso_datetime,
    repair_mojibake,
    site_label,
    tracking_offer_identity,
)

WEB_PORT = 8099
RESET_NOTIFICATIONS_LOCK = threading.Lock()
PRICE_HISTORY_RESET_LOCK = threading.Lock()

DASHBOARD_CSS = """
:root { color-scheme:light; --bg:#F5F4FB; --panel:#FFFFFF; --card:#FBFAFF; --line:#E9E7F5; --text:#2B2A3D; --muted:#6B6980; --accent:#D6D4FB; --accent2:#C9EEDA; --ok:#146C43; --warn:#9A5B10; --bad:#B03A4A; --head:#F1EFFC; --ink:#2B2A3D; --ink-soft:#6B6980; --ink-faint:#9C9AB0; --surface:#FFFFFF; --surface-2:#FBFAFF; --mint-bg:#C9EEDA; --mint-ink:#146C43; --mint-soft:#E4F7ED; --indigo-bg:#D6D4FB; --indigo-ink:#4B47D6; --indigo-soft:#EDECFE; --peach-bg:#FBDFC0; --peach-ink:#9A5B10; --peach-soft:#FDEFDD; --rose-bg:#F9CDD4; --rose-ink:#B03A4A; --rose-soft:#FCE7EA; --radius-lg:20px; --radius-md:14px; --radius-sm:10px; }
* { box-sizing:border-box; } body { margin:0; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; background:radial-gradient(circle at top left,#EFEDFB,var(--bg) 58%); color:var(--text); font-size:14px; }
main { max-width:1060px; margin:0 auto; padding:28px 18px 44px; } .hero { border:1px solid var(--line); border-radius:22px; padding:22px; background:var(--panel); box-shadow:0 16px 36px rgba(43,42,61,.10); }
p { margin:0; color:var(--muted); line-height:1.5; font-size:13px; }
.badge { display:inline-flex; margin-bottom:12px; color:var(--indigo-ink); background:linear-gradient(135deg,var(--indigo-bg),var(--mint-bg)); border-radius:18px; padding:9px 15px; font-size:clamp(24px,5vw,42px); line-height:1; letter-spacing:-.04em; font-weight:900; }
.actions { display:flex; flex-wrap:wrap; gap:10px; margin-top:18px; align-items:center; } .inline-form { margin:0; } .button { display:inline-flex; align-items:center; justify-content:center; min-height:40px; padding:0 14px; border-radius:13px; border:1px solid transparent; text-decoration:none; font-weight:800; font-size:13px; cursor:pointer; }
.button.primary { color:#181a2c; background:linear-gradient(135deg,var(--indigo-bg),var(--accent2)); } .button.secondary { color:var(--text); background:var(--indigo-soft); border-color:var(--line); } .button.test { color:var(--text); background:linear-gradient(135deg,var(--mint-soft),var(--indigo-soft)); border-color:var(--line); }
.notice { margin-top:14px; padding:11px 13px; border-radius:12px; font-weight:700; font-size:13px; } .notice-ok { color:var(--mint-ink); background:var(--mint-soft); border:1px solid rgba(20,108,67,.28); } .notice-fail { color:var(--rose-ink); background:var(--rose-soft); border:1px solid rgba(176,58,74,.28); }
.link-test-form { display:flex; align-items:end; flex-wrap:wrap; gap:10px; } .link-test-form label { flex:1 1 520px; display:grid; gap:7px; color:var(--muted); font-size:12px; font-weight:750; } .link-test-form .link-test-url { flex-basis:100%; } .link-test-form input { width:100%; min-height:42px; padding:10px 12px; color:var(--text); background:var(--surface); border:1px solid var(--line); border-radius:12px; font:inherit; } .link-test-form input:focus { outline:2px solid rgba(75,71,214,.32); outline-offset:1px; } .link-test-options { display:grid; grid-template-columns:2fr 1fr 2fr auto; gap:10px; width:100%; align-items:end; } .link-test-options label { min-width:0; flex:initial; } .link-test-options .link-test-checkbox { display:flex; align-items:center; gap:7px; min-height:42px; padding:0 12px; white-space:nowrap; border:1px solid var(--line); border-radius:12px; background:var(--indigo-soft); color:var(--text); } .link-test-options .link-test-checkbox input { width:16px; min-height:16px; padding:0; accent-color:var(--indigo-ink); }
.grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:11px; margin-top:16px; } .card { border:1px solid var(--line); border-radius:15px; padding:14px; background:var(--card); min-height:82px; } .card span { display:block; color:var(--muted); font-size:12px; margin-bottom:8px; } .card strong { display:block; font-size:19px; line-height:1.18; overflow-wrap:anywhere; }
.card.status-ok { border-color:rgba(20,108,67,.28); background:linear-gradient(135deg,var(--mint-soft),var(--card) 62%); } .card.status-ok strong { color:var(--ok); } .card.status-warn strong { color:var(--warn); } .card.status-error strong { color:var(--bad); }
.error-card { grid-column:1 / -1; } .error-card ul { display:grid; gap:9px; margin:10px 0 0; padding:0; list-style:none; color:var(--text); } .error-card li { display:grid; gap:6px; padding:10px 12px; border:1px solid rgba(176,58,74,.22); border-radius:12px; background:var(--rose-soft); font-size:12px; line-height:1.35; overflow-wrap:anywhere; } .error-card li.empty-error { border-color:rgba(20,108,67,.22); background:var(--mint-soft); color:var(--muted); } .error-card li strong { font-size:13px; color:var(--text); } .error-card li span { margin:0; color:var(--muted); } .error-card li em { color:var(--rose-ink); font-style:normal; } .error-card li a { color:var(--indigo-ink); font-weight:800; text-decoration:none; width:max-content; } .error-card li a:hover { color:var(--mint-ink); text-decoration:underline; } .failed-link { display:grid; gap:3px; margin-top:4px; padding:8px 10px; border-radius:10px; background:var(--surface-2); border:1px solid var(--line); } .failed-link span { color:var(--muted); font-weight:800; font-size:11px; } .failed-link strong { color:var(--text); font-size:12px; } .failed-link em { color:var(--muted); font-size:11px; }
.public-error-card { margin-top:18px; }
.summary-panel { margin-top:18px; border:1px solid var(--line); border-radius:18px; padding:16px; background:var(--card); } .summary-head { display:flex; align-items:flex-end; justify-content:space-between; gap:12px; margin-bottom:12px; } .summary-head h2 { font-size:18px; margin:0; } .summary-head span { color:var(--muted); font-size:12px; white-space:nowrap; } .table-section + .table-section { margin-top:18px; } .table-section h3 { margin:0 0 9px; font-size:14px; color:var(--text); } .deals-section h3 { color:var(--mint-ink); }
.telegram-recent { margin-top:14px; border:1px solid var(--line); border-radius:14px; padding:13px; background:var(--surface-2); } .telegram-recent h3 { margin:0 0 10px; font-size:13px; color:var(--text); } .telegram-recent p { color:var(--muted); } .telegram-recent ul { display:grid; gap:8px; margin:0; padding:0; list-style:none; } .telegram-recent li { display:grid; gap:4px; padding:10px 11px; border:1px solid var(--line); border-radius:12px; background:var(--surface); } .telegram-recent li a,.telegram-recent li strong { color:var(--text); font-size:13px; font-weight:850; text-decoration:none; overflow-wrap:anywhere; } .telegram-recent li a:hover { color:var(--indigo-ink); text-decoration:underline; } .telegram-recent li span { color:var(--muted); font-size:11px; } .telegram-recent li em { color:var(--ink-soft); font-size:12px; font-style:normal; line-height:1.35; overflow-wrap:anywhere; }
.table-wrap { overflow-x:auto; border:1px solid var(--line); border-radius:14px; } table { width:100%; border-collapse:collapse; min-width:860px; } th,td { padding:8px 8px; border-bottom:1px solid var(--line); text-align:right; white-space:nowrap; } th { color:var(--ink-soft); background:var(--head); font-size:11px; text-transform:uppercase; letter-spacing:.035em; } td { color:var(--text); font-size:13px; font-variant-numeric:tabular-nums; } tr:last-child td { border-bottom:none; } th:nth-child(1),td:nth-child(1) { width:104px; } th:nth-child(1),td:nth-child(1),th:nth-child(2),td:nth-child(2) { text-align:left; } th:not(:nth-child(2)),td:not(:nth-child(2)) { width:100px; } th:nth-child(6),td:nth-child(6) { width:148px; } .empty-row td { color:var(--muted); text-align:left; background:rgba(43,42,61,.02); }
.search-result-group { margin:10px 0; overflow:hidden; border:1px solid var(--line); border-radius:14px; background:var(--surface-2); } .search-result-group summary { display:flex; align-items:center; justify-content:space-between; gap:10px; padding:12px 14px; color:var(--text); font-size:13px; font-weight:850; cursor:pointer; list-style:none; } .search-result-group summary::-webkit-details-marker { display:none; } .search-result-group summary::before { content:'▸'; display:inline-block; margin-right:8px; color:var(--ink-faint); font-size:16px; transition:transform .16s ease; } .search-result-group[open] summary::before { transform:rotate(90deg); } .search-result-group summary strong { margin-right:auto; } .search-result-group summary span { color:var(--muted); font-size:11px; font-weight:750; white-space:nowrap; } .search-result-group[open] summary { border-bottom:1px solid var(--line); background:var(--indigo-soft); } .search-result-group .table-wrap { border:0; border-radius:0; }
tbody tr.site-amazon { --site-bg:rgba(217,158,45,.16); --site-bg-strong:rgba(217,158,45,.28); --site-line:rgba(217,158,45,.75); --site-link:#9a6a15; }
tbody tr.site-hepsiburada { --site-bg:rgba(230,110,55,.16); --site-bg-strong:rgba(230,110,55,.28); --site-line:rgba(230,110,55,.75); --site-link:#a6491b; }
tbody tr.site-trendyol { --site-bg:rgba(211,74,140,.16); --site-bg-strong:rgba(211,74,140,.28); --site-line:rgba(211,74,140,.75); --site-link:#a1215f; }
tbody tr.site-network { --site-bg:rgba(32,158,138,.16); --site-bg-strong:rgba(32,158,138,.28); --site-line:rgba(32,158,138,.75); --site-link:#147d6f; }
tbody tr.site-beymenclub { --site-bg:rgba(180,120,60,.16); --site-bg-strong:rgba(180,120,60,.28); --site-line:rgba(180,120,60,.75); --site-link:#8a5a2e; }
tbody tr.site-nordbron { --site-bg:rgba(66,116,214,.16); --site-bg-strong:rgba(66,116,214,.28); --site-line:rgba(66,116,214,.75); --site-link:#2c5fb8; }
tbody tr.site-zara { --site-bg:rgba(101,151,55,.16); --site-bg-strong:rgba(101,151,55,.28); --site-line:rgba(101,151,55,.75); --site-link:#4c7a22; }
tbody tr.site-hm { --site-bg:rgba(140,80,196,.16); --site-bg-strong:rgba(140,80,196,.28); --site-line:rgba(140,80,196,.75); --site-link:#6b3fa0; }
tbody tr.site-other { --site-bg:rgba(75,71,214,.14); --site-bg-strong:rgba(75,71,214,.24); --site-line:rgba(75,71,214,.6); --site-link:#4b47d6; } tbody tr[class*='site-'] td { background:linear-gradient(90deg,var(--site-bg),rgba(255,255,255,.5)); } tbody tr[class*='site-'] td:first-child { border-left:4px solid var(--site-line); color:var(--site-link); font-weight:800; } tbody tr[class*='site-'] .product-cell a { color:var(--site-link); } tbody tr[class*='site-']:hover td { background:linear-gradient(90deg,rgba(43,42,61,.05),var(--site-bg)); }
.product-cell { max-width:360px; white-space:normal; line-height:1.22; } .product-cell a { color:var(--text); text-decoration:none; } .product-cell a:hover { color:var(--indigo-ink); text-decoration:underline; } .product-cell .warehouse-tag { display:inline-block; margin:0 8px 0 0; padding:0 7px; border-radius:5px; background:var(--peach-bg); color:var(--peach-ink); font-size:13px; font-weight:900; letter-spacing:.05em; line-height:1.1; vertical-align:top; } .product-cell span { display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; text-overflow:ellipsis; } .deal-row td { color:var(--mint-ink); } .deal-row td:first-child { color:var(--site-link); } .deal-row .product-cell a { color:var(--mint-ink); } .note { margin-top:18px; border-left:4px solid var(--ink-faint); padding:12px 14px; background:var(--surface-2); border-radius:10px; font-size:13px; } .footer { margin-top:18px; font-size:12px; color:var(--muted); }
.public main { max-width:1180px; } .public .hero { padding:18px; } .public .badge { font-size:clamp(22px,4vw,36px); }
.public-actions { margin:16px 0 6px; } .public-actions .button { min-width:132px; }
.public-cycle-row { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; margin:10px 0 4px; }
.public-cycle-pill { min-width:0; min-height:40px; padding:7px 10px; border:1px solid var(--line); border-radius:13px; background:var(--surface); }
.public-cycle-pill span { display:block; color:var(--muted); font-size:10px; font-weight:800; letter-spacing:.035em; text-transform:uppercase; }
.public-cycle-pill strong { display:block; margin-top:2px; font-size:14px; line-height:1.1; color:var(--text); }
@media (max-width:720px) {
  body { font-size:13px; background:var(--bg); }
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
  .public .summary-head span { font-size:16px; line-height:1.3; color:var(--indigo-ink); font-weight:800; }
  .table-section h3 { font-size:13px; }
  .table-wrap { overflow:visible; border:0; border-radius:0; }
  .search-result-group { margin:8px 0; border-radius:13px; }
  .search-result-group summary { min-height:48px; padding:12px; font-size:13px; }
  .search-result-group summary span { font-size:11px; }
  table { min-width:0; }
  thead { display:none; }
  table, tbody, td { display:block; width:100%; }
  tbody tr[class*='site-'] { position:relative; display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:6px 8px; margin:0 0 8px; border:1px solid var(--site-line); border-left:7px solid var(--site-line); border-radius:15px; padding:9px 10px 9px 12px; background:linear-gradient(135deg,var(--site-bg-strong),rgba(255,255,255,.94) 58%),rgba(255,255,255,.92); box-shadow:0 8px 18px rgba(43,42,61,.08); overflow:hidden; }
  tbody tr[class*='site-'] td { display:flex; justify-content:flex-start; gap:4px; padding:0; border-bottom:0; background:transparent; text-align:left; white-space:normal; font-size:13.5px; line-height:1.18; }
  tbody tr[class*='site-'] td:first-child { border-left:0; color:var(--site-link); }
  tbody tr[class*='site-'] td::before { content:attr(data-label); flex:0 0 auto; color:var(--muted); text-align:left; font-size:10px; font-weight:850; letter-spacing:.045em; text-transform:uppercase; }
  tbody tr[class*='site-'] .seller-cell { grid-column:1 / -1; align-items:center; gap:0; padding-bottom:0; color:var(--site-link); font-size:15px; font-weight:900; }
  tbody tr[class*='site-'] .seller-cell::before, tbody tr[class*='site-'] .product-cell::before { display:none; }
  tbody tr[class*='site-'] .product-cell { grid-column:1 / -1; max-width:none; display:block; padding-bottom:0; text-align:left; line-height:1.22; font-size:14px; }
  tbody tr[class*='site-'] .price-cell, tbody tr[class*='site-'] .target-cell, tbody tr[class*='site-'] .diff-cell, tbody tr[class*='site-'] .range-cell { min-height:33px; border:1px solid var(--line); border-radius:10px; padding:5px 7px; background:rgba(255,255,255,.65); flex-direction:column; justify-content:center; font-size:14.5px; }
  tbody tr[class*='site-'] .price-cell, tbody tr[class*='site-'] .target-cell, tbody tr[class*='site-'] .diff-cell { min-width:0; }
  tbody tr[class*='site-'] .range-cell { grid-column:1 / -1; min-height:31px; flex-direction:row; align-items:center; justify-content:flex-start; gap:8px; white-space:nowrap; }
  tbody tr[class*='site-'] .range-cell::before { margin-right:3px; }
  .product-cell span { -webkit-line-clamp:2; }
  .empty-row td { padding:10px; border:1px solid var(--line); border-radius:12px; }
  .note, .footer { font-size:11px; }
}

/* --- Hermes mobile panel (hm-*): pastel card dashboard, additive & separate from the table/link-test styles above --- */
.hm-app { max-width:460px; margin:0 auto; min-height:100vh; background:var(--bg); position:relative; padding-bottom:96px; }
.hm-header { position:sticky; top:0; z-index:10; background:rgba(245,244,251,.9); backdrop-filter:blur(10px); padding:16px 16px 10px; display:flex; align-items:center; justify-content:space-between; border-bottom:1px solid var(--line); }
.hm-brand { display:flex; align-items:center; gap:10px; min-width:0; }
.hm-brand-mark { flex-shrink:0; width:38px; height:38px; border-radius:12px; background:linear-gradient(155deg,var(--indigo-bg),var(--mint-bg)); display:flex; align-items:center; justify-content:center; }
.hm-brand-mark svg { width:19px; height:19px; }
.hm-brand-text h1 { font-size:18px; font-weight:700; letter-spacing:-.01em; margin:0; }
.hm-brand-text p { font-size:12px; color:var(--ink-faint); margin-top:1px; }
.hm-header-actions { display:flex; gap:8px; flex-shrink:0; }
.hm-icon-btn { width:36px; height:36px; border-radius:11px; background:var(--surface); border:1px solid var(--line); display:flex; align-items:center; justify-content:center; color:var(--ink-soft); text-decoration:none; position:relative; }
.hm-icon-btn svg { width:17px; height:17px; }
.hm-icon-btn.hm-has-dot::after { content:''; position:absolute; top:7px; right:7px; width:6px; height:6px; border-radius:50%; background:var(--rose-ink); }
.hm-chips { display:flex; gap:8px; padding:14px 16px 4px; overflow-x:auto; scrollbar-width:none; }
.hm-chips::-webkit-scrollbar { display:none; }
.hm-chip { flex-shrink:0; font-size:13px; font-weight:600; padding:7px 14px; border-radius:999px; background:var(--surface); border:1px solid var(--line); color:var(--ink-soft); cursor:pointer; white-space:nowrap; }
.hm-chip.hm-active { background:var(--indigo-bg); color:var(--indigo-ink); border-color:var(--indigo-bg); }
.hm-section-title { font-size:13px; font-weight:700; color:var(--ink-soft); padding:20px 16px 10px; letter-spacing:.01em; }
.hm-deals { display:flex; gap:12px; padding:0 16px 4px; overflow-x:auto; scroll-snap-type:x proximity; scrollbar-width:none; }
.hm-deals::-webkit-scrollbar { display:none; }
.hm-deal-card { scroll-snap-align:start; flex-shrink:0; width:168px; background:var(--mint-soft); border-radius:var(--radius-md); padding:14px; text-decoration:none; color:inherit; display:block; }
.hm-deal-drop { display:inline-flex; align-items:center; gap:3px; background:var(--mint-bg); color:var(--mint-ink); font-size:11px; font-weight:700; padding:3px 8px; border-radius:999px; margin-bottom:10px; }
.hm-deal-name { font-size:13px; font-weight:600; line-height:1.3; margin-bottom:8px; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }
.hm-deal-price { font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; font-size:17px; font-weight:700; color:var(--mint-ink); }
.hm-deal-site { font-size:11px; color:var(--ink-faint); margin-top:2px; }
.hm-empty { margin:0 16px; padding:16px; border:1px dashed var(--line); border-radius:var(--radius-md); color:var(--muted); font-size:13px; background:var(--surface-2); }
.hm-list { padding:0 16px; display:flex; flex-direction:column; gap:12px; }
.hm-card { background:var(--surface); border:1px solid var(--line); border-radius:var(--radius-lg); padding:16px; }
.hm-card.hm-paused { opacity:.6; }
.hm-card-top { display:flex; justify-content:space-between; align-items:flex-start; gap:10px; margin-bottom:12px; }
.hm-card-name { font-size:14.5px; font-weight:700; line-height:1.35; }
.hm-status { flex-shrink:0; font-size:11px; font-weight:700; padding:4px 9px; border-radius:999px; white-space:nowrap; }
.hm-status-below { background:var(--mint-bg); color:var(--mint-ink); }
.hm-status-watch { background:var(--indigo-soft); color:var(--indigo-ink); }
.hm-status-stock { background:var(--peach-bg); color:var(--peach-ink); }
.hm-status-error { background:var(--rose-bg); color:var(--rose-ink); }
.hm-price-row { display:flex; align-items:baseline; gap:10px; margin-bottom:10px; flex-wrap:wrap; }
.hm-price-best { font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; font-size:20px; font-weight:700; }
.hm-price-best.hm-below { color:var(--mint-ink); }
.hm-price-best.hm-error { color:var(--rose-ink); font-family:inherit; font-size:13px; font-weight:600; }
.hm-price-target { font-size:12px; color:var(--ink-faint); font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }
.hm-gauge { height:6px; border-radius:999px; background:var(--surface-2); border:1px solid var(--line); position:relative; margin-bottom:12px; overflow:visible; }
.hm-gauge-fill { position:absolute; top:0; left:0; height:100%; border-radius:999px; }
.hm-gauge-fill.hm-below { background:var(--mint-bg); }
.hm-gauge-fill.hm-watch { background:var(--indigo-bg); }
.hm-gauge-marker { position:absolute; top:50%; width:2px; height:12px; background:var(--ink-faint); transform:translate(-50%,-50%); }
.hm-card-bottom { display:flex; align-items:center; justify-content:space-between; gap:10px; }
.hm-sites { display:flex; gap:5px; flex-wrap:wrap; }
.hm-site-dot { width:22px; height:22px; border-radius:7px; background:var(--surface-2); border:1px solid var(--line); display:flex; align-items:center; justify-content:center; font-size:9px; font-weight:700; color:var(--ink-soft); }
.hm-meta { font-size:11px; color:var(--ink-faint); }
.hm-toggle-form { margin:0; flex-shrink:0; }
.hm-toggle { width:34px; height:20px; border-radius:999px; background:var(--line); position:relative; border:0; padding:0; cursor:pointer; flex-shrink:0; }
.hm-toggle.hm-on { background:var(--indigo-bg); }
.hm-toggle .hm-knob { position:absolute; top:2px; left:2px; width:16px; height:16px; border-radius:50%; background:var(--surface); box-shadow:0 1px 2px rgba(43,42,61,.2); }
.hm-toggle.hm-on .hm-knob { transform:translateX(14px); }
.hm-more-results { margin-top:10px; border-top:1px solid var(--line); padding-top:10px; }
.hm-more-results summary { cursor:pointer; font-size:12px; font-weight:700; color:var(--indigo-ink); list-style:none; }
.hm-more-results summary::-webkit-details-marker { display:none; }
.hm-more-row { display:flex; justify-content:space-between; gap:8px; padding:8px 0; border-bottom:1px solid var(--line); font-size:12.5px; }
.hm-more-row:last-child { border-bottom:0; }
.hm-more-row a { color:var(--text); text-decoration:none; }
.hm-more-row span { font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; color:var(--ink-soft); flex-shrink:0; }
.hm-panel { margin:20px 16px 0; background:var(--surface); border:1px solid var(--line); border-radius:var(--radius-lg); padding:16px; }
.hm-panel h2 { font-size:15px; font-weight:700; margin:0 0 10px; }
.hm-panel-actions { display:flex; flex-wrap:wrap; gap:8px; margin-top:10px; }
.hm-panel-actions form { margin:0; }
.hm-btn { display:inline-flex; align-items:center; justify-content:center; min-height:38px; padding:0 14px; border-radius:12px; border:1px solid var(--line); background:var(--indigo-soft); color:var(--text); font-weight:700; font-size:12.5px; cursor:pointer; text-decoration:none; }
.hm-fab { position:fixed; bottom:82px; left:50%; transform:translateX(calc(-50% + 165px)); width:52px; height:52px; border-radius:16px; background:var(--indigo-ink); display:flex; align-items:center; justify-content:center; color:#fff; text-decoration:none; box-shadow:0 8px 20px rgba(75,71,214,.28); }
.hm-fab svg { width:22px; height:22px; }
.hm-nav { position:fixed; bottom:0; left:50%; transform:translateX(-50%); width:100%; max-width:460px; background:rgba(255,255,255,.92); backdrop-filter:blur(10px); border-top:1px solid var(--line); display:flex; justify-content:space-around; padding:10px 12px 14px; }
.hm-nav-item { display:flex; flex-direction:column; align-items:center; gap:3px; color:var(--ink-faint); font-size:10.5px; font-weight:600; text-decoration:none; }
.hm-nav-item svg { width:20px; height:20px; }
.hm-nav-item.hm-active { color:var(--indigo-ink); }
@media (max-width:380px) { .hm-deal-card { width:150px; } }
"""


def _parse_turkish_money(value):
    text = str(value or "").strip().replace("TL", "").replace(" ", "")
    text = text.replace("+", "").replace(".", "").replace(",", ".")
    if not text or text == "-":
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _display_tl(value, signed=False):
    text = str(value or "").strip()
    amount = _parse_turkish_money(text)
    if amount is None:
        return text or "-"
    sign = ""
    if signed:
        sign = "-" if amount < 0 or text.startswith("-") else "+"
    whole_lira = abs(amount).quantize(Decimal("1"), rounding=ROUND_DOWN)
    formatted = f"{whole_lira:,}".replace(",", ".")
    return f"{sign}{formatted} TL"


def _display_tl_range(value, fallback_min="-", fallback_max="-"):
    raw_range = str(value or f"{fallback_min} / {fallback_max}").strip()
    parts = [part.strip() for part in raw_range.split("/") if part.strip()]
    if not parts:
        return "-"
    return " / ".join(_display_tl(part) for part in parts)


def _relative_time_text(value) -> str:
    raw_value = str(value or "").strip()
    parsed = None
    for fmt in ("%Y-%m-%d %H:%M:%S",):
        try:
            parsed = datetime.strptime(raw_value, fmt).astimezone()
            break
        except ValueError:
            pass
    if parsed is None:
        parsed = parse_iso_datetime(raw_value)
    if not parsed:
        return "-"
    elapsed_seconds = max(0, int((datetime.now().astimezone() - parsed.astimezone()).total_seconds()))
    if elapsed_seconds < 60:
        return "az önce" if elapsed_seconds < 10 else f"{elapsed_seconds} sn önce"
    minutes = elapsed_seconds // 60
    if minutes < 60:
        return f"{minutes} dk önce"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} sa önce"
    days = hours // 24
    return f"{days} gün önce"


def _duration_text(seconds_value, fallback="-") -> str:
    if seconds_value in (None, ""):
        return str(fallback or "-")
    try:
        total_seconds = max(0, int(round(float(seconds_value))))
    except (TypeError, ValueError):
        return str(fallback or "-")
    minutes, seconds = divmod(total_seconds, 60)
    if minutes:
        return f"{minutes} dk {seconds} sn"
    return f"{seconds} sn"


def _is_target_hit(row):
    explicit = row.get("is_target_hit")
    if isinstance(explicit, bool):
        return explicit
    price = _parse_turkish_money(row.get("price"))
    target = _parse_turkish_money(row.get("target"))
    if price is not None and target is not None:
        return price <= target
    diff = _parse_turkish_money(row.get("difference"))
    return diff is not None and diff <= 0


def _extract_first_url(text):
    if not text:
        return ""
    match = re.search(r"https?://\S+", str(text))
    if not match:
        return ""
    return match.group(0).rstrip(".,;)]}")


def _short_link(value, max_length=74):
    text = str(value or "").strip()
    if not text:
        return "-"
    if len(text) <= max_length:
        return text
    return text[: max_length - 3].rstrip() + "..."


def _site_name(raw_site, is_search):
    if is_search:
        return "Amazon arama"
    labels = {
        "amazon": "Amazon",
        "hepsiburada": "Hepsiburada",
        "trendyol": "Trendyol",
        "network": "Network",
        "beymenclub": "Beymen Club",
        "nordbron": "Nordbron",
        "zara": "Zara",
        "hm": "H&M",
    }
    return labels.get(str(raw_site or "").strip().lower(), "Ürün kontrolü")


def _site_theme_class(seller):
    normalized = repair_mojibake(seller).casefold()
    if "amazon" in normalized:
        return "site-amazon"
    if "hepsiburada" in normalized:
        return "site-hepsiburada"
    if "network" in normalized:
        return "site-network"
    if "beymen club" in normalized or "beymenclub" in normalized:
        return "site-beymenclub"
    if "trendyol" in normalized:
        return "site-trendyol"
    if "nordbron" in normalized:
        return "site-nordbron"
    if "zara" in normalized:
        return "site-zara"
    if "h&m" in normalized or "hm" in normalized:
        return "site-hm"
    return "site-other"


def _clean_error_message(error_text):
    text = repair_mojibake(error_text or "").strip()
    if not text:
        return "Hata ayrıntısı kaydedilmemiş."
    parts = [part.strip() for part in text.split("|") if part.strip()]
    non_url_parts = [part for part in parts if not part.startswith(("http://", "https://"))]
    if non_url_parts:
        text = " | ".join(non_url_parts)
    text = re.sub(r"https?://\S+", "[link]", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or "Hata ayrıntısı kaydedilmemiş."


def _error_link_details(error_text):
    text = repair_mojibake(error_text or "").strip()
    if not text:
        return []
    details = []
    seen = set()
    for segment in [item.strip() for item in text.split(";") if item.strip()]:
        url = _extract_first_url(segment)
        if not url:
            continue
        message = _clean_error_message(segment)
        key = (url, message)
        if key in seen:
            continue
        seen.add(key)
        details.append({"url": url, "message": message})
    return details


def _unique_text(values):
    unique = []
    seen = set()
    for value in values:
        text = repair_mojibake(value).strip()
        if not text or text.casefold() in seen:
            continue
        seen.add(text.casefold())
        unique.append(text)
    return unique


def _target_text(labels):
    if not labels:
        return "Aranan keyword belirtilmemiş"
    prefix = "Aranan keyword" if len(labels) == 1 else "Aranan keywordler"
    return f"{prefix}: {', '.join(labels)}"


WATCH_URL_FIELDS = ("url_1", "url_2", "url_3", "url_4", "url_5")


def _watch_urls_from_options(item):
    urls = []
    if not isinstance(item, dict):
        return urls
    for field_name in WATCH_URL_FIELDS:
        url = str(item.get(field_name) or "").strip()
        if url and url not in urls:
            urls.append(url)
    return urls


def _context_for_watch_url(item, url):
    try:
        site = detect_site_from_url(url)
        seller = site_label(site)
    except Exception:  # noqa: BLE001
        site = str(item.get("site") or "urun").strip().lower()
        seller = _site_name(site, False)
    name = str(item.get("name") or "").strip()
    display_name = name or url
    key = normalize_item_key("watch", site, name, url)
    return key, {
        "title": f"{seller}: {display_name}",
        "meta": f"Takip edilen: {display_name}",
        "url": url,
        "urls": [url],
        "keywords": [name] if name else [],
    }


def _contexts_for_watch(item):
    return [
        context
        for url in _watch_urls_from_options(item)
        for context in [_context_for_watch_url(item, url)]
        if context
    ]


def _error_contexts(options):
    watches = options.get("takip_edilenler") if isinstance(options.get("takip_edilenler"), list) else []
    contexts = {}
    for item in watches:
        if isinstance(item, dict):
            for context in _contexts_for_watch(item):
                key, value = context
                contexts[key] = value
    return contexts


def _urls_from_error_and_state(raw_error, state_entry):
    urls = [item["url"] for item in _error_link_details(raw_error)]
    for field in ("url", "configured_url"):
        url = str(state_entry.get(field) or "").strip()
        if url:
            urls.append(url)
    return _unique_text(urls)


def _find_error_context(state_key, state_entry, raw_error, contexts):
    if state_key in contexts:
        return contexts[state_key]
    failed_urls = _urls_from_error_and_state(raw_error, state_entry)
    for context in contexts.values():
        context_urls = context.get("urls") or [context.get("url")]
        context_urls = [str(url or "").strip() for url in context_urls]
        if any(url and url in context_urls for url in failed_urls):
            return context
    return {}


def _error_detail(state_key, state_entry, contexts):
    raw_error = state_entry.get("last_error")
    context = _find_error_context(state_key, state_entry, raw_error, contexts)
    failed_links = _error_link_details(raw_error)
    keywords = context.get("keywords") or []
    keyword_text = _target_text(keywords) if keywords else ""
    for failed_link in failed_links:
        if keyword_text:
            failed_link["keywords"] = keyword_text
    url_text = (
        (failed_links[0]["url"] if failed_links else "")
        or str(context.get("url") or state_entry.get("url") or "").strip()
        or _extract_first_url(raw_error)
    )
    title = context.get("title") or _site_name(state_entry.get("site"), False)
    meta = context.get("meta") or keyword_text
    if not meta:
        meta = "Takip edilen link kontrol edilirken hata oluştu."
    elif keyword_text and "keyword" not in meta.casefold():
        meta = f"{meta} · {keyword_text}" if keyword_text else meta
    return {
        "title": repair_mojibake(title),
        "meta": repair_mojibake(meta),
        "message": _clean_error_message(raw_error),
        "url": url_text,
        "failed_links": failed_links[:4],
    }


def _error_detail_key(detail):
    fields = ("title", "meta", "message", "url")
    link_key = ";".join(item.get("url", "") for item in detail.get("failed_links", []))
    return "|".join(str(detail.get(field) or "") for field in fields) + "|" + link_key


def _collect_summary(error_detail_limit: int | None = 4):
    options = load_json(OPTIONS_PATH, {})
    state = load_json(STATE_PATH, {})
    latest_summary = load_json(SUMMARY_PATH, {})
    if not isinstance(latest_summary, dict):
        latest_summary = {}
    watches = options.get("takip_edilenler") if isinstance(options.get("takip_edilenler"), list) else []
    contexts = _error_contexts(options if isinstance(options, dict) else {})

    error_cutoff = timedelta(hours=24)
    now = datetime.now().astimezone()
    last_checks = []
    error_count = 0
    error_details = []
    seen_details = set()

    if isinstance(state, dict):
        for key, value in state.items():
            if key == "_meta" or not isinstance(value, dict):
                continue
            checked_at = parse_iso_datetime(value.get("last_checked_at"))
            if checked_at:
                checked_local = checked_at.astimezone()
                last_checks.append(checked_local)
                if value.get("last_error") and now - checked_local <= error_cutoff:
                    error_count += 1
                    detail = _error_detail(key, value, contexts)
                    detail_key = _error_detail_key(detail)
                    if detail_key not in seen_details:
                        seen_details.add(detail_key)
                        error_details.append(detail)
    interval_seconds = int(options.get("interval_seconds") or 60)
    last_check = max(last_checks) if last_checks else None
    return {
        "interval": interval_seconds,
        "watches": len(watches),
        "last_check": last_check.strftime("%Y-%m-%d %H:%M:%S") if last_check else "-",
        "next_check": (last_check + timedelta(seconds=interval_seconds)).strftime("%Y-%m-%d %H:%M:%S") if last_check else "-",
        "cycle_duration": _duration_text(
            latest_summary.get("cycle_duration_seconds"),
            latest_summary.get("cycle_duration_minutes") or "-",
        ),
        "last_update": _relative_time_text(latest_summary.get("checked_at")),
        "errors": error_count,
        "error_details": error_details if error_detail_limit is None else error_details[:error_detail_limit],
        "configured": bool(options),
        "telegram": _collect_telegram_summary(options if isinstance(options, dict) else {}),
    }


def _telegram_error_count_24h():
    payload = load_json(TELEGRAM_ERROR_EVENTS_PATH, [])
    if not isinstance(payload, list):
        return 0
    cutoff = datetime.now().astimezone() - timedelta(hours=24)
    count = 0
    for item in payload:
        if not isinstance(item, dict):
            continue
        try:
            created_at = datetime.fromisoformat(str(item.get("created_at")))
            if created_at.tzinfo is None:
                created_at = created_at.astimezone()
        except ValueError:
            continue
        if created_at.astimezone() >= cutoff:
            count += 1
    return count


def _collect_telegram_summary(options):
    status = load_json(TELEGRAM_STATUS_PATH, {})
    if not isinstance(status, dict):
        status = {}
    channels = options.get("channels") if isinstance(options.get("channels"), list) else []
    keywords = options.get("keywords") if isinstance(options.get("keywords"), list) else []
    enabled = parse_bool(options.get("telegram_enabled"), default=False)
    return {
        "enabled": enabled,
        "state": status.get("telegram_state") or ("Pasif" if not enabled else "Bekleniyor"),
        "channels": status.get("telegram_channels") or len(channels),
        "keywords": status.get("telegram_keywords") or len(keywords),
        "notifications": status.get("notifications_sent", 0),
        "last_check": status.get("last_check") or "-",
        "last_notification": status.get("last_notification") or "-",
        "errors": _telegram_error_count_24h(),
        "recent_notifications": status.get("recent_notifications") if isinstance(status.get("recent_notifications"), list) else [],
    }


def _render_table_row(row):
    seller_text = repair_mojibake(row.get("seller") or "-")
    seller = escape(seller_text)
    raw_title = repair_mojibake(row.get("product_title") or "-")
    if seller_text == "Hepsiburada":
        raw_title = hepsiburada_provider.clean_display_title(raw_title)
    product_title = escape(raw_title)
    warehouse_tag = '<strong class="warehouse-tag">DEPO</strong>' if row.get("is_warehouse") else ""
    product_url = str(row.get("product_url") or "").strip()
    if product_url:
        label = (
            f'<a href="{escape(product_url, quote=True)}" target="_blank" rel="noopener noreferrer">'
            f"<span>{warehouse_tag}{product_title}</span></a>"
        )
    else:
        label = f"<span>{warehouse_tag}{product_title}</span>"
    price = escape(_display_tl(row.get("price", "-")))
    target = escape(_display_tl(row.get("target", "-")))
    difference = escape(_display_tl(row.get("difference", "-"), signed=True))
    price_range = escape(
        _display_tl_range(
            row.get("price_range"),
            row.get("min_price", "-"),
            row.get("max_price", "-"),
        )
    )
    row_classes = [_site_theme_class(seller_text)]
    if _is_target_hit(row):
        row_classes.append("deal-row")
    row_class = f' class="{" ".join(row_classes)}"'
    return (
        f'<tr{row_class}><td data-label="Satıcı" class="seller-cell">{seller}</td>'
        f'<td data-label="Ürün" class="product-cell" title="{product_title}">{label}</td>'
        f'<td data-label="Güncel" class="price-cell">{price}</td>'
        f'<td data-label="Hedef" class="target-cell">{target}</td>'
        f'<td data-label="Fark" class="diff-cell">{difference}</td>'
        f'<td data-label="Min / Maks" class="range-cell">{price_range}</td></tr>'
    )


def _render_rows_table(rows, empty_text):
    if rows:
        body = "".join(_render_table_row(row) for row in rows)
    else:
        body = f"<tr class='empty-row'><td colspan='6'>{escape(empty_text)}</td></tr>"
    return f"""
        <div class="table-wrap">
          <table>
            <thead><tr><th>Satıcı</th><th>Ürün Adı</th><th>Güncel</th><th>Hedef</th><th>Fark</th><th>Min / Maks</th></tr></thead>
            <tbody>{body}</tbody>
          </table>
        </div>
    """


def _summary_difference_sort_value(row) -> Decimal:
    """Parse persisted Turkish price text for stable dashboard ordering."""
    raw_value = repair_mojibake(str(row.get("difference") or "0"))
    cleaned = re.sub(r"[^0-9,.-]", "", raw_value).strip()
    if not cleaned:
        return Decimal("0")
    if "," in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    else:
        cleaned = cleaned.replace(".", "")
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return Decimal("0")


def _summary_row_sort_key(row):
    seller = repair_mojibake(str(row.get("seller") or ""))
    title = repair_mojibake(str(row.get("product_title") or ""))
    return seller.casefold(), _summary_difference_sort_value(row), title.casefold()


def _deduplicate_dashboard_rows(rows):
    """Hide duplicate offers while keeping new and Amazon Depo rows separate."""
    unique_rows = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = tracking_offer_identity(row.get("product_url"), parse_bool(row.get("is_warehouse"), default=False))
        tracking_id = str(row.get("tracking_id") or "").strip()
        if key and tracking_id:
            key = f"{tracking_id}|{key}"
        key = key or f"__missing_url__:{len(unique_rows)}"
        current = unique_rows.get(key)
        if current is None or _summary_difference_sort_value(row) < _summary_difference_sort_value(current):
            unique_rows[key] = row
    return list(unique_rows.values())


def _inferred_variant_group_label(row):
    title = repair_mojibake(str(row.get("product_title") or "")).strip()
    if " / " in title:
        return title.split(" / ", 1)[0].strip()
    if " — " in title:
        return title.split(" — ", 1)[0].strip()
    return title


def _split_search_result_groups(rows):
    rows = _deduplicate_dashboard_rows(rows)
    inferred_counts = {}
    for row in rows:
        if row.get("search_group"):
            continue
        label = _inferred_variant_group_label(row)
        if not label:
            continue
        key = (
            repair_mojibake(str(row.get("seller") or "")).casefold(),
            str(row.get("target") or "").strip(),
            label.casefold(),
        )
        inferred_counts[key] = inferred_counts.get(key, 0) + 1

    grouped = {}
    ungrouped = []
    for row in rows:
        seller = repair_mojibake(str(row.get("seller") or "")).casefold()
        label = str(row.get("search_group_label") or "").strip()
        group_key = str(row.get("search_group") or "").strip()
        if group_key:
            label = label or _inferred_variant_group_label(row)
            # New watch/state groups deliberately identify a configured watch. Keep
            # that identity for blank-name variation cards; legacy URL groups are
            # still merged by their visible tracked-item label.
            if not group_key.startswith(("watch_result_group_", "state_result_group_")):
                group_key = normalize_item_key("dashboard_result_group", seller, label)
        else:
            inferred_label = _inferred_variant_group_label(row)
            inferred_key = (seller, str(row.get("target") or "").strip(), inferred_label.casefold())
            if inferred_label and inferred_counts.get(inferred_key, 0) > 1:
                label = inferred_label
                group_key = normalize_item_key("inferred_variant_group", seller, inferred_label, inferred_key[1])
        if not group_key:
            ungrouped.append(row)
            continue
        grouped.setdefault(group_key, {"label": label, "rows": []})["rows"].append(row)

    collapsible_groups = []
    for group in grouped.values():
        group_rows = group["rows"]
        if len(group_rows) < 2:
            ungrouped.extend(group_rows)
            continue
        group_rows.sort(key=_summary_row_sort_key)
        label = str(group["label"] or "Arama sonuçları").strip()
        collapsible_groups.append((label, group_rows))
    ungrouped.sort(key=_summary_row_sort_key)
    collapsible_groups.sort(
        key=lambda item: (
            _summary_row_sort_key(item[1][0]) if item[1] else ("", Decimal("0"), ""),
            item[0].casefold(),
        )
    )
    return ungrouped, collapsible_groups


def _render_collapsible_search_group(label, rows):
    count = len(rows)
    return f"""
      <details class="search-result-group">
        <summary><strong>{escape(label)}</strong><span>{count} sonuç</span></summary>
        {_render_rows_table(rows, "")}
      </details>
    """


def _render_table_section(title, rows, empty_text, extra_class="", collapse_search_results=False):
    if not rows:
        body = _render_rows_table([], empty_text)
    elif not collapse_search_results:
        body = _render_rows_table(rows, empty_text)
    else:
        ungrouped, collapsible_groups = _split_search_result_groups(rows)
        pieces = []
        if ungrouped:
            pieces.append(_render_rows_table(ungrouped, empty_text))
        pieces.extend(_render_collapsible_search_group(label, group_rows) for label, group_rows in collapsible_groups)
        body = "".join(pieces)
    return f"""
      <div class="table-section {extra_class}">
        <h3>{escape(title)}</h3>
        {body}
      </div>
    """


def _render_stock_row(row):
    seller_text = repair_mojibake(row.get("seller") or "-")
    seller = escape(seller_text)
    product_title = escape(repair_mojibake(row.get("product_title") or "-"))
    product_url = str(row.get("product_url") or "").strip()
    if product_url:
        label = (
            f'<a href="{escape(product_url, quote=True)}" target="_blank" rel="noopener noreferrer">'
            f"<span>{product_title}</span></a>"
        )
    else:
        label = f"<span>{product_title}</span>"
    target = escape(_display_tl(row.get("target", "-")))
    reason = escape(repair_mojibake(row.get("reason") or "Stokta yok"))
    row_class = f' class="{_site_theme_class(seller_text)} stock-missing-row"'
    return (
        f'<tr{row_class}><td data-label="Satıcı" class="seller-cell">{seller}</td>'
        f'<td data-label="Ürün" class="product-cell" title="{product_title}">{label}</td>'
        f'<td data-label="Hedef" class="target-cell">{target}</td>'
        f'<td data-label="Durum" class="diff-cell">{reason}</td></tr>'
    )


def _render_stock_rows_table(rows, empty_text=""):
    body = "".join(_render_stock_row(row) for row in rows)
    if not body and empty_text:
        body = f"<tr class='empty-row'><td colspan='4'>{escape(empty_text)}</td></tr>"
    return f"""
        <div class="table-wrap">
          <table>
            <thead><tr><th>Satıcı</th><th>Ürün Adı</th><th>Hedef</th><th>Durum</th></tr></thead>
            <tbody>{body}</tbody>
          </table>
        </div>
    """


def _split_stock_rows_by_site(rows):
    grouped = {}
    for row in rows:
        seller = repair_mojibake(row.get("seller") or "Diğer")
        grouped.setdefault(seller, []).append(row)
    return sorted(grouped.items(), key=lambda item: item[0].casefold())


def _render_stock_site_group(seller, rows):
    return f"""
      <details class="search-result-group stock-site-group">
        <summary><strong>{escape(seller)}</strong><span>{len(rows)} ürün</span></summary>
        {_render_stock_rows_table(rows)}
      </details>
    """


def _render_stock_section(rows):
    if rows:
        body = "".join(_render_stock_site_group(seller, site_rows) for seller, site_rows in _split_stock_rows_by_site(rows))
    else:
        body = _render_stock_rows_table([], "Stok dışında izlenen ürün yok.")
    return f"""
      <div class="table-section stock-section">
        <h3>Stokta Olmayanlar</h3>
        {body}
      </div>
    """


def _is_search_result_source(site: str, configured_url: str) -> bool:
    return (
        (site == "amazon" and is_amazon_search_url(configured_url))
        or (site == "hepsiburada" and is_hepsiburada_search_url(configured_url))
    )


def _variation_watch_sources(options):
    """Return configured sources whose output rows must stay collapsed together."""
    sources = set()
    if not isinstance(options, dict):
        return sources
    watches = options.get("takip_edilenler")
    if not isinstance(watches, list):
        return sources
    for watch in watches:
        if not isinstance(watch, dict) or not parse_bool(watch.get("include_variations"), default=False):
            continue
        for configured_url in _watch_urls_from_options(watch):
            try:
                site = detect_site_from_url(configured_url)
            except Exception:  # noqa: BLE001
                continue
            sources.add((site, configured_url))
            sources.add((site, canonical_tracking_url(configured_url)))
    return sources


def _search_result_groups_from_state(state, options=None):
    groups = {}
    configured_groups = {}
    variation_sources = _variation_watch_sources(options)
    if not isinstance(state, dict):
        return groups
    for entry in state.values():
        if not isinstance(entry, dict):
            continue
        configured_url = str(entry.get("configured_url") or "").strip()
        result_url = str(entry.get("url") or "").strip()
        watch_name = str(entry.get("watch_name") or "").strip()
        site = str(entry.get("site") or "").strip()
        if not configured_url or not result_url:
            continue
        if not site:
            try:
                site = detect_site_from_url(configured_url)
            except Exception:  # noqa: BLE001
                continue
        is_variation_watch = (
            bool(entry.get("include_variations"))
            or (site, configured_url) in variation_sources
            or (site, canonical_tracking_url(configured_url)) in variation_sources
        )
        # Search pages have always been shown as collapsed result groups. A
        # product page joins them only when its variation option is enabled.
        if not (
            is_variation_watch
            or _is_search_result_source(site, configured_url)
            or str(entry.get("search_group") or "").strip()
        ):
            continue
        source_key = (site, configured_url)
        if source_key not in configured_groups:
            fallback_label = _inferred_variant_group_label({"product_title": entry.get("title") or ""})
            label = watch_name or fallback_label
            if label:
                identity = watch_name or configured_url
                configured_groups[source_key] = {
                    "search_group": str(entry.get("search_group") or "")
                    or normalize_item_key("state_result_group", site, identity),
                    "search_group_label": str(entry.get("search_group_label") or "") or label,
                }
        metadata = configured_groups.get(source_key)
        if metadata:
            groups[canonical_tracking_url(result_url)] = metadata
    return groups


def _attach_state_search_groups(rows, state, options=None):
    """Restore result-group metadata that belongs to persisted watch state."""
    groups = _search_result_groups_from_state(state, options)
    if not groups:
        return rows
    enriched = []
    for row in rows:
        if not isinstance(row, dict):
            enriched.append(row)
            continue
        # Fresh summaries already carry their configured-card identity. Do not
        # overwrite it with URL-only legacy state from another tracking card.
        if str(row.get("search_group") or "").strip():
            enriched.append(row)
            continue
        metadata = groups.get(canonical_tracking_url(row.get("product_url")))
        if not metadata:
            enriched.append(row)
            continue
        enriched_row = dict(row)
        enriched_row.update(metadata)
        enriched.append(enriched_row)
    return enriched


def _render_table():
    payload = load_json(SUMMARY_PATH, {})
    rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
    stock_rows = payload.get("stock_rows") if isinstance(payload.get("stock_rows"), list) else []
    rows = _attach_state_search_groups(
        rows,
        load_json(STATE_PATH, {}),
        load_json(OPTIONS_PATH, {}),
    )
    if not rows and not stock_rows:
        return """
        <section class="summary-panel">
          <div class="summary-head"><h2>Özet Tablo</h2><span>Henüz tablo yok</span></div>
          <p class="empty-table">İlk kontrol döngüsü tamamlandığında son fiyat tablosu burada görünecek.</p>
        </section>
        """

    deal_rows = [row for row in rows if _is_target_hit(row)]
    watch_rows = [row for row in rows if not _is_target_hit(row)]
    row_count = escape(str(payload.get("row_count") or len(rows)))
    deal_count = escape(str(len(deal_rows)))
    stock_count = escape(str(payload.get("stock_row_count") or len(stock_rows)))
    sections = _render_table_section(
        "Hedef Fiyat Altındaki Fırsatlar",
        deal_rows,
        "Şu anda hedef fiyatın altına düşen ürün yok.",
        "deals-section",
    )
    sections += _render_table_section(
        "Hedefin Üstünde Kalan Ürünler",
        watch_rows,
        "Hedef üstünde bekleyen ürün yok.",
        collapse_search_results=True,
    )
    sections += _render_stock_section(stock_rows)
    return f"""
    <section class="summary-panel">
      <div class="summary-head"><h2>Özet Tablo</h2><span>{row_count} ürün · {deal_count} fırsat · {stock_count} stokta yok</span></div>
      {sections}
    </section>
    """


def _render_telegram_recent_notifications(items):
    if not items:
        return "<div class='telegram-recent'><h3>Son Telegram Bildirimleri</h3><p>Henüz Telegram bildirimi yok.</p></div>"
    rows = []
    for item in items[:5]:
        if not isinstance(item, dict):
            continue
        keyword = escape(str(item.get("keyword") or "-"))
        channel = escape(str(item.get("channel") or "-"))
        created_at = escape(str(item.get("created_at") or "-"))
        message = escape(str(item.get("message") or ""))
        url = str(item.get("url") or "").strip()
        title = keyword
        if url:
            title_html = f"<a href='{escape(url, quote=True)}' target='_blank' rel='noopener noreferrer'>{title}</a>"
        else:
            title_html = f"<strong>{title}</strong>"
        rows.append(
            "<li>"
            f"{title_html}"
            f"<span>{channel} · {created_at}</span>"
            f"<em>{message}</em>"
            "</li>"
        )
    body = "".join(rows) if rows else "<li class='empty-error'>Henüz Telegram bildirimi yok.</li>"
    return f"<div class='telegram-recent'><h3>Son Telegram Bildirimleri</h3><ul>{body}</ul></div>"


def _send_test_notification():
    options = load_json(OPTIONS_PATH, {})
    user_key = str(options.get("pushover_user_key", "")).strip()
    api_token = str(options.get("pushover_api_token", "")).strip()
    timeout = int(options.get("request_timeout_seconds", 20) or 20)
    if not user_key or not api_token:
        return False, "Pushover anahtarları eksik. Config sekmesini kontrol et."
    payload = urllib.parse.urlencode(
        {
            "token": api_token,
            "user": user_key,
            "title": "Hermes test",
            "message": "Hermes test bildirimi. Ayarlar sağlıklı görünüyor.",
            "sound": "pushover",
            "priority": "0",
        }
    ).encode("utf-8")
    try:
        with urllib.request.urlopen(
            urllib.request.Request(PUSHOVER_URL, data=payload, method="POST"), timeout=timeout
        ) as response:
            response.read()
        return True, "Pushover test bildirimi gönderildi."
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        return False, f"Pushover hata verdi: {exc.code} {detail[:180]}"
    except Exception as exc:  # noqa: BLE001
        return False, f"Pushover test bildirimi gönderilemedi: {exc}"


def _reset_notifications_worker() -> None:
    try:
        from .config_loader import load_config
        from .service import check_once, reset_notification_suppression

        reset_count = reset_notification_suppression()
        config = load_config()
        check_once(config)
        log(f"Bildirim sifirlama sonrasi tek seferlik kontrol tamamlandi: sifirlanan_kayit={reset_count}")
    except Exception as exc:  # noqa: BLE001
        log(f"Bildirim sifirlama kontrolu tamamlanamadi: {exc}")
    finally:
        RESET_NOTIFICATIONS_LOCK.release()


def _reset_notifications_async():
    if not RESET_NOTIFICATIONS_LOCK.acquire(blocking=False):
        return False, "Bildirim sıfırlama zaten çalışıyor. Lütfen biraz sonra tekrar dene."
    thread = threading.Thread(target=_reset_notifications_worker, name="notification-reset-check", daemon=True)
    thread.start()
    return True, "Bildirim susturma hafızası sıfırlandı. Hedef altında kalan fırsatlar için tek seferlik kontrol arka planda başladı."


def _reset_price_history():
    if not PRICE_HISTORY_RESET_LOCK.acquire(blocking=False):
        return False, "Min/maks sıfırlama zaten çalışıyor. Lütfen biraz sonra tekrar dene."
    try:
        from .service import reset_price_history

        cleared_count = reset_price_history()
        return True, f"Min/maks fiyat geçmişi sıfırlandı. Temizlenen kayıt alanı: {cleared_count}."
    except Exception as exc:  # noqa: BLE001
        log(f"Min/maks fiyat gecmisi sifirlanamadi: {exc}")
        return False, f"Min/maks fiyat geçmişi sıfırlanamadı: {exc}"
    finally:
        PRICE_HISTORY_RESET_LOCK.release()


def _render_failed_links(detail):
    failed_links = detail.get("failed_links") or []
    if not failed_links:
        return ""
    rendered = []
    for item in failed_links:
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        message = escape(str(item.get("message") or "Hata ayrıntısı yok."))
        keywords = escape(str(item.get("keywords") or ""))
        keyword_line = f"<strong>{keywords}</strong>" if keywords else ""
        rendered.append(
            "<div class='failed-link'>"
            f"<span>Hatalı link</span>"
            f"{keyword_line}"
            f"<a href='{escape(url, quote=True)}' target='_blank' rel='noopener noreferrer'>{escape(_short_link(url, 96))}</a>"
            f"<em>{message}</em>"
            "</div>"
        )
    return "".join(rendered)


def _render_error_details(error_details):
    if not error_details:
        return "<li class='empty-error'>Son 24 saatte hata yok.</li>"
    items = []
    for detail in error_details:
        title = escape(str(detail.get("title") or "Hata"))
        meta = escape(str(detail.get("meta") or "Kontrol sırasında hata oluştu."))
        message = escape(str(detail.get("message") or "Hata ayrıntısı yok."))
        url = str(detail.get("url") or "").strip()
        link = ""
        if url:
            link = f"<a href='{escape(url, quote=True)}' target='_blank' rel='noopener noreferrer'>Linki aç</a>"
        items.append(
            "<li>"
            f"<strong>{title}</strong>"
            f"<span>{meta}</span>"
            f"<em>Hata: {message}</em>"
            f"{_render_failed_links(detail)}"
            f"{link}"
            "</li>"
        )
    return "".join(items)


# --- Hermes mobile panel: pastel card dashboard built on top of the same real data ---

_HM_STATUS_LABELS = {"below": "Hedef altı", "watch": "İzleniyor", "stock": "Stok bekliyor", "error": "Hata"}
_HM_STATUS_PILL = {"below": "hm-status-below", "watch": "hm-status-watch", "stock": "hm-status-stock", "error": "hm-status-error"}
_HM_STATUS_ORDER = ("below", "watch", "stock", "error")
_HM_SITE_ABBR = {
    "amazon": "AZ",
    "hepsiburada": "HB",
    "trendyol": "TY",
    "network": "AĞ",
    "beymenclub": "BC",
    "nordbron": "NB",
    "zara": "ZR",
    "hm": "HM",
}


def _hm_site_abbr(seller_text: str) -> str:
    site = _site_theme_class(seller_text).removeprefix("site-")
    if site in _HM_SITE_ABBR:
        return _HM_SITE_ABBR[site]
    fallback = repair_mojibake(seller_text).strip()[:2].upper()
    return fallback or "?"


def _hm_gauge(price, target, min_price, max_price):
    """Gauge fill (progress from the observed peak) + marker (where target sits) as 0-100 floats."""
    if price is None or target is None or min_price is None or max_price is None:
        return None
    span = max_price - min_price
    if span <= 0:
        fill = Decimal("100") if price <= target else Decimal("55")
        marker = Decimal("90")
    else:
        fill = max(Decimal("0"), min(Decimal("100"), ((max_price - price) / span) * 100))
        marker = max(Decimal("0"), min(Decimal("100"), ((max_price - target) / span) * 100))
    return float(fill), float(marker)


def _hm_build_cards(options, summary_payload, state):
    """One card per configured takip_edilenler entry, matched to its real price/state data via tracking_id."""
    watches = options.get("takip_edilenler") if isinstance(options.get("takip_edilenler"), list) else []
    rows = _deduplicate_dashboard_rows(
        summary_payload.get("rows") if isinstance(summary_payload.get("rows"), list) else []
    )
    stock_rows = summary_payload.get("stock_rows") if isinstance(summary_payload.get("stock_rows"), list) else []
    contexts = _error_contexts(options)
    state_items = [(key, value) for key, value in state.items() if key != "_meta" and isinstance(value, dict)]

    cards = []
    for index, item in enumerate(watches):
        if not isinstance(item, dict):
            continue
        tracking_id = tracking_id_for_watch(item)
        configured_urls = _watch_urls_from_options(item)
        canon_urls = {canonical_tracking_url(url) for url in configured_urls}

        matching_rows = [row for row in rows if tracking_id and str(row.get("tracking_id") or "") == tracking_id]
        if not matching_rows and canon_urls:
            matching_rows = [row for row in rows if canonical_tracking_url(row.get("product_url")) in canon_urls]

        matching_stock = (
            [row for row in stock_rows if isinstance(row, dict) and canonical_tracking_url(row.get("product_url")) in canon_urls]
            if canon_urls
            else []
        )

        matching_state = [
            (key, value) for key, value in state_items if tracking_id and str(value.get("tracking_id") or "") == tracking_id
        ]
        if not matching_state and canon_urls:
            matching_state = [
                (key, value)
                for key, value in state_items
                if canonical_tracking_url(value.get("configured_url")) in canon_urls
                or canonical_tracking_url(value.get("url")) in canon_urls
            ]

        active = parse_bool(item.get("active"), default=True)
        name = str(item.get("name") or "").strip()
        if not name and matching_rows:
            name = repair_mojibake(matching_rows[0].get("product_title") or "")
        if not name and matching_stock:
            name = repair_mojibake(matching_stock[0].get("product_title") or "")
        if not name and configured_urls:
            try:
                name = f"{site_label(detect_site_from_url(configured_urls[0]))} ürünü"
            except Exception:  # noqa: BLE001
                name = configured_urls[0]
        name = name or f"Takip {index + 1}"

        has_error = any(value.get("last_error") for _, value in matching_state)

        best_row = None
        if matching_rows:
            best_row = min(
                matching_rows,
                key=lambda row: _parse_turkish_money(row.get("price")) if _parse_turkish_money(row.get("price")) is not None else Decimal("Infinity"),
            )
        extra_rows = [row for row in matching_rows if row is not best_row]

        if matching_rows:
            status = "below" if any(_is_target_hit(row) for row in matching_rows) else "watch"
        elif matching_stock:
            status = "stock"
        elif has_error:
            status = "error"
        else:
            status = "watch"

        price_text = target_text = None
        price_dec = target_dec = min_dec = max_dec = None
        gauge = None
        if best_row:
            price_text = best_row.get("price")
            target_text = best_row.get("target")
            price_dec = _parse_turkish_money(price_text)
            target_dec = _parse_turkish_money(target_text)
            min_dec = _parse_turkish_money(best_row.get("min_price"))
            max_dec = _parse_turkish_money(best_row.get("max_price"))
            gauge = _hm_gauge(price_dec, target_dec, min_dec, max_dec)
        elif matching_stock:
            target_text = matching_stock[0].get("target")

        drop_pct = None
        if status == "below" and price_dec is not None and max_dec and max_dec > 0:
            drop_pct = round(float((max_dec - price_dec) / max_dec * 100))

        last_checked_raw = ""
        last_checked_dt = None
        for _, value in matching_state:
            candidate_raw = value.get("last_checked_at")
            candidate_dt = parse_iso_datetime(candidate_raw)
            if candidate_dt and (last_checked_dt is None or candidate_dt > last_checked_dt):
                last_checked_dt = candidate_dt
                last_checked_raw = candidate_raw

        error_detail = None
        if status == "error":
            for key, value in matching_state:
                if value.get("last_error"):
                    error_detail = _error_detail(key, value, contexts)
                    break

        sellers = []
        seen_sellers = set()
        for row in matching_rows + matching_stock:
            seller_text = repair_mojibake(row.get("seller") or "").strip()
            seller_key = seller_text.casefold()
            if not seller_text or seller_key in seen_sellers:
                continue
            seen_sellers.add(seller_key)
            sellers.append(seller_text)

        product_url = str((best_row or {}).get("product_url") or (configured_urls[0] if configured_urls else "")).strip()

        cards.append(
            {
                "index": index,
                "name": name,
                "active": active,
                "status": status,
                "price_text": price_text,
                "target_text": target_text,
                "gauge": gauge,
                "drop_pct": drop_pct,
                "extra_rows": extra_rows,
                "sellers": sellers,
                "last_checked_text": _relative_time_text(last_checked_raw) if last_checked_raw else "-",
                "error_detail": error_detail,
                "product_url": product_url,
            }
        )
    return cards


def _hm_render_status_block(card):
    status = card["status"]
    if status == "error":
        message = "Sayfa okunamadı, tekrar denenecek."
        if card.get("error_detail"):
            message = card["error_detail"].get("message") or message
        return f'<div class="hm-price-row"><span class="hm-price-best hm-error">{escape(message)}</span></div>'
    if status == "stock":
        return (
            '<div class="hm-price-row">'
            '<span class="hm-price-best" style="color:var(--peach-ink)">Stokta yok</span>'
            f'<span class="hm-price-target">hedef {escape(_display_tl(card["target_text"]))}</span>'
            "</div>"
        )
    price_class = "hm-below" if status == "below" else ""
    price_html = escape(_display_tl(card["price_text"])) if card["price_text"] is not None else "Henüz kontrol edilmedi"
    target_html = (
        f'<span class="hm-price-target">hedef {escape(_display_tl(card["target_text"]))}</span>'
        if card["target_text"] is not None
        else ""
    )
    return f'<div class="hm-price-row"><span class="hm-price-best {price_class}">{price_html}</span>{target_html}</div>'


def _hm_render_gauge(card):
    if card["status"] in {"error", "stock"} or not card.get("gauge"):
        return ""
    fill, marker = card["gauge"]
    fill_class = "hm-below" if card["status"] == "below" else "hm-watch"
    return (
        f'<div class="hm-gauge"><div class="hm-gauge-fill {fill_class}" style="width:{fill:.1f}%"></div>'
        f'<div class="hm-gauge-marker" style="left:{marker:.1f}%"></div></div>'
    )


def _hm_render_sites(card):
    if not card["sellers"]:
        return ""
    dots = "".join(
        f'<div class="hm-site-dot" title="{escape(seller)}">{escape(_hm_site_abbr(seller))}</div>'
        for seller in card["sellers"][:4]
    )
    return f'<div class="hm-sites">{dots}</div>'


def _hm_render_extra_results(card):
    rows = card["extra_rows"]
    if not rows:
        return ""
    items = []
    for row in rows[:8]:
        seller = escape(repair_mojibake(row.get("seller") or "-"))
        title = escape(repair_mojibake(row.get("product_title") or "-"))
        price = escape(_display_tl(row.get("price", "-")))
        url = str(row.get("product_url") or "").strip()
        label = (
            f'<a href="{escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">{seller}: {title}</a>'
            if url
            else f"{seller}: {title}"
        )
        items.append(f'<div class="hm-more-row">{label}<span>{price}</span></div>')
    return (
        f'<details class="hm-more-results"><summary>+{len(rows)} sonuç daha</summary>{"".join(items)}</details>'
    )


def _hm_render_card(card, base_path):
    status = card["status"]
    classes = "hm-card" + ("" if card["active"] else " hm-paused")
    paused_suffix = " · Duraklatıldı" if not card["active"] else ""
    pill = f'<span class="hm-status {_HM_STATUS_PILL[status]}">{escape(_HM_STATUS_LABELS[status])}{escape(paused_suffix)}</span>'
    toggle_value = "0" if card["active"] else "1"
    toggle_label = "Duraklat" if card["active"] else "Devam ettir"
    toggle_html = (
        f'<form class="hm-toggle-form" method="post" action="{base_path}/toggle-watch">'
        f'<input type="hidden" name="watch_index" value="{card["index"]}">'
        f'<input type="hidden" name="desired_active" value="{toggle_value}">'
        f'<button type="submit" class="hm-toggle{" hm-on" if card["active"] else ""}" aria-label="{escape(toggle_label, quote=True)}">'
        '<span class="hm-knob"></span></button>'
        "</form>"
    )
    return (
        f'<div class="{classes}" data-hm-status="{status}">'
        f'<div class="hm-card-top"><div class="hm-card-name">{escape(card["name"])}</div>{pill}</div>'
        f"{_hm_render_status_block(card)}"
        f"{_hm_render_gauge(card)}"
        f'<div class="hm-card-bottom">{_hm_render_sites(card)}<div class="hm-meta">{escape(card["last_checked_text"])}</div>{toggle_html}</div>'
        f"{_hm_render_extra_results(card)}"
        "</div>"
    )


def _hm_render_card_list(cards, base_path):
    if not cards:
        return '<div class="hm-empty">Henüz takip edilen bir ürün yok. Ayarlar sayfasından ekleyebilirsin.</div>'
    return f'<div class="hm-list">{"".join(_hm_render_card(card, base_path) for card in cards)}</div>'


def _hm_render_chips(cards):
    counts = dict.fromkeys(_HM_STATUS_ORDER, 0)
    for card in cards:
        counts[card["status"]] += 1
    chips = [f'<button type="button" class="hm-chip hm-active" data-hm-filter="all">Tümü · {len(cards)}</button>']
    chips.extend(
        f'<button type="button" class="hm-chip" data-hm-filter="{key}">{escape(_HM_STATUS_LABELS[key])} · {counts[key]}</button>'
        for key in _HM_STATUS_ORDER
    )
    return f'<div class="hm-chips">{"".join(chips)}</div>'


def _hm_render_deal_carousel(cards):
    deals = sorted(
        (card for card in cards if card["status"] == "below" and card["drop_pct"] is not None),
        key=lambda card: card["drop_pct"],
        reverse=True,
    )[:6]
    if not deals:
        return ""
    items = []
    for card in deals:
        seller = card["sellers"][0] if card["sellers"] else ""
        url = card["product_url"]
        href = escape(url, quote=True) if url else "#"
        target_attr = ' target="_blank" rel="noopener noreferrer"' if url else ""
        items.append(
            f'<a class="hm-deal-card" href="{href}"{target_attr}>'
            '<span class="hm-deal-drop"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">'
            f'<path d="M12 5v14M5 12l7 7 7-7"/></svg>%{card["drop_pct"]} düştü</span>'
            f'<div class="hm-deal-name">{escape(card["name"])}</div>'
            f'<div class="hm-deal-price">{escape(_display_tl(card["price_text"]))}</div>'
            f'<div class="hm-deal-site">{escape(seller)}</div>'
            "</a>"
        )
    return '<div class="hm-section-title">Yeni fırsatlar</div>' f'<div class="hm-deals">{"".join(items)}</div>'


def _hm_render_header(base_path, last_update_text, has_alert):
    dot_class = " hm-has-dot" if has_alert else ""
    return f"""
    <header class="hm-header" id="top">
      <div class="hm-brand">
        <div class="hm-brand-mark"><svg viewBox="0 0 24 24" fill="none"><path d="M4 16L9 9L14 13L20 5" stroke="#4B47D6" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg></div>
        <div class="hm-brand-text"><h1>Hermes</h1><p>Son kontrol: {escape(last_update_text)}</p></div>
      </div>
      <div class="hm-header-actions">
        <a class="hm-icon-btn{dot_class}" href="#hm-panel" aria-label="Bildirimler ve bakım"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M18 8a6 6 0 10-12 0c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.7 21a2 2 0 01-3.4 0"/></svg></a>
        <a class="hm-icon-btn" href="{base_path}/settings" aria-label="Ayarlar"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 00.3 1.9l.1.1a2 2 0 11-2.8 2.8l-.1-.1a1.7 1.7 0 00-1.9-.3 1.7 1.7 0 00-1 1.6V21a2 2 0 11-4 0v-.2a1.7 1.7 0 00-1-1.5 1.7 1.7 0 00-1.9.3l-.1.1a2 2 0 11-2.8-2.8l.1-.1a1.7 1.7 0 00.3-1.9 1.7 1.7 0 00-1.5-1H3a2 2 0 110-4h.2a1.7 1.7 0 001.5-1 1.7 1.7 0 00-.3-1.9l-.1-.1a2 2 0 112.8-2.8l.1.1a1.7 1.7 0 001.9.3H9a1.7 1.7 0 001-1.5V3a2 2 0 114 0v.2a1.7 1.7 0 001 1.5 1.7 1.7 0 001.9-.3l.1-.1a2 2 0 112.8 2.8l-.1.1a1.7 1.7 0 00-.3 1.9V9a1.7 1.7 0 001.5 1h.2a2 2 0 110 4h-.2a1.7 1.7 0 00-1.5 1z"/></svg></a>
      </div>
    </header>
    """


def _hm_render_nav(base_path):
    return f"""
    <nav class="hm-nav">
      <a class="hm-nav-item hm-active" href="#top"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M3 9l9-6 9 6v11a1 1 0 01-1 1h-5v-7H9v7H4a1 1 0 01-1-1z"/></svg>Panel</a>
      <a class="hm-nav-item" href="#hm-list"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M4 5h16M4 12h16M4 19h10"/></svg>Takipler</a>
      <a class="hm-nav-item" href="#hm-telegram"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M21 5L2 12l6 3m13-10l-4 16-6-6m10-10L8 15"/></svg>Telegram</a>
      <a class="hm-nav-item" href="{base_path}/settings"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 00.3 1.9l.1.1a2 2 0 11-2.8 2.8l-.1-.1a1.7 1.7 0 00-1.9-.3 1.7 1.7 0 00-1 1.6V21a2 2 0 11-4 0v-.2a1.7 1.7 0 00-1-1.5 1.7 1.7 0 00-1.9.3l-.1.1a2 2 0 11-2.8-2.8l.1-.1a1.7 1.7 0 00.3-1.9 1.7 1.7 0 00-1.5-1H3a2 2 0 110-4h.2a1.7 1.7 0 001.5-1 1.7 1.7 0 00-.3-1.9l-.1-.1a2 2 0 112.8-2.8l.1.1a1.7 1.7 0 001.9.3H9a1.7 1.7 0 001-1.5V3a2 2 0 114 0v.2a1.7 1.7 0 001 1.5 1.7 1.7 0 001.9-.3l.1-.1a2 2 0 112.8 2.8l-.1.1a1.7 1.7 0 00-.3 1.9V9a1.7 1.7 0 001.5 1h.2a2 2 0 110 4h-.2a1.7 1.7 0 00-1.5 1z"/></svg>Ayarlar</a>
    </nav>
    """


def _hm_render_fab(base_path):
    return (
        f'<a class="hm-fab" href="{base_path}/settings#new-watch" aria-label="Yeni takip ekle">'
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M12 5v14M5 12h14"/></svg>'
        "</a>"
    )


_HM_SCRIPT = """
<script>
  document.querySelectorAll('.hm-chip').forEach((chip) => {
    chip.addEventListener('click', () => {
      document.querySelectorAll('.hm-chip').forEach((c) => c.classList.remove('hm-active'));
      chip.classList.add('hm-active');
      const filter = chip.dataset.hmFilter;
      document.querySelectorAll('.hm-list > .hm-card').forEach((card) => {
        card.hidden = filter !== 'all' && card.dataset.hmStatus !== filter;
      });
    });
  });
</script>"""


def _toggle_watch_active(body) -> tuple[bool, str]:
    """Flip one configured watch's active flag in place and persist through the normal save+restart flow."""
    try:
        if not isinstance(body, (bytes, bytearray)):
            raise ValueError("Geçersiz istek.")
        form = urllib.parse.parse_qs(body.decode("utf-8", errors="replace"))
        index = int(form.get("watch_index", [""])[0])
        desired_active = parse_bool(form.get("desired_active", ["1"])[0], default=True)
        options = load_json(OPTIONS_PATH, {})
        if not isinstance(options, dict):
            raise ValueError("Ayarlar okunamadı.")
        watches = options.get("takip_edilenler")
        if not isinstance(watches, list) or not (0 <= index < len(watches)) or not isinstance(watches[index], dict):
            raise ValueError("Takip kaydı bulunamadı.")
        watches[index]["active"] = desired_active
        name = str(watches[index].get("name") or "").strip() or f"Takip {index + 1}"

        from .settings_ui import save_options_and_restart

        save_options_and_restart(options)
        verb = "devam ettiriliyor" if desired_active else "duraklatıldı"
        return True, f"{name} takibi {verb}."
    except Exception as exc:  # noqa: BLE001
        return False, f"Takip durumu değiştirilemedi: {exc}"


def _render_page(path: str = "/", error_detail_limit: int | None = 4) -> bytes:
    return _render_dashboard_page(path, ".", error_detail_limit)


def _render_error_card(summary, extra_class: str = "") -> str:
    error_class = "status-error" if int(summary.get("errors") or 0) > 0 else ""
    classes = " ".join(item for item in ("card", "error-card", error_class, extra_class) if item)
    return (
        f"<section class='{escape(classes)}'><span>Hata sayısı (son 24 saat)</span>"
        + f"<strong>{escape(str(summary.get('errors') or 0))}</strong>"
        + f"<ul>{_render_error_details(summary.get('error_details') or [])}</ul></section>"
    )


def _public_token_from_path(path: str) -> str:
    parsed = urllib.parse.urlparse(path)
    parts = [urllib.parse.unquote(part) for part in parsed.path.split("/") if part]
    if len(parts) >= 2 and parts[0] == "public":
        return parts[1]
    params = urllib.parse.parse_qs(parsed.query)
    return str(params.get("token", [""])[0]).strip()


def _public_base_path(path: str) -> str:
    parsed = urllib.parse.urlparse(path)
    parts = [urllib.parse.quote(urllib.parse.unquote(part), safe="") for part in parsed.path.split("/") if part]
    if len(parts) >= 2 and parts[0] == "public":
        return "/" + "/".join(parts[:2])
    token = _public_token_from_path(path)
    return f"/public/{urllib.parse.quote(token, safe='')}" if token else "/public"


def _public_dashboard_allowed(path: str) -> bool:
    options = load_json(OPTIONS_PATH, {})
    if not isinstance(options, dict):
        return False
    if not parse_bool(options.get("public_dashboard_enabled"), default=False):
        return False
    expected_token = str(options.get("public_dashboard_token") or "").strip()
    if len(expected_token) < 24:
        return False
    return _public_token_from_path(path) == expected_token


def _render_dashboard_page(path: str, base_path: str, error_detail_limit: int | None = None) -> bytes:
    payload = load_json(SUMMARY_PATH, {})
    params = urllib.parse.parse_qs(urllib.parse.urlparse(path).query)
    action_status = ""
    action_message = ""
    for key in ("test", "reset", "history", "settings", "toggle"):
        status = params.get(key, [""])[0]
        if status in {"ok", "fail"}:
            action_status = status
            action_message = params.get("msg", [""])[0]
            break
    notice_html = ""
    if action_status:
        notice_class = "notice-ok" if action_status == "ok" else "notice-fail"
        notice_html = f"<p class='notice {notice_class}'>{escape(action_message)}</p>"
    web_app_head = render_web_app_head(base_path)
    base_path = escape(base_path, quote=True)
    options = load_json(OPTIONS_PATH, {})
    options = options if isinstance(options, dict) else {}
    state = load_json(STATE_PATH, {})
    state = state if isinstance(state, dict) else {}
    telegram_summary = _collect_telegram_summary(options)
    telegram_recent_html = _render_telegram_recent_notifications(
        telegram_summary.get("recent_notifications") or []
    )
    error_card_html = _render_error_card(
        _collect_summary(error_detail_limit=error_detail_limit),
        "public-error-card",
    )
    cycle_duration = "-"
    last_update = "-"
    if isinstance(payload, dict):
        cycle_duration = escape(
            _duration_text(payload.get("cycle_duration_seconds"), payload.get("cycle_duration_minutes") or "-")
        )
        last_update = escape(_relative_time_text(payload.get("checked_at")))
    public_cycle_row = (
        "<div class='public-cycle-row'>"
        f"<section class='public-cycle-pill'><span>Çevrim süresi</span><strong>{cycle_duration}</strong></section>"
        f"<section class='public-cycle-pill'><span>Son güncelleme</span><strong>{last_update}</strong></section>"
        "</div>"
    )
    confirm_script = """
<script>
  document.querySelectorAll('form[data-confirm]').forEach((form) => {
    form.addEventListener('submit', (event) => {
      const message = form.getAttribute('data-confirm') || 'Bu işlemi yapmak istediğine emin misin?';
      if (!window.confirm(message)) {
        event.preventDefault();
      }
    });
  });
</script>"""

    cards = _hm_build_cards(options, payload if isinstance(payload, dict) else {}, state)
    has_alert = any(card["status"] == "error" for card in cards)
    header_html = _hm_render_header(base_path, last_update, has_alert)
    chips_html = _hm_render_chips(cards)
    deals_html = _hm_render_deal_carousel(cards)
    card_list_html = _hm_render_card_list(cards, base_path)
    telegram_section_html = f'<div id="hm-telegram" style="padding:0 16px; margin-top:20px;">{telegram_recent_html}</div>'
    fab_html = _hm_render_fab(base_path)
    nav_html = _hm_render_nav(base_path)
    classic_html = f"""
    <details class="hm-panel" id="hm-panel">
      <summary>Klasik özet ve bakım</summary>
      <div class="actions public-actions" style="margin-top:12px">
        <a class="button secondary" href="{base_path}/settings">Ayarlar</a>
        <a class="button secondary" href="{base_path}/link-test">Test</a>
        <form class="inline-form" method="post" action="{base_path}/test-pushover"><button class="button test" type="submit">Pushover</button></form>
        <form class="inline-form" method="post" action="{base_path}/reset-notifications" data-confirm="Bildirim susturma hafızası sıfırlanacak ve hedef altında kalan fırsatlar için tek seferlik kontrol başlatılacak. Devam etmek istiyor musun?"><button class="button secondary" type="submit">Bildirim Sıfırla</button></form>
        <form class="inline-form" method="post" action="{base_path}/reset-price-history" data-confirm="Min/maks fiyat geçmişi temizlenecek ve güncel fiyattan yeniden başlayacak. Devam etmek istiyor musun?"><button class="button secondary" type="submit">Min/Maks Sıfırla</button></form>
      </div>
      {public_cycle_row}
      {_render_table()}
      {error_card_html}
    </details>
    """
    html = f"""<!doctype html>
<html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover"><meta name="theme-color" content="#F5F4FB"><meta name="apple-mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-title" content="Hermes">{web_app_head}<meta http-equiv="refresh" content="60"><title>Hermes</title><style>{DASHBOARD_CSS}</style></head><body class="public"><div class="hm-app">{header_html}{notice_html}{chips_html}{deals_html}<div class="hm-section-title" id="hm-list">Takip edilenler</div>{card_list_html}{telegram_section_html}<div style="padding:0 16px; margin-top:20px;">{classic_html}</div></div>{fab_html}{nav_html}{confirm_script}{_HM_SCRIPT}</body></html>"""
    return html.encode("utf-8")


def _render_public_page(path: str):
    if not _public_dashboard_allowed(path):
        return 404, b"not found\n"
    return 200, _render_dashboard_page(path, _public_base_path(path), error_detail_limit=None)


class _StatusHandler(BaseHTTPRequestHandler):
    def _redirect_with_message(self, flag_name: str, ok: bool, message: str) -> None:
        status = "ok" if ok else "fail"
        self.send_response(303)
        self.send_header("Location", f"?{flag_name}={status}&msg={urllib.parse.quote(message)}")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def do_GET(self) -> None:
        path = urllib.parse.urlparse(self.path).path.rstrip("/")
        status = 200
        if path == "/health":
            payload = b"ok\n"
            content_type = "text/plain; charset=utf-8"
        elif path == "/icon.png":
            payload = HERMES_ICON_PNG
            content_type = "image/png"
        elif path == "/icon.svg":
            payload = HERMES_ICON_SVG
            content_type = "image/svg+xml"
        elif path == "/manifest.webmanifest":
            payload = render_web_manifest(".")
            content_type = "application/manifest+json; charset=utf-8"
        elif path == "/link-test":
            payload = render_link_test_page(DASHBOARD_CSS, "./link-test", "./")
            content_type = "text/html; charset=utf-8"
        elif path == "/public" or path.startswith("/public/"):
            status, payload = _render_public_page(self.path)
            content_type = "text/html; charset=utf-8" if status == 200 else "text/plain; charset=utf-8"
        else:
            payload = _render_page(self.path)
            content_type = "text/html; charset=utf-8"
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self) -> None:
        content_length = int(self.headers.get("Content-Length", "0") or 0)
        body = self.rfile.read(content_length) if content_length else b""
        path = urllib.parse.urlparse(self.path).path.rstrip("/")
        if path == "/link-test":
            payload = render_link_test_from_request(DASHBOARD_CSS, "./link-test", "./", body)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if path.endswith("/reset-notifications"):
            ok, message = _reset_notifications_async()
            self._redirect_with_message("reset", ok, message)
            return
        if path.endswith("/reset-price-history"):
            ok, message = _reset_price_history()
            self._redirect_with_message("history", ok, message)
            return
        if not path.endswith("/test-pushover"):
            self.send_error(404)
            return
        ok, message = _send_test_notification()
        self._redirect_with_message("test", ok, message)

    def log_message(self, _format, *args) -> None:
        _ = args
        return


def run_dashboard() -> None:
    ThreadingHTTPServer(("0.0.0.0", WEB_PORT), _StatusHandler).serve_forever()


if __name__ == "__main__":
    run_dashboard()
