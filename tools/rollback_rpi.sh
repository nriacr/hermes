#!/usr/bin/env sh
set -eu
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
REPO_DIR="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
COMMON_DIR="$(git -C "$REPO_DIR" rev-parse --path-format=absolute --git-common-dir)"
HA_REPO_DIR="$(CDPATH= cd -- "$COMMON_DIR/../.." && pwd)"
if [ -z "${HA_DEPLOY_LOCK_PID:-}" ]; then
  exec "$HA_REPO_DIR/bin/ha-exclusive" sh "$0" "$@"
fi
"$HA_REPO_DIR/bin/ha-exclusive" --check
SSH_HELPER="${HA_SSH_HELPER:-$HA_REPO_DIR/bin/ha-ssh}"
BACKUP="${1:?Kullanım: sh tools/rollback_rpi.sh YEDEK_KODU}"
case "$BACKUP" in *[!a-zA-Z0-9_-]*) printf 'Geçersiz yedek kodu.\n' >&2; exit 1;; esac
# This restores Hermes's /data too: price history, notification memory and settings return to the
# backup moment. It is the data-disaster path, never the normal way back from a bad release
# (use tools/prepare_code_rollback.sh for that). It needs the owner's explicit confirmation.
BACKUP_DATE="$(HA_SSH_HELPER="$SSH_HELPER" "${PYTHON:-python3}" "$SCRIPT_DIR/backup_rpi.py" --backup "$BACKUP" --date)"
if [ "${HERMES_CONFIRM_DATA_RESTORE:-}" != "evet" ]; then
  printf 'DURDURULDU: bu işlem Hermes verisini (fiyat geçmişi, bildirim hafızası, ayarlar) %s anına döndürür;\n' "$BACKUP_DATE" >&2
  printf 'o tarihten sonraki veri kaybolur. Sadece kod geri alınacaksa: sh tools/prepare_code_rollback.sh <eski-commit>\n' >&2
  printf 'Veri geri yüklemesine Nuri açıkça onay verdiyse: HERMES_CONFIRM_DATA_RESTORE=evet sh tools/rollback_rpi.sh %s\n' "$BACKUP" >&2
  exit 1
fi
EXPECTED_VERSION="$(HA_SSH_HELPER="$SSH_HELPER" "${PYTHON:-python3}" "$SCRIPT_DIR/backup_rpi.py" --backup "$BACKUP" --version)"
HA_SSH_HELPER="$SSH_HELPER" "${PYTHON:-python3}" "$SCRIPT_DIR/backup_rpi.py" --backup "$BACKUP" --restore
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
