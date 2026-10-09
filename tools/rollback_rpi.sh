#!/usr/bin/env sh
set -eu
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
SSH_HELPER="${HA_SSH_HELPER:-$SCRIPT_DIR/../../bin/ha-ssh}"
BACKUP="${1:?Kullanım: sh tools/rollback_rpi.sh YEDEK_KODU}"
case "$BACKUP" in *[!a-zA-Z0-9_-]*) printf 'Geçersiz yedek kodu.\n' >&2; exit 1;; esac
"$SSH_HELPER" "ha backups info '$BACKUP' --raw-json | jq -e '.data.type == \"full\" and any(.data.addons[]; .slug == \"769724e3_hermes\")' >/dev/null"
EXPECTED_VERSION="$("$SSH_HELPER" "ha backups info '$BACKUP' --raw-json | jq -er '.data.addons[] | select(.slug == \"769724e3_hermes\") | .version'")"
"$SSH_HELPER" "ha backups restore '$BACKUP' --app 769724e3_hermes --homeassistant=false --no-progress"
attempt=0
while [ "$attempt" -lt 36 ]; do
  if "$SSH_HELPER" "ha apps info 769724e3_hermes --raw-json | jq -e --arg expected '$EXPECTED_VERSION' '.data.version == \$expected and .data.state == \"started\"' >/dev/null" && "$SSH_HELPER" 'curl --fail --silent --max-time 10 http://192.168.1.143:8100/health'; then
    printf 'Geri yüklenen Hermes sürümü: %s\n' "$EXPECTED_VERSION"
    break
  fi
  attempt=$((attempt + 1))
  sleep 5
done
if [ "$attempt" -eq 36 ]; then
  printf 'Geri yükleme sonrası sağlık doğrulanamadı.\n' >&2
  exit 1
fi
printf 'Hermes yedeğine dönüldü; Home Assistant ve diğer uygulamalar geri alınmadı.\n'
