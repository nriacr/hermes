# 2026-10-17 — Claude — Hermes JSON yedek kopyası 5 dakikada bir

## Sorumlu ve yetki

- Nuri, SSD yazmasını azaltmak için JSON yedek kopyasını dakikada bir yerine 5 dakikada bir yazmayı onayladı (mesaj 14:36 UTC). Sürüm 4.19.2.

## Amaç ve davranış

- Önce: JSON kopyaları (state.json ~0,82 MB, fiyat özeti vb.) en çok 60 sn'de bir yazılıyordu (~0,8 MB/dk).
- Sonra: `storage.EXPORT_EVERY_SECONDS` 60 → 300; zamanlayıcı en çok 30 sn'de bir kontrol eder. Durdurma/çıkışta son hali yazılır. SQL asıl kayıt, bildirim ve fiyat geçmişi değişmedi. Beklenen kazanç ~0,65 MB/dk.
- Dosyalar: ha-addon/app/hermes/storage.py, ha-addon/CHANGELOG.md, config/constants sürümü.

## Test ve doğrulama

| Kontrol | Sonuç |
|---|---|
| Yerel test | `sh tools/check.sh`: ruff + 644 test geçti |
| Kurulum | `ha-exclusive sh tools/install_rpi.sh` 4.19.2 kuruldu (commit fd76e83); sonra başka thread 4.19.3 kurdu |
| Sağlık | günlükte hata/bozuk kayıt satırı 0, Hermes started |
| SSD yazma (sda, 10 dk, başka kurulum yokken) | 6,3 MB/dk (iki 5 dk: 6,27 ve 6,31), önceki 7,65 MB/dk. İlk ölçüm penceresi başka thread'in 4.19.3 kurulumuyla çakıştığı için geçersiz sayıldı |
| Yazma özeti | Hermes yeniden başlatmaları nedeniyle son 100 günlük satırında yoktu; ölçülmedi |

## Veri, yedek ve geri alma

- Kullanıcı verisi etkilenmez. Yedek: "Hermes 4.19.2 öncesi - 2026-10-17" slug 7a1a7d89 (tam, Hermes dahil); önceki e7a080d7. Geri alma: önceki sürüm kodu (4.19.1, sıkıştırma okuyabilir) `sh tools/prepare_code_rollback.sh 2393453`.
