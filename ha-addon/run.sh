#!/bin/sh
set -eu

cd /app
# One process serves the monitor, the ingress (8099) and public (8100) panels
# and Telegram; it handles SIGTERM itself to close Chromium cleanly.
exec /opt/venv/bin/python -u -m hermes
