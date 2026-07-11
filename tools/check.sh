#!/usr/bin/env sh
set -eu

PYTHON="${PYTHON:-.venv/bin/python}"
RUFF="${RUFF:-.venv/bin/ruff}"
PIP_CACHE_DIR="${PIP_CACHE_DIR:-/tmp/hermes-pip-cache}"
export PIP_CACHE_DIR

"$PYTHON" -m pip check
"$PYTHON" -m py_compile ha-addon/app/hermes/*.py ha-addon/app/hermes/providers/*.py tools/hermes_smoke_test.py
"$RUFF" check ha-addon/app tools/hermes_smoke_test.py
"$PYTHON" tools/hermes_smoke_test.py
