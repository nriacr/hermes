#!/bin/sh
set -eu

cd /app

/opt/venv/bin/python -m hermes.dashboard_with_settings &
/opt/venv/bin/python -m hermes.public_dashboard &
exec /opt/venv/bin/python -u /app/main.py
