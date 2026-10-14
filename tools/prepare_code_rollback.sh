#!/usr/bin/env sh
# Code-only rollback: publish the add-on code of an earlier release as a NEW patch version.
# Hermes data (/data, price history, notification memory) is never touched. Nothing is pushed or installed here.
set -eu
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
REPO_DIR="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
COMMON_DIR="$(git -C "$REPO_DIR" rev-parse --path-format=absolute --git-common-dir)"
HA_REPO_DIR="$(CDPATH= cd -- "$COMMON_DIR/../.." && pwd)"
if [ -z "${HA_DEPLOY_LOCK_PID:-}" ]; then
  exec "$HA_REPO_DIR/bin/ha-exclusive" sh "$0" "$@"
fi
GOOD="${1:?Kullanım: sh tools/prepare_code_rollback.sh ESKİ_COMMIT}"
git -C "$REPO_DIR" fetch --quiet origin main
[ -z "$(git -C "$REPO_DIR" status --porcelain)" ] || { printf 'Kaydedilmemiş değişiklik var; durduruldu.\n' >&2; exit 1; }
[ "$(git -C "$REPO_DIR" rev-parse HEAD)" = "$(git -C "$REPO_DIR" rev-parse origin/main)" ] || { printf 'Yerel kayıt origin/main ile aynı değil; durduruldu.\n' >&2; exit 1; }
git -C "$REPO_DIR" merge-base --is-ancestor "$GOOD" HEAD || { printf 'Verilen commit main geçmişinde değil; durduruldu.\n' >&2; exit 1; }
GOOD_VERSION="$(git -C "$REPO_DIR" show "$GOOD:ha-addon/config.yaml" | sed -n 's/^version: "\([^"]*\)"/\1/p' | head -n 1)"
[ -n "$GOOD_VERSION" ] || { printf 'Eski sürüm okunamadı; durduruldu.\n' >&2; exit 1; }
# Only the add-on code returns; tools, tests, docs and changelogs of the current main stay.
git -C "$REPO_DIR" checkout "$GOOD" -- ha-addon/app ha-addon/config.yaml ha-addon/Dockerfile ha-addon/run.sh ha-addon/.dockerignore
NEW_VERSION="$("$HA_REPO_DIR/bin/ha-version" --repo "$REPO_DIR" --change patch)"
GOOD_VERSION="$GOOD_VERSION" NEW_VERSION="$NEW_VERSION" GOOD="$GOOD" REPO_DIR="$REPO_DIR" "${PYTHON:-python3}" - <<'PY'
import os
from pathlib import Path
path = Path(os.environ["REPO_DIR"]) / "ha-addon/CHANGELOG.md"
text = path.read_text(encoding="utf-8")
entry = (f"## {os.environ['NEW_VERSION']}\n\n"
         f"- Geri dönüş: eklenti kodu {os.environ['GOOD_VERSION']} sürümündeki (kayıt {os.environ['GOOD'][:12]}) haline döndürüldü.\n"
         "- Fiyat geçmişi, takipler ve bildirim hafızası dahil Hermes verisine dokunulmadı; yedek geri yüklemesi yapılmadı.\n\n")
head = "# Değişiklik günlüğü\n\n"
path.write_text(head + entry + text[len(head):] if text.startswith(head) else entry + text, encoding="utf-8")
PY
(cd "$REPO_DIR" && sh tools/check.sh)
git -C "$REPO_DIR" add ha-addon
git -C "$REPO_DIR" commit -q -m "Roll the add-on code back to $GOOD_VERSION as $NEW_VERSION (data untouched)"
printf 'Hazır: %s sürümü, %s kodunu taşıyor. Sıradaki adımlar:\n  git push origin HEAD:main\n  sh tools/install_rpi.sh\nChangelogs klasörüne ayrıntılı kayıt yazmayı unutma.\n' "$NEW_VERSION" "$GOOD_VERSION"
