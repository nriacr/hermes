"""Select/restore existing Pi backups without creating backups or exposing secrets."""

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import subprocess

APP = "769724e3_hermes"
REQUIRED_FOLDERS = {"share", "ssl", "media"}


def capture(args):
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode:
        raise ValueError("Yedek yönetimi tamamlanamadı; gizli bağlantı ayrıntıları gösterilmedi.")
    return result.stdout


def valid_backup(data):
    has_app = any(item.get("slug") == APP for item in data.get("addons", []))
    complete = data.get("type") == "full" or (
        data.get("homeassistant") and REQUIRED_FOLDERS.issubset(data.get("folders") or [])
    )
    return bool(has_app and complete)


def backup_info(helper, slug):
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", slug):
        raise ValueError("Geçersiz yedek kodu.")
    return json.loads(capture([helper, f"ha backups info {slug} --raw-json"]))["data"]


def newest_backup(helper):
    backups = json.loads(capture([helper, "ha backups list --raw-json"]))["data"]["backups"]
    for item in sorted(backups, key=lambda item: item["date"], reverse=True):
        if valid_backup(backup_info(helper, item["slug"])):
            return item["slug"]
    raise ValueError("Hermes'i içeren tam kapsamlı bir yedek bulunamadı; kurulum durduruldu.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backup")
    parser.add_argument("--restore", action="store_true")
    parser.add_argument("--version", action="store_true")
    args = parser.parse_args()
    workspace = Path(__file__).resolve().parents[2]
    helper = os.environ.get("HA_SSH_HELPER", str(workspace / "bin/ha-ssh"))
    slug = args.backup or newest_backup(helper)
    data = backup_info(helper, slug)
    if not valid_backup(data):
        raise ValueError("Yedek tam kapsamlı değil veya Hermes'i içermiyor.")
    if args.restore:
        password_arg = ""
        if data.get("protected"):
            config = json.loads(capture([str(workspace / "bin/ha-ws"), "rpc", "backup/config/info"]))
            password = config["config"]["create_backup"].get("password")
            if not password:
                raise ValueError("Şifreli yedeğin geri yükleme anahtarı bulunamadı.")
            password_arg = " --password " + shlex.quote(password)
        capture([helper, f"ha backups restore {slug} --app {APP} --homeassistant=false --no-progress{password_arg}"])
        print("Hermes geri yükleme işlemi tamamlandı.")
    elif args.version:
        print(next(item["version"] for item in data["addons"] if item["slug"] == APP))
    else:
        print(slug)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError):
        raise SystemExit("Yedek doğrulaması veya geri yükleme başarısız; kurulum/geri yükleme durduruldu.") from None
