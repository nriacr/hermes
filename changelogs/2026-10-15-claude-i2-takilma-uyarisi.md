# 2026-10-15 — Claude — I2 site başına takılma uyarısı (4.2.8)

## Sorumlu ve yetki

- Agent: Claude, hermes-work. İnsan talimatı: Nuri optimizasyon listesindeki tüm maddeleri onayladı (I2).
- Sürüm: 4.2.7 → 4.2.8.

## Amaç ve davranış

- Önce: sağlık denetimi yalnız hiçbir istek/iş ilerlemesi olmayan okuyucuyu yakalıyordu (Amazon 3 saat); başarısız isteklerle dönen bir site sessiz kalabilirdi.
- Sonra: Diagnostics.last_success (ok/partial/stock/empty ile biten iş) izlenir; monitor/stall.py StallWatch bir site eşik süre (max(45 dk, 3×en uzun takip aralığı)) başarısız kalırsa bir kez uyarır: delivery.send (sessiz saat 22–08 dışında) + HA persistent_problem; kurtarınca kapatır. CAPTCHA/503 guard'ı olan site sessiz kalır. Eşik başlangıcı servis başlangıcıdır.
- Dosyalar: monitor/stall.py (yeni), monitor/runner.py (_check_stalls, 5 sn'lik tanı döngüsünde), diagnostics.py (last_success), tests/test_stall.py, CHANGELOG, sürüm.

## Test ve doğrulama

| Kontrol | Komut | Sonuç |
|---|---|---|
| Yerel test | sh tools/check.sh | ruff + 602 test OK |
| Canlı | kurulum sonrası eklenir | |

## Veri, yedek ve geri alma

- Kullanıcı verisi: dokunulmaz; yeni kalıcı veri yok (bellekte sayaçlar). Uyarı yalnız bildirim kuyruğuna ek mesaj yazar.
- Geri alma: sh tools/prepare_code_rollback.sh 1855091.

## Kalan durum

- Gerçek takılma canlıda tetiklenmedi; tetikleme birim testle doğrulandı.
