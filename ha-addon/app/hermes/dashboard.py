import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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
:root {
  color-scheme: dark;
  --bg: #121519;
  --panel: #1b1e24;
  --card: #242930;
  --line: #323843;
  --text: #eef1f5;
  --muted: #a2afbd;
  --accent: #c2d5f0;
  --accent2: #d6e4f7;
  --ok: #b2ebd5;
  --warn: #ffe3b3;
  --bad: #fca6b5;
  --head: #2a2f39;
  --transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
}
* {
  box-sizing: border-box;
  -webkit-tap-highlight-color: transparent;
}
body {
  margin: 0;
  font-family: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
  background: radial-gradient(circle at top left, #29303c, var(--bg) 65%);
  color: var(--text);
  font-size: 14px;
  line-height: 1.5;
  letter-spacing: -0.01em;
}
main {
  max-width: 1100px;
  margin: 0 auto;
  padding: 32px 20px 48px;
}
.hero {
  border: 1px solid var(--line);
  border-radius: 24px;
  padding: 28px;
  background: rgba(27, 30, 36, 0.85);
  backdrop-filter: blur(16px);
  box-shadow: 0 20px 50px rgba(0,0,0,0.4);
}
p {
  margin: 0;
  color: var(--muted);
  line-height: 1.6;
  font-size: 13.5px;
}
.badge {
  display: inline-flex;
  margin-bottom: 16px;
  color: #111418;
  background: linear-gradient(135deg, #ffd3b6, #ffaaa5);
  border-radius: 20px;
  padding: 8px 18px;
  font-size: clamp(26px, 5vw, 44px);
  line-height: 1;
  letter-spacing: -0.04em;
  font-weight: 800;
  box-shadow: 0 4px 15px rgba(255, 170, 165, 0.25);
  user-select: none;
}
.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 24px;
  align-items: center;
}
.inline-form {
  margin: 0;
}
.button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 42px;
  padding: 0 18px;
  border-radius: 14px;
  border: 1px solid transparent;
  text-decoration: none;
  font-weight: 700;
  font-size: 13px;
  cursor: pointer;
  transition: var(--transition);
}
.button.primary {
  color: #111418;
  background: linear-gradient(135deg, var(--accent), var(--accent2));
  box-shadow: 0 4px 12px rgba(194, 213, 240, 0.25);
}
.button.primary:hover {
  transform: translateY(-2px);
  box-shadow: 0 6px 16px rgba(194, 213, 240, 0.35);
  background: linear-gradient(135deg, var(--accent2), var(--accent));
}
.button.secondary {
  color: var(--text);
  background: #2a2f38;
  border-color: var(--line);
}
.button.secondary:hover {
  background: #363d49;
  border-color: #4b5566;
  transform: translateY(-1px);
}
.button.test {
  color: #f5f7fa;
  background: linear-gradient(135deg, #4f5664, #2a2f38);
  border-color: #565e6d;
}
.button.test:hover {
  transform: translateY(-2px);
  background: linear-gradient(135deg, #5c6475, #363d49);
}
.notice {
  margin-top: 18px;
  padding: 14px 16px;
  border-radius: 14px;
  font-weight: 600;
  font-size: 13.5px;
  line-height: 1.4;
  animation: fadeIn 0.3s ease-out;
}
@keyframes fadeIn {
  from { opacity: 0; transform: translateY(5px); }
  to { opacity: 1; transform: translateY(0); }
}
.notice-ok {
  color: #c9ebd8;
  background: rgba(178, 235, 213, 0.1);
  border: 1px solid rgba(178, 235, 213, 0.35);
}
.notice-fail {
  color: #ffcad1;
  background: rgba(252, 166, 181, 0.1);
  border: 1px solid rgba(252, 166, 181, 0.35);
}
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 14px;
  margin-top: 20px;
}
.card {
  border: 1px solid var(--line);
  border-radius: 16px;
  padding: 16px;
  background: var(--card);
  min-height: 90px;
  transition: var(--transition);
}
.card:hover {
  transform: translateY(-3px);
  box-shadow: 0 10px 25px rgba(0,0,0,0.15);
  border-color: #434c5b;
}
.card span {
  display: block;
  color: var(--muted);
  font-size: 12px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  margin-bottom: 8px;
}
.card strong {
  display: block;
  font-size: 20px;
  line-height: 1.15;
  font-weight: 800;
  overflow-wrap: anywhere;
}
.card.status-ok {
  border-color: rgba(178, 235, 213, 0.35);
  background: linear-gradient(135deg, rgba(178, 235, 213, 0.08), var(--card) 75%);
}
.card.status-ok strong {
  color: var(--ok);
}
.card.status-warn strong {
  color: var(--warn);
}
.card.status-error strong {
  color: var(--bad);
}
.error-card {
  grid-column: 1 / -1;
}
.error-card ul {
  display: grid;
  gap: 12px;
  margin: 14px 0 0;
  padding: 0;
  list-style: none;
}
.error-card li {
  display: grid;
  gap: 8px;
  padding: 14px;
  border: 1px solid rgba(252, 166, 181, 0.25);
  border-radius: 14px;
  background: rgba(252, 166, 181, 0.06);
  font-size: 13px;
  line-height: 1.4;
  overflow-wrap: anywhere;
  transition: var(--transition);
}
.error-card li:hover {
  background: rgba(252, 166, 181, 0.09);
  border-color: rgba(252, 166, 181, 0.4);
}
.error-card li.empty-error {
  border-color: rgba(178, 235, 213, 0.2);
  background: rgba(178, 235, 213, 0.04);
  color: var(--muted);
}
.error-card li strong {
  font-size: 14px;
  color: var(--text);
  font-weight: 700;
}
.error-card li span {
  margin: 0;
  color: var(--muted);
}
.error-card li em {
  color: #ffcad1;
  font-style: normal;
  font-weight: 500;
}
.error-card li a {
  color: var(--accent);
  font-weight: 700;
  text-decoration: none;
  width: max-content;
  transition: var(--transition);
}
.error-card li a:hover {
  color: var(--accent2);
  text-decoration: underline;
}
.failed-link {
  display: grid;
  gap: 4px;
  margin-top: 6px;
  padding: 10px 12px;
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid rgba(255, 255, 255, 0.08);
}
.failed-link span {
  color: #e0e2e3;
  font-weight: 700;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
.failed-link strong {
  color: var(--text);
  font-size: 13px;
}
.failed-link em {
  color: #c8cccf;
  font-size: 11.5px;
}
.public-error-card {
  margin-top: 24px;
}
.summary-panel {
  margin-top: 24px;
  border: 1px solid var(--line);
  border-radius: 20px;
  padding: 20px;
  background: var(--card);
}
.summary-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}
.summary-head h2 {
  font-size: 20px;
  font-weight: 800;
  margin: 0;
  letter-spacing: -0.02em;
}
.summary-head span {
  color: var(--muted);
  font-size: 12.5px;
  white-space: nowrap;
  font-weight: 500;
}
.table-section + .table-section {
  margin-top: 24px;
}
.table-section h3 {
  margin: 0 0 12px;
  font-size: 14.5px;
  font-weight: 700;
  color: #f0f1f0;
}
.deals-section h3 {
  color: var(--ok);
}
.telegram-recent {
  margin-top: 18px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 16px;
  padding: 16px;
  background: rgba(20, 22, 24, 0.4);
}
.telegram-recent h3 {
  margin: 0 0 12px;
  font-size: 14px;
  font-weight: 700;
  color: #eef1f5;
}
.telegram-recent p {
  color: var(--muted);
}
.telegram-recent ul {
  display: grid;
  gap: 10px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.telegram-recent li {
  display: grid;
  gap: 6px;
  padding: 12px 14px;
  border: 1px solid rgba(255, 255, 255, 0.06);
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.02);
  transition: var(--transition);
}
.telegram-recent li:hover {
  background: rgba(255, 255, 255, 0.04);
  border-color: rgba(255, 255, 255, 0.1);
}
.telegram-recent li a, .telegram-recent li strong {
  color: #f1f3f3;
  font-size: 13.5px;
  font-weight: 700;
  text-decoration: none;
  overflow-wrap: anywhere;
}
.telegram-recent li a:hover {
  color: var(--accent);
  text-decoration: underline;
}
.telegram-recent li span {
  color: var(--muted);
  font-size: 11px;
}
.telegram-recent li em {
  color: #d9dcdd;
  font-size: 12.5px;
  font-style: normal;
  line-height: 1.4;
  overflow-wrap: anywhere;
}
.table-wrap {
  overflow-x: auto;
  border: 1px solid var(--line);
  border-radius: 16px;
  box-shadow: 0 4px 15px rgba(0,0,0,0.1);
}
table {
  width: 100%;
  border-collapse: collapse;
  min-width: 880px;
}
th, td {
  padding: 10px 12px;
  border-bottom: 1px solid var(--line);
  text-align: right;
  white-space: nowrap;
}
th {
  color: #e1e3e3;
  background: var(--head);
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
td {
  color: var(--text);
  font-size: 13.5px;
  font-variant-numeric: tabular-nums;
}
tr:last-child td {
  border-bottom: none;
}
th:nth-child(1), td:nth-child(1) {
  width: 110px;
}
th:nth-child(1), td:nth-child(1), th:nth-child(2), td:nth-child(2) {
  text-align: left;
}
th:not(:nth-child(2)), td:not(:nth-child(2)) {
  width: 110px;
}
th:nth-child(6), td:nth-child(6) {
  width: 160px;
}
.empty-row td {
  color: var(--muted);
  text-align: left;
  background: rgba(255, 255, 255, 0.015);
  padding: 16px;
  font-weight: 500;
}
.search-result-group {
  margin: 12px 0;
  overflow: hidden;
  border: 1px solid var(--line);
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.02);
  transition: var(--transition);
}
.search-result-group:hover {
  border-color: #434c5b;
}
.search-result-group summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 16px;
  color: #f0f1f0;
  font-size: 14px;
  font-weight: 700;
  cursor: pointer;
  list-style: none;
}
.search-result-group summary::-webkit-details-marker {
  display: none;
}
.search-result-group summary::before {
  content: '▸';
  display: inline-block;
  margin-right: 10px;
  color: #a2afbd;
  font-size: 18px;
  transition: transform 0.2s ease;
}
.search-result-group[open] summary::before {
  transform: rotate(90deg);
}
.search-result-group summary strong {
  margin-right: auto;
}
.search-result-group summary span {
  color: var(--muted);
  font-size: 11.5px;
  font-weight: 600;
  white-space: nowrap;
}
.search-result-group[open] summary {
  border-bottom: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.04);
}
.search-result-group .table-wrap {
  border: 0;
  border-radius: 0;
  box-shadow: none;
}

tbody tr.site-amazon {
  --site-line: #fcd38a;
  --site-bg: rgba(252, 211, 138, 0.06);
  --site-bg-strong: rgba(252, 211, 138, 0.12);
  --site-link: #ffe0a3;
}
tbody tr.site-hepsiburada {
  --site-line: #fca583;
  --site-bg: rgba(252, 165, 131, 0.06);
  --site-bg-strong: rgba(252, 165, 131, 0.12);
  --site-link: #ffbc9f;
}
tbody tr.site-trendyol {
  --site-line: #fcb1d2;
  --site-bg: rgba(252, 177, 210, 0.06);
  --site-bg-strong: rgba(252, 177, 210, 0.12);
  --site-link: #ffc9e1;
}
tbody tr.site-network {
  --site-line: #9eeddd;
  --site-bg: rgba(158, 237, 221, 0.06);
  --site-bg-strong: rgba(158, 237, 221, 0.12);
  --site-link: #bbfcfa;
}
tbody tr.site-beymenclub {
  --site-line: #f4cfb6;
  --site-bg: rgba(244, 207, 182, 0.06);
  --site-bg-strong: rgba(244, 207, 182, 0.12);
  --site-link: #ffdfcb;
}
tbody tr.site-nordbron {
  --site-line: #b2dbff;
  --site-bg: rgba(178, 219, 255, 0.06);
  --site-bg-strong: rgba(178, 219, 255, 0.12);
  --site-link: #cfe8ff;
}
tbody tr.site-zara {
  --site-line: #cef2b5;
  --site-bg: rgba(206, 242, 181, 0.06);
  --site-bg-strong: rgba(206, 242, 181, 0.12);
  --site-link: #e2fccb;
}
tbody tr.site-hm {
  --site-line: #dec4ff;
  --site-bg: rgba(222, 196, 255, 0.06);
  --site-bg-strong: rgba(222, 196, 255, 0.12);
  --site-link: #ebdaff;
}
tbody tr.site-other {
  --site-line: #b2c2d3;
  --site-bg: rgba(178, 194, 211, 0.05);
  --site-bg-strong: rgba(178, 194, 211, 0.1);
  --site-link: #dbe4ee;
}
tbody tr[class*='site-'] td {
  background: linear-gradient(90deg, var(--site-bg), rgba(30, 34, 42, 0.2));
}
tbody tr[class*='site-'] td:first-child {
  border-left: 4px solid var(--site-line);
  color: var(--site-link);
  font-weight: 700;
}
tbody tr[class*='site-'] .product-cell a {
  color: var(--site-link);
}
tbody tr[class*='site-']:hover td {
  background: linear-gradient(90deg, rgba(255, 255, 255, 0.04), var(--site-bg-strong));
}
.product-cell {
  max-width: 380px;
  white-space: normal;
  line-height: 1.35;
}
.product-cell a {
  color: #e4e6e6;
  text-decoration: none;
  font-weight: 500;
  transition: var(--transition);
}
.product-cell a:hover {
  color: var(--accent);
  text-decoration: underline;
}
.product-cell .warehouse-tag {
  display: inline-block;
  margin: 0 8px 0 0;
  padding: 2px 8px;
  border-radius: 6px;
  background: rgba(252, 211, 138, 0.15);
  border: 1px solid rgba(252, 211, 138, 0.3);
  color: var(--site-line);
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 0.04em;
  line-height: 1.1;
  vertical-align: middle;
}
.product-cell span {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  text-overflow: ellipsis;
}
.deal-row td {
  color: var(--ok);
}
.deal-row td:first-child {
  color: var(--site-link);
}
.deal-row .product-cell a {
  color: var(--ok);
}
.note {
  margin-top: 24px;
  border-left: 4px solid var(--muted);
  padding: 14px 16px;
  background: rgba(255, 255, 255, 0.02);
  border-radius: 12px;
  font-size: 13.5px;
}
.footer {
  margin-top: 24px;
  font-size: 12px;
  color: var(--muted);
}
.public main {
  max-width: 1200px;
}
.public .hero {
  padding: 24px;
}
.public .badge {
  font-size: clamp(24px, 4vw, 38px);
}
.public-actions {
  margin: 20px 0 10px;
}
.public-actions .button {
  min-width: 140px;
}
.public-cycle-row {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  margin: 12px 0 6px;
}
.public-cycle-pill {
  min-width: 0;
  min-height: 44px;
  padding: 8px 12px;
  border: 1px solid var(--line);
  border-radius: 14px;
  background: #1f242d;
  transition: var(--transition);
}
.public-cycle-pill:hover {
  border-color: #434c5b;
}
.public-cycle-pill span {
  display: block;
  color: var(--muted);
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.05em;
  text-transform: uppercase;
}
.public-cycle-pill strong {
  display: block;
  margin-top: 3px;
  font-size: 14.5px;
  line-height: 1.1;
  color: var(--text);
}
@media (max-width: 720px) {
  body {
    font-size: 13px;
    background: var(--bg);
  }
  main {
    padding: 12px 10px 32px;
  }
  .hero {
    border-radius: 20px;
    padding: 16px;
  }
  .public main {
    padding: 0;
  }
  .public .hero {
    min-height: 100vh;
    border-width: 0;
    border-radius: 0;
    padding: 16px 12px 32px;
    box-shadow: none;
    background: var(--bg);
  }
  .badge {
    margin-bottom: 12px;
    padding: 6px 14px;
    font-size: 30px;
  }
  p {
    font-size: 12.5px;
  }
  .actions {
    gap: 10px;
  }
  .public-actions {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 10px;
  }
  .public-actions .button, .public-actions .inline-form {
    width: 100%;
    min-width: 0;
    margin: 0;
  }
  .public-actions .button {
    min-height: 46px;
    padding: 0 12px;
    font-size: 12.5px;
    border-radius: 12px;
  }
  .public-cycle-row {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 10px;
    margin: 14px 0 6px;
  }
  .public-cycle-pill {
    min-height: 72px;
    padding: 12px 14px;
    border-radius: 16px;
  }
  .public-cycle-pill span {
    font-size: 11px;
  }
  .public-cycle-pill strong {
    margin-top: 6px;
    font-size: 21px;
  }
  .link-test-options {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .link-test-options .link-test-checkbox {
    min-height: 42px;
  }
  .link-test-result tbody tr[class*='site-'] {
    grid-template-columns: 1fr;
  }
  .link-test-result tbody tr[class*='site-'] .price-cell {
    grid-column: 1 / -1;
  }
  .grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 10px;
  }
  .card {
    min-height: 76px;
    padding: 12px;
    border-radius: 14px;
  }
  .card span {
    font-size: 11px;
    margin-bottom: 6px;
  }
  .card strong {
    font-size: 16px;
  }
  .summary-panel {
    margin-top: 16px;
    padding: 14px;
    border-radius: 18px;
  }
  .summary-head {
    align-items: flex-start;
    flex-direction: column;
    gap: 6px;
    margin-bottom: 12px;
  }
  .summary-head h2 {
    font-size: 18px;
  }
  .summary-head span {
    white-space: normal;
    font-size: 12px;
  }
  .public .summary-head span {
    font-size: 15px;
    line-height: 1.3;
    color: var(--accent);
    font-weight: 700;
  }
  .table-section h3 {
    font-size: 13.5px;
  }
  .table-wrap {
    overflow: visible;
    border: 0;
    border-radius: 0;
    box-shadow: none;
  }
  .search-result-group {
    margin: 10px 0;
    border-radius: 14px;
  }
  .search-result-group summary {
    min-height: 50px;
    padding: 12px 14px;
    font-size: 13.5px;
  }
  .search-result-group summary span {
    font-size: 11px;
  }
  table {
    min-width: 0;
  }
  thead {
    display: none;
  }
  table, tbody, td {
    display: block;
    width: 100%;
  }
  tbody tr[class*='site-'] {
    position: relative;
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 8px 10px;
    margin: 0 0 12px;
    border: 1px solid var(--site-line);
    border-left: 6px solid var(--site-line);
    border-radius: 16px;
    padding: 12px 14px;
    background: linear-gradient(135deg, var(--site-bg-strong), rgba(24, 28, 36, 0.95) 60%), rgba(24, 28, 36, 0.92);
    box-shadow: 0 6px 20px rgba(0,0,0,0.15);
    overflow: hidden;
  }
  tbody tr[class*='site-'] td {
    display: flex;
    justify-content: flex-start;
    gap: 4px;
    padding: 0;
    border-bottom: 0;
    background: transparent;
    text-align: left;
    white-space: normal;
    font-size: 13.5px;
    line-height: 1.3;
  }
  tbody tr[class*='site-'] td:first-child {
    border-left: 0;
    color: var(--site-link);
  }
  tbody tr[class*='site-'] td::before {
    content: attr(data-label);
    flex: 0 0 auto;
    color: var(--muted);
    text-align: left;
    font-size: 9.5px;
    font-weight: 800;
    letter-spacing: 0.05em;
    text-transform: uppercase;
  }
  tbody tr[class*='site-'] .seller-cell {
    grid-column: 1 / -1;
    align-items: center;
    gap: 0;
    padding-bottom: 0;
    color: var(--site-link);
    font-size: 15.5px;
    font-weight: 800;
  }
  tbody tr[class*='site-'] .seller-cell::before, tbody tr[class*='site-'] .product-cell::before {
    display: none;
  }
  tbody tr[class*='site-'] .product-cell {
    grid-column: 1 / -1;
    max-width: none;
    display: block;
    padding-bottom: 0;
    text-align: left;
    line-height: 1.35;
    font-size: 14px;
  }
  tbody tr[class*='site-'] .price-cell, tbody tr[class*='site-'] .target-cell, tbody tr[class*='site-'] .diff-cell, tbody tr[class*='site-'] .range-cell {
    min-height: 38px;
    border: 1px solid rgba(255, 255, 255, 0.04);
    border-radius: 10px;
    padding: 6px 8px;
    background: rgba(20, 22, 24, 0.3);
    flex-direction: column;
    justify-content: center;
    font-size: 14px;
  }
  tbody tr[class*='site-'] .price-cell, tbody tr[class*='site-'] .target-cell, tbody tr[class*='site-'] .diff-cell {
    min-width: 0;
  }
  tbody tr[class*='site-'] .range-cell {
    grid-column: 1 / -1;
    min-height: 34px;
    flex-direction: row;
    align-items: center;
    justify-content: flex-start;
    gap: 8px;
    white-space: nowrap;
  }
  tbody tr[class*='site-'] .range-cell::before {
    margin-right: 3px;
  }
  .product-cell span {
    -webkit-line-clamp: 2;
  }
  .empty-row td {
    padding: 12px;
    border: 1px solid var(--line);
    border-radius: 12px;
  }
  .note, .footer {
    font-size: 11.5px;
  }
}
}
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
    for key in ("test", "reset", "history", "settings"):
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
    telegram_summary = _collect_telegram_summary(options if isinstance(options, dict) else {})
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
    html = f"""<!doctype html>
<html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover"><meta name="theme-color" content="#111315"><meta name="apple-mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-title" content="Hermes">{web_app_head}<meta http-equiv="refresh" content="60"><title>Hermes</title><style>{DASHBOARD_CSS}</style></head><body class="public"><main><div class="hero"><div class="badge">Hermes</div><div class="actions public-actions"><a class="button secondary" href="{base_path}/settings">Ayarlar</a><a class="button secondary" href="{base_path}/link-test">Test</a><form class="inline-form" method="post" action="{base_path}/test-pushover"><button class="button test" type="submit">Pushover</button></form><form class="inline-form" method="post" action="{base_path}/reset-notifications" data-confirm="Bildirim susturma hafızası sıfırlanacak ve hedef altında kalan fırsatlar için tek seferlik kontrol başlatılacak. Devam etmek istiyor musun?"><button class="button secondary" type="submit">Bildirim Sıfırla</button></form><form class="inline-form" method="post" action="{base_path}/reset-price-history" data-confirm="Min/maks fiyat geçmişi temizlenecek ve güncel fiyattan yeniden başlayacak. Devam etmek istiyor musun?"><button class="button secondary" type="submit">Min/Maks Sıfırla</button></form></div>{public_cycle_row}{notice_html}{_render_table()}{telegram_recent_html}{error_card_html}</div></main>{confirm_script}</body></html>"""
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
