# 2026-10-15 — Claude — İkinci tarama paketleri E, F, G

## Sorumlu ve yetki

- Agent ve konu/çalışma kopyası: Claude, `hermes-work` (main), konu "ikinci tarama".
- İnsan talimatı ve kapsamı: Nuri 15 Ekim 2026'da `hermes-ikinci-tarama-2026-10-15.md` raporundaki E, F ve G paketlerinin hepsini "hepsini uygula" diyerek istedi. Veri geri yükleme, eski klasör silme, SSH/GitHub anahtar ayrımı, iCloud ve .claude izinleri kapsam dışı.
- Başlangıç: `b09b365` (canlı 4.2.4, `started`). Sürüm 4.2.5 `bin/ha-version` ile ayrıldı. E+G tek sürüm; F sürüm artırmaz ayrı commit.

## Amaç ve davranış

### Paket E (kod, 4.2.5)

- Sorun: eskimiş ayar sayfası, liste başka yerden değiştikten sonra yanlış takibi silebiliyor/ezebiliyordu (konum indeksi kimliksiz kullanılıyordu). İki ayar kaydı birbirini ezebiliyordu. Forged `watches_count` sınırsızdı.
- Sonra: `apply_settings_operation` formdaki takip kimliğini (`watches_<i>_id` / `watch_id`) saklı listeyle karşılaştırır; uyuşmazsa "Ayar sayfası eskimiş…" ile reddeder. Tam sayfa kaydında saklı listedeki bir takip formda yoksa da reddedilir (sessiz silme olmasın). Kimlik göndermeyen eski/API formları eskisi gibi çalışır. `watches_count` 0..500.
- Ayar kaydı ve Telegram hızlı ekleme ortak `OPTIONS_LOCK` altında (oku-değiştir-kaydet tek adım).
- `web/access.py verify()`: PBKDF2 kilit dışında; kilit/başarısız deneme sayacı kaydı kilit altında ve tekrar kontrolle yapılır (kilit devreye girdikten sonra tamamlanan doğru tahmin de reddedilir). PIN değiştiyse sonuç geçersiz.
- `web/server.py public_token_allowed`: seçenekler 5 sn önbellekte; ayar kaydında anında yenilenir.
- `telegram/listener.py _resolve_shared_url`: yönlendirmeler elle (en çok 5), her adımda çözülen tüm adresler genel olmalı; özel/yerel/link-local/loopback reddedilir.
- Ölü kod: `/settings/restart.js` çift yolu, `TelegramConfig.session_name` (seçenek ve ayar alanı kaldı), `tests/support.py` içinde `amazon_product`, `price_html`, `search_card`. `amazon/client.py` içindeki `handle_*` metodları `HTMLParser` geçersiz kılmalarıdır, ölü kod değildir; dokunulmadı. ruff: try-except-pass → `contextlib.suppress`, `zip(strict=False)`, `raise … from None`.
- Panel görünümü değişmedi.

### Paket G (4.2.5 içinde)

- `config.yaml`: `hassio_role: manager` kaldırıldı; `supervisor.py` `/addons/self/options` ve `/addons/self/restart` kullanır (slug çözümleme kaldırıldı). AGENTS.md uyumluluk notu güncellendi. Canlıda çalışmazsa 4.2.4'teki manager + slug yoluna dönülecek.
- `database.py`: açılışta `PRAGMA quick_check(5)`; bozuksa dosya silinmez/yeniden yazılmaz, log'a "VERİTABANI SAĞLIKSIZ" yazılır, çalışma sürer. Saatte bir `wal_checkpoint(TRUNCATE)` (çevrim kaydından tetiklenir).
- T4 ölçümü: `/runtime` çıktısına `storage_bytes` (state_snapshot, database, wal, shm) ve `database_integrity` eklendi; canlı değerler aşağıda.

### Paket F (araç/CI, sürüm artmaz)

Commit `5fc03e3` (runtime kodu değişmedi, sürüm artmadı):

1. `tools/prepare_code_rollback.sh`: eski sürümde bulunmayan yeni kod dosyaları da `git rm` ile kaldırılır (önceden yeni dosyalar eski kodun yanında kalıyordu); her hata adımı net mesajla durur ve `git reset --hard <başlangıç commit>` talimatı verir, "canlıya hiçbir şey kurulmadı" yazar; `git add -A ha-addon`. Gerçek bir çalışma kopyasında değil, geçici bir depoda (sahte `bin/ha-version`, sahte `check.sh`, yerel bare origin) `tests/test_rollback_tool.py` ile denendi: yeni dosya kaldırılıyor, araçlar kalıyor, başarısız test işlemi commit atmadan duruyor, main dışı commit reddediliyor. Gerçek sürüm numarası ayrılmadı.
2. `.github/workflows/quality.yml`: `pip-audit==2.9.0` adımı (`ha-addon/app/requirements.txt`, yerelde çalıştırıldı: "No known vulnerabilities found"). `.github/dependabot.yml`: pip/docker/github-actions için `open-pull-requests-limit: 0` (yalnız güvenlik güncellemesi PR'ı).
3. `.gitignore`: `.claude/`, `.cursor/permissions.json`.
4. Mac `bin/ha-ssh` (Home Assistant deposu): kilit regex'ine `ha core|host|os|supervisor` (restart, update, stop, reboot, shutdown, rebuild) ve `ha backups restore|remove` eklendi; zsh ile 11 komut örneği denendi (yıkıcılar kilitli, info/check/list serbest). `docs/YAYIN_AKISI.md` güncellendi.
5. `tools/backup_rpi.py`: şifreli yedek anahtarı artık komut satırına değil stdin'e yazılır (`read -r HA_BACKUP_KEY && ha backups restore … --password "$HA_BACKUP_KEY"`); Mac süreç listesinde ve ssh komut metninde görünmez. Pi'de komut `ha` sürecinin argümanında bir an görünür kalır (HA CLI başka yol sunmuyor). stdin aktarımı canlı SSH'ta zararsız bir `read` ile denendi; gerçek geri yükleme denenmedi (veri geri yükleme yetkisi yok), birim testi güncellendi.

## Değişen dosyalar

ha-addon/config.yaml, ha-addon/CHANGELOG.md, ha-addon/app/hermes/{config,constants,database,diagnostics,history,models,supervisor,storage}.py, monitor/runner.py, providers/{registry.py,hepsiburada/__init__.py}, telegram/listener.py, web/{access,dashboard,pricechart,server,settings}.py, AGENTS.md, tests/{support,test_app,test_web}.py, tests/test_database_health.py (yeni).

## Test ve doğrulama

| Kontrol | Komut/yöntem ve kapsam | Sonuç / kanıt dosyası |
|---|---|---|
| Yerel test | `sh tools/check.sh` (pip check, compileall, ruff, 583 unittest) | Geçti (E+G taslağı) |
| Yeni testler | eskimiş sayfa silme/güncelleme/tam kayıt, sayı sınırı, Telegram özel IP reddi (5 adres türü + yönlendirme), veritabanı bozuk dosya + WAL küçültme | Geçti |
| Canlı davranış | aşağıdaki "Yayın ve kurulum" bölümünde | — |

## Yayın ve kurulum

(Kurulumdan sonra doldurulur.)

## Veri, yedek ve geri alma

- Kullanıcı verisine etkisi: yok; DB şeması değişmedi, takip/fiyat/bildirim verisi okunmadan/silinmeden korunur.
- Geri alma (kod): `sh tools/prepare_code_rollback.sh b09b365` (yeni yama sürümü olarak 4.2.4 kodu), ardından push ve `sh tools/install_rpi.sh`.
- Veri geri yükleme ayrı açık insan yetkisi gerektirir; yapılmadı.

## Kalan durum

(Kurulum sonrası doldurulur.)
