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
APP_SLUG="769724e3_hermes"
EXPECTED_VERSION="$(sed -n 's/^version: "\([^"]*\)"/\1/p' "$REPO_DIR/ha-addon/config.yaml" | head -n 1)"
BACKUP="${HERMES_BACKUP_SLUG:-}"
[ -x "$SSH_HELPER" ] && [ -n "$EXPECTED_VERSION" ]
# Home Assistant installs the code on GitHub main, so the tested tree must be exactly that code.
git -C "$REPO_DIR" fetch --quiet origin main
if [ -n "$(git -C "$REPO_DIR" status --porcelain)" ]; then
  printf 'Çalışma kopyasında kaydedilmemiş değişiklik var; kurulum durduruldu.\n' >&2
  exit 1
fi
LOCAL_COMMIT="$(git -C "$REPO_DIR" rev-parse HEAD)"
if [ "$LOCAL_COMMIT" != "$(git -C "$REPO_DIR" rev-parse origin/main)" ]; then
  printf 'Yerel kayıt GitHub main ile aynı değil (önce yayımla veya birleştir); kurulum durduruldu.\n' >&2
  exit 1
fi
printf 'Kurulacak kayıt: %s\n' "$LOCAL_COMMIT"
MAX_AGE="${HERMES_BACKUP_MAX_AGE_HOURS:-36}"
# Fail on missing management/backup access before tests or any live mutation.
HA_SSH_HELPER="$SSH_HELPER" "${PYTHON:-python3}" "$SCRIPT_DIR/check_access.py" --homeassistant-only
# Reuse Home Assistant's existing daily full backup; updates never create another backup.
(cd "$REPO_DIR" && sh tools/check.sh)
if [ -z "$BACKUP" ]; then
  BACKUP="$(HA_SSH_HELPER="$SSH_HELPER" "${PYTHON:-python3}" "$SCRIPT_DIR/backup_rpi.py" --max-age-hours "$MAX_AGE")"
fi
if [ -z "$BACKUP" ]; then
  printf 'Doğrulanabilir tam yedek bulunamadı; kurulum durduruldu.\n' >&2
  exit 1
fi
case "$BACKUP" in *[!a-zA-Z0-9_-]*) printf 'Geçersiz yedek kodu.\n' >&2; exit 1;; esac
HA_SSH_HELPER="$SSH_HELPER" "${PYTHON:-python3}" "$SCRIPT_DIR/backup_rpi.py" --backup "$BACKUP" --max-age-hours "$MAX_AGE" >/dev/null
printf 'Doğrulanan tam yedek: %s\n' "$BACKUP"
"$SSH_HELPER" 'ha core check --no-progress'
# Some unrelated store repositories can fail refresh; require Hermes's own exact version independently.
if ! "$SSH_HELPER" 'ha store reload'; then
  printf 'Mağaza yenilemesi hata bildirdi; Hermes sürümü ayrıca doğrulanıyor.\n'
fi
"$SSH_HELPER" "ha apps info $APP_SLUG --raw-json | jq -e --arg expected '$EXPECTED_VERSION' '.data.version_latest == \$expected' >/dev/null"
if "$SSH_HELPER" "ha apps info $APP_SLUG --raw-json | jq -e --arg expected '$EXPECTED_VERSION' '.data.version == \$expected and .data.state == \"started\"' >/dev/null"; then
  printf 'Beklenen sürüm zaten çalışıyor; canlı doğrulama yapılıyor.\n'
else
  "$SSH_HELPER" "ha apps update $APP_SLUG --no-progress"
fi
"$SSH_HELPER" "ha apps info $APP_SLUG --raw-json | jq -e --arg expected '$EXPECTED_VERSION' '.data.version == \$expected and .data.state == \"started\"' >/dev/null"
# Basic health is insufficient: require a completed new-version read, preserved history, and queue counts.
attempt=0
while [ "$attempt" -lt 36 ]; do
  if runtime_json="$(HA_SSH_HELPER="$SSH_HELPER" "${PYTHON:-python3}" "$SCRIPT_DIR/read_runtime.py")" && printf '%s' "$runtime_json" | jq -e --arg expected "$EXPECTED_VERSION" '.version == $expected and .healthy and .completed_jobs > 0' >/dev/null; then
    printf '%s\n' "$runtime_json" | jq '{version,healthy,active_watches,active_cards,state_entries,price_points,reads,jobs,completed_jobs,migration,outbox,open_incidents,delivery_seconds,last_site_read_age_seconds}'
    break
  fi
  attempt=$((attempt + 1))
  sleep 5
done
if [ "$attempt" -eq 36 ]; then
  printf 'Canlı ürün okuması doğrulanamadı. Geri dönüş: sh tools/rollback_rpi.sh %s\n' "$BACKUP" >&2
  exit 1
fi
"$SSH_HELPER" 'curl --fail --silent --max-time 15 http://192.168.1.143:8100/health'
printf 'Hermes %s kuruldu. Geri dönüş: sh tools/rollback_rpi.sh %s\n' "$EXPECTED_VERSION" "$BACKUP"
