#!/usr/bin/env python3
"""Read-only release access checks. Credentials and raw responses never leave memory."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.error
import urllib.request

REPO = Path(__file__).resolve().parents[1]
GITHUB_REPOSITORY = "nriacr/hermes"
APP_SLUG = "769724e3_hermes"


class AccessError(Exception):
    pass


def capture(command, *, input_text=None):
    try:
        result = subprocess.run(command, input=input_text, capture_output=True, text=True,
                                timeout=60, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    except (OSError, subprocess.TimeoutExpired):
        raise AccessError("Erişim aracı çalıştırılamadı veya bağlantı zaman aşımına uğradı.") from None
    if result.returncode:
        # stderr can include a credential-bearing URL; never echo it.
        raise AccessError("Erişim aracı başarısız oldu. Bağlantı ve hesap girişini kontrol et.")
    return result.stdout


def github_get(path, token):
    request = urllib.request.Request("https://api.github.com" + path, headers={
        "Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
        "User-Agent": "Hermes-release-access-check", "X-GitHub-Api-Version": "2022-11-28"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.load(response), response.headers
    except urllib.error.HTTPError as exc:
        raise AccessError(f"GitHub erişimi reddedildi (HTTP {exc.code}). Hesap ve anahtar yetkisini kontrol et.") from None
    except (OSError, ValueError):
        raise AccessError("GitHub bağlantısı veya yanıtı doğrulanamadı.") from None


def validate_github(identity, repository, actions, scopes, require_workflow):
    if identity.get("login") != GITHUB_REPOSITORY.split("/")[0]:
        raise AccessError("GitHub için beklenen nriacr hesabı kullanılmıyor.")
    if repository.get("archived") or not repository.get("permissions", {}).get("push"):
        raise AccessError("Hermes deposunda kod yayımlama yetkisi yok veya depo arşivlenmiş.")
    if not actions.get("enabled"):
        raise AccessError("Hermes deposunda otomatik kontroller kapalı.")
    if require_workflow and "workflow" not in scopes:
        raise AccessError("Otomatik kontrol dosyaları değişiyor; workflow izni doğrulanamadı. "
                          "Mevcut GitHub anahtarının workflow iznini hesap ayarlarında kontrol et. "
                          "Sınırlı depo anahtarında Contents ve Workflows yazma izinleri ayrıca doğrulanmalıdır.")


def check_github(require_workflow=False):
    raw = capture(["git", "credential", "fill"], input_text="protocol=https\nhost=github.com\n\n")
    credential = dict(line.split("=", 1) for line in raw.splitlines() if "=" in line)
    token = credential.get("password")
    if not token:
        raise AccessError("GitHub erişim anahtarı güvenli kimlik deposunda bulunamadı.")
    identity, headers = github_get("/user", token)
    repository, _ = github_get("/repos/" + GITHUB_REPOSITORY, token)
    actions, _ = github_get("/repos/" + GITHUB_REPOSITORY + "/actions/permissions", token)
    github_get("/repos/" + GITHUB_REPOSITORY + "/actions/workflows", token)
    scopes = {scope.strip() for scope in headers.get("X-OAuth-Scopes", "").split(",")}
    validate_github(identity, repository, actions, scopes, require_workflow)
    print("GitHub: doğru hesap, kod yayımlama ve otomatik kontrol erişimi doğrulandı.")
    print("Workflow düzenleme: " + ("yetkili." if "workflow" in scopes else "bu kontrolde doğrulanmadı."))


def remote_json(helper, command):
    try:
        payload = json.loads(capture([str(helper), command]))
        if payload.get("result") != "ok" or not isinstance(payload.get("data"), dict):
            raise ValueError
        return payload["data"]
    except ValueError:
        raise AccessError("Home Assistant yönetim yanıtı doğrulanamadı.") from None


def check_homeassistant():
    helper = Path(os.environ.get("HA_SSH_HELPER", REPO.parent / "bin" / "ha-ssh"))
    if not helper.is_file() or not os.access(helper, os.X_OK):
        raise AccessError("Home Assistant kurulum bağlantısı bulunamadı.")
    supervisor = remote_json(helper, "ha supervisor info --raw-json")
    if not supervisor.get("healthy"):
        raise AccessError("Home Assistant yönetimi sağlıklı değil; kurulum başlatılmadı.")
    app = remote_json(helper, f"ha apps info {APP_SLUG} --raw-json")
    if app.get("slug") != APP_SLUG:
        raise AccessError("Kurulum hedefi Hermes olarak doğrulanamadı.")
    backups = remote_json(helper, "ha backups list --raw-json")
    if not isinstance(backups.get("backups"), list):
        raise AccessError("Yedek listesine erişim doğrulanamadı.")
    print("Home Assistant: yönetim, Hermes kurulum bilgisi ve yedek erişimi doğrulandı.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--github-only", action="store_true")
    scope.add_argument("--homeassistant-only", action="store_true")
    parser.add_argument("--require-workflow", action="store_true")
    args = parser.parse_args()
    try:
        if not args.homeassistant_only:
            check_github(args.require_workflow)
        if not args.github_only:
            check_homeassistant()
    except AccessError as exc:
        print(f"YETKİ KONTROLÜ: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
