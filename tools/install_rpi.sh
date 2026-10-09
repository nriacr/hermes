#!/usr/bin/env sh
set -eu
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
REPO_DIR="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
HA_REPO_DIR="$(CDPATH= cd -- "$REPO_DIR/.." && pwd)"
SSH_HELPER="${HA_SSH_HELPER:-$HA_REPO_DIR/bin/ha-ssh}"
APP_SLUG="769724e3_hermes"
EXPECTED_VERSION="$(sed -n 's/^version: "\([^"]*\)"/\1/p' "$REPO_DIR/ha-addon/config.yaml" | head -n 1)"
BACKUP="${HERMES_BACKUP_SLUG:-}"
[ -x "$SSH_HELPER" ] && [ -n "$EXPECTED_VERSION" ]
# Always check before mutating the live system. Supplying a backup only reuses a verified full backup.
(cd "$REPO_DIR" && sh tools/check.sh)
if [ -z "$BACKUP" ]; then
  BACKUP="$("$SSH_HELPER" "ha backups new --name 'Hermes $EXPECTED_VERSION öncesi tam yedek' --no-progress --raw-json | jq -er '.data.slug'")"
fi
case "$BACKUP" in *[!a-zA-Z0-9_-]*) printf 'Geçersiz yedek kodu.\n' >&2; exit 1;; esac
"$SSH_HELPER" "ha backups info '$BACKUP' --raw-json | jq -e '.data.type == \"full\" and any(.data.addons[]; .slug == \"$APP_SLUG\")' >/dev/null"
printf 'Doğrulanan tam yedek: %s\n' "$BACKUP"
"$SSH_HELPER" 'ha core check --no-progress'
# Some unrelated store repositories can fail refresh; require Hermes's own exact version independently.
if ! "$SSH_HELPER" 'ha store reload'; then
  printf 'Mağaza yenilemesi hata bildirdi; Hermes sürümü ayrıca doğrulanıyor.\n'
fi
"$SSH_HELPER" "ha apps info $APP_SLUG --raw-json | jq -e --arg expected '$EXPECTED_VERSION' '.data.version_latest == \$expected' >/dev/null"
"$SSH_HELPER" "ha apps update $APP_SLUG --no-progress"
"$SSH_HELPER" "ha apps info $APP_SLUG --raw-json | jq -e --arg expected '$EXPECTED_VERSION' '.data.version == \$expected and .data.state == \"started\"' >/dev/null"
# Basic health is insufficient: require a completed new-version read, preserved history, and queue counts.
RUNTIME_COMMAND="$(cat <<'REMOTE'
ha apps info 769724e3_hermes --raw-json | jq -r '.data.options | "url = " + (("http://192.168.1.143:8100/public/" + .public_dashboard_token + "/runtime") | @json)' | curl --fail --silent --max-time 25 --config -
REMOTE
)"
attempt=0
while [ "$attempt" -lt 36 ]; do
  if runtime_json="$("$SSH_HELPER" "$RUNTIME_COMMAND")" && printf '%s' "$runtime_json" | jq -e --arg expected "$EXPECTED_VERSION" '.version == $expected and .healthy and .completed_jobs > 0' >/dev/null; then
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
