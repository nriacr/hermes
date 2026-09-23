#!/usr/bin/env sh
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
REPO_DIR="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
HA_REPO_DIR="$(CDPATH= cd -- "$REPO_DIR/.." && pwd)"
SSH_HELPER="${HA_SSH_HELPER:-$HA_REPO_DIR/bin/ha-ssh}"
APP_SLUG="769724e3_hermes"
EXPECTED_VERSION="$(sed -n 's/^version: "\([^"]*\)"/\1/p' "$REPO_DIR/ha-addon/config.yaml" | head -n 1)"

if [ ! -x "$SSH_HELPER" ]; then
  printf 'Home Assistant SSH helper not found or not executable: %s\n' "$SSH_HELPER" >&2
  exit 1
fi
if [ -z "$EXPECTED_VERSION" ]; then
  printf 'Could not read the Hermes add-on version from ha-addon/config.yaml.\n' >&2
  exit 1
fi

"$SSH_HELPER" 'ha store reload'
if ! "$SSH_HELPER" "ha apps update $APP_SLUG"; then
  printf 'Update command did not report a new version; checking the installed version.\n'
fi
if ! "$SSH_HELPER" "ha apps info $APP_SLUG --raw-json | jq -e --arg expected '$EXPECTED_VERSION' '.data.version == \$expected and .data.version_latest == \$expected and .data.state == \"started\"' >/dev/null"; then
  printf 'Hermes is not running at the expected latest version %s.\n' "$EXPECTED_VERSION" >&2
  exit 1
fi

printf 'Hermes %s is installed and running on Home Assistant.\n' "$EXPECTED_VERSION"
