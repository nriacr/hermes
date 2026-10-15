# 2026-10-15 — Claude — U4 toplu kart işlemleri (4.8.0)

## Sorumlu ve yetki

- Agent: Claude (dal release-u4). İnsan talimatı: Nuri U4'ü onayladı, önizlemeyi gördü ve "yayına al" dedi (15 Ekim 2026). Sürükleyerek sıralama kapsam dışı.

## Amaç ve davranış

- Önce: her kart tek tek düzenlenirdi.
- Sonra: settings.bulk_toolbar + kart başlığında data-bulk-pick kutusu; settings.js seçili ve görünür kartlarda active/priority/group alanlarını doldurur ve input/change olayı tetikler. Sunucu tarafı kaydı değişmedi (build_watch aynı).
- Dosyalar: web/settings.py, static/settings.js, static/settings.css, tests/test_web.py, docs/previews/u4-toplu-islem.png, CHANGELOG, config sürümü.

## Test ve doğrulama

- sh tools/check.sh: ruff + 606 test OK. Canlı kanıt aşağıda.

## Veri, yedek ve geri alma

- Veri değişmez. Geri alma: 4.7.0 kaydıyla prepare_code_rollback.sh.

## Canlı kanıt

- install_rpi.sh (yedek b160df65): 4.8.0 started, sağlık ok, 13 takip/364 kayıt/644 fiyat noktası korundu, günlükte Traceback yok.
