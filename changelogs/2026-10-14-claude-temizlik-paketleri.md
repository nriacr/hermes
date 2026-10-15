# 2026-10-14 — Claude — Genel temizlik incelemesi paketleri (A, B, C, D-kısmi)

## Sorumlu ve yetki

- Agent: Claude (bulut çalışma kopyası; gönderme/kurulum Mac üzerinden)
- İnsan talimatı: Nuri 14 Ekim 2026 20:30Z "Hermes kodlarını detaylı temizlik amaçlı tara", 20:39Z "hepsini önerdiğin sırayla yapmaya başla". Rapor: /mnt/project-files/hermes/hermes-temizlik-incelemesi-2026-10-14.md
- Başlangıç: origin/main e3d03b4 (4.2.2). Sonuç commit/sürüm: Mac oturumu doldurur (aşağıdaki "Yayın").

## Amaç ve davranış

Paket A (araç, sürüm artmaz)
- tools/install_rpi.sh: kirli çalışma ağacı veya HEAD != origin/main ise kurulum başlamadan durur (HA kodu GitHub main'den kurar); kurulacak commit yazdırılır. Yedek 36 saatten (HERMES_BACKUP_MAX_AGE_HOURS) eskiyse durur.
- tools/backup_rpi.py: --max-age-hours (kurulumda), --date. Yedek yaşı testleri tests/test_backup_rpi.py.

Paket B (araç, sürüm artmaz)
- tools/rollback_rpi.sh veri geri yüklemesidir; artık HERMES_CONFIRM_DATA_RESTORE=evet olmadan çalışmaz ve hangi tarihe döneceğini yazar.
- tools/prepare_code_rollback.sh (yeni): eski commit'in eklenti kodunu yeni yama sürümü olarak hazırlar (ha-version, changelog, check.sh, commit). Veriye dokunmaz. Gerçek ha-exclusive/ha-version kopyalarıyla bir sim. depoda denendi: 4.2.2 → 4.2.3, testler geçti.
- AGENTS.md bu akışı anlatıyor.

Paket C (runtime, yama sürümü)
- requests 2.32.3 → 2.34.2 (pip-audit: bilinen açık kalmadı). Amazon taşıyıcıları (curl_cffi, selenium) değişmedi.
- Kullanılmayan kod silindi: telegram_error_count_24h, LIVE_REFRESH_NOTE, has_secondary_offer_text, has_verified_warehouse_evidence, short_amazon_url, _optional_bounded_integer, last_cycle_finished_at, docs/ui-demo.html. Davranış değişmedi.
- utils.detect_site_from_url yalnız alan adına (hostname) bakıyor; kullanıcı bilgisi/port/sorgu içindeki site adı artık eşleşmiyor (test eklendi).

Paket D, kısmi (runtime)
- Public yüzey X-Frame-Options: DENY ve Content-Security-Policy: frame-ancestors 'none' gönderiyor; ingress aynı kaldı (HA içinde gömülü çalışır). Test eklendi.
- Bilerek yapılmadı: PIN kilidinin istemci başına olması (tünel arkasında tüm istemciler aynı adres görünür, ayrı tartışma), options.json önbelleği, imaj/kullanıcı sertleştirme.

## Test ve doğrulama

| Kontrol | Komut/yöntem ve kapsam | Sonuç |
|---|---|---|
| Yerel test | sh tools/check.sh (bulut, Python 3.13): pip check, derleme, ruff, 566 test | Geçti |
| Bağımlılık | pip-audit -r ha-addon/app/requirements.txt | Açık yok |
| Kod geri dönüşü | sim. HA klasörü + gerçek deploy_lock.py/release_version.py ile prepare_code_rollback.sh | 4.2.3 hazırlandı, testler geçti |
| Canlı davranış | Kurulum sonrası okuma, aşağıdaki "Yayın ve kurulum" | Geçti |

## Yayın ve kurulum

- Yayın: b6e43e7 (araçlar, AGENTS.md), 6420e45 + 3fac96e (runtime, docs/ui-demo.html silindi), 9313461 (4.2.3 sürümü ve CHANGELOG). Mac'te check.sh: 566 test geçti. origin/main = 9313461.
- Kurulum: 2026-10-15 tools/install_rpi.sh, önkontroller geçti (temiz ağaç, HEAD == origin/main). Kurulum için .claude/ ve .cursor/permissions.json geçici olarak ~/hermes-gecici altına taşındı, sonra geri konacak.
- Yedek: b160df65 (Automatic backup 2026.10.1, 2026-10-15T01:49Z), geri dönüş: sh tools/rollback_rpi.sh b160df65 (yalnız veri felaketinde).
- Canlı kanıt: sürüm 4.2.3, started, healthy true; 14 aktif takip/kart; 364 durum kaydı; 635 fiyat noktası (kurulum öncesi geçmiş 634, düşüş yok); gönderim kuyruğu sent 102, failed 1 (eskiden beri); amazon/hepsiburada/togg son okuma <1 dk.
- Public panel (8100): GET yanıtında X-Frame-Options: DENY ve Content-Security-Policy: frame-ancestors 'none'. Sunucu HEAD'i desteklemediği için curl -I yerine GET kullanıldı. Ingress (8099) yolu dışarıdan açık değil, bu başlık orada beklenmiyor.
- Ölçülmedi: "33 ürün gösteriliyor, 6 stokta yok" tablo sayıları bu doğrulamada okunmadı.
- Amazon ağ sayaçları sıfırlanmadı.

## Veri, yedek ve geri alma

- Kullanıcı verisine dokunulmadı; Amazon sayaçları ve fiyat geçmişi korunuyor.
- Geri alma: sh tools/prepare_code_rollback.sh <4.2.2 commit'i e3d03b4>. Veri geri yükleme yalnız Nuri'nin açık onayıyla.

## Kalan durum

- 2026-10-15 (Nuri onayıyla): install_rpi.sh ve prepare_code_rollback.sh önkontrolü `status --porcelain --untracked-files=no` kullanıyor; kayıtsız agent dosyaları (.claude, .cursor) kurulumu durdurmaz, kayıtlı dosya değişikliği yine durdurur. Sürüm artmadı, kurulum yapılmadı.
- Paket D'nin kalanı (PIN kilidi, açık HTTP uyarısı, root olmayan konteyner) ayrı konuşma ister.
- İnsan adımları: ajan başına SSH anahtarı, sınırlı GitHub anahtarı, .claude izinleri, iCloud yedek kontrolü, eski çalışma klasörlerinin silinmesi.
