#!/bin/sh
set -eu

cd /app

python3 -m hermes.dashboard_with_settings &
python3 -m hermes.public_dashboard &
exec python3 /app/main.py
