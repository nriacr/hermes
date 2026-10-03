#!/usr/bin/env sh
set -eu

PYTHON="${PYTHON:-.venv/bin/python}"
RUFF="${RUFF:-.venv/bin/ruff}"
PIP_CACHE_DIR="${PIP_CACHE_DIR:-/tmp/hermes-pip-cache}"
PYTHONPYCACHEPREFIX="${PYTHONPYCACHEPREFIX:-/tmp/hermes-pycache}"
RUFF_CACHE_DIR="${RUFF_CACHE_DIR:-/tmp/hermes-ruff-cache}"
export PIP_CACHE_DIR
export PYTHONPYCACHEPREFIX
export RUFF_CACHE_DIR

"$PYTHON" -m pip check
"$PYTHON" -m compileall -q ha-addon/app tests
"$RUFF" check ha-addon/app tests
"$PYTHON" -m unittest discover --start-directory tests --top-level-directory tests
