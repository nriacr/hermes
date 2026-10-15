# 2026-10-15 — Claude — U8 desteklenen site listesi ve desteklenmeyen site uyarısı (4.10.0)

## Sorumlu ve yetki

- Agent: Claude (dal release-u8). İnsan talimatı: Nuri U8'i onayladı, önizlemeyi gördü ve "yayına al" dedi (15 Ekim 2026).

## Amaç ve davranış

- Önce: desteklenmeyen site linki kaydedilir, okuma sırasında günlükte "Desteklenmeyen takip linki atlandı" satırıyla sessizce atlanırdı.
- Sonra: web/settings.build_watch her linki detect_site_from_url ile denetler; desteklenmeyen host için ValueError (kart bağlamı + site + desteklenen liste). Ayar sayfasında desteklenen site satırı. Mevcut kartta desteklenmeyen link varsa o düzeltilmeden ayarlar kaydedilmez (bilinçli).
- Dosyalar: web/settings.py, tests/test_web.py, docs/previews/u8-desteklenen-siteler.png, CHANGELOG, config sürümü.

## Test ve doğrulama

- sh tools/check.sh: ruff + 609 test OK. Canlı kanıt aşağıda.

## Veri, yedek ve geri alma

- Veri değişmez. Geri alma: 4.9.0 kaydıyla prepare_code_rollback.sh.

## Canlı kanıt

- install_rpi.sh (yedek b160df65): 4.10.0 started, sağlık ok, 13 takip/364 kayıt/644 fiyat noktası korundu, günlükte Traceback ve "Desteklenmeyen takip linki" satırı yok (mevcut kartlarda sorunlu link yok).
