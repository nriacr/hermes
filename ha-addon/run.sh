#!/bin/sh
set -eu

cd /app

python3 -m hermes.dashboard &
exec python3 /app/main.py
