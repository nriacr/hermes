#!/bin/sh
set -eu

cd /app

python3 -m hermes.dashboard &
python3 -m hermes.public_dashboard &
exec python3 /app/main.py
