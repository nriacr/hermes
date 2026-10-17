# 2026-10-17 — Claude — Hermes JSON yedek kopyası 5 dakikada bir

## Sorumlu ve yetki

- Nuri, SSD yazmasını azaltmak için JSON yedek kopyasını dakikada bir yerine 5 dakikada bir yazmayı onayladı (mesaj 14:36 UTC). Sürüm 4.19.2.

## Amaç ve davranış

- Önce: JSON kopyaları (state.json ~0,82 MB, fiyat özeti vb.) en çok 60 sn'de bir yazılıyordu (~0,8 MB/dk).
- Sonra: `storage.EXPORT_EVERY_SECONDS` 60 → 300; zamanlayıcı en çok 30 sn'de bir kontrol eder. Durdurma/çıkışta son hali yazılır. SQL asıl kayıt, bildirim ve fiyat geçmişi değişmedi. Beklenen kazanç ~0,65 MB/dk.
- Dosyalar: ha-addon/app/hermes/storage.py, ha-addon/CHANGELOG.md, config/constants sürümü.

## Test ve doğrulama

Yerel test ve canlı sonuç aşağıda kurulumdan sonra eklenir (yapılmadan başarılı yazılmaz).

## Veri, yedek ve geri alma

- Kullanıcı verisi etkilenmez. Yedek: HA tam yedeği e7a080d7 (13:26 UTC) en yeni tam yedek, Hermes dahil. Geri alma: önceki sürüm kodu (4.19.1, sıkıştırma okuyabilir) `sh tools/prepare_code_rollback.sh 2393453`.
