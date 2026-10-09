#!/usr/bin/env sh
set -eu
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
SSH_HELPER="${HA_SSH_HELPER:-$SCRIPT_DIR/../../bin/ha-ssh}"
BACKUP="${1:?Kullanım: sh tools/rollback_rpi.sh YEDEK_KODU}"
case "$BACKUP" in *[!a-zA-Z0-9_-]*) printf 'Geçersiz yedek kodu.\n' >&2; exit 1;; esac
"$SSH_HELPER" "ha backups info '$BACKUP' --raw-json | jq -e '.data.type == \"full\" and any(.data.addons[]; .slug == \"769724e3_hermes\")' >/dev/null"
"$SSH_HELPER" "ha backups restore '$BACKUP' --app 769724e3_hermes --homeassistant=false --no-progress"
"$SSH_HELPER" 'ha apps info 769724e3_hermes --raw-json | jq ".data | {version,state}"'
printf 'Hermes yedeğine dönüldü; Home Assistant ve diğer uygulamalar geri alınmadı.\n'
