# 2026-10-16 — Claude — Depo ve Yeni fiyat için ayrı çizgi (4.13.0)

## Sorumlu ve yetki

- Agent: Claude (dal preview-depo-cizgi). İnsan talimatı: Nuri isteği verdi, önizleme ekran görüntüsünü gördü ve "al" dedi (15 Ekim 2026).

## Amaç ve davranış

- Önce: ayrıntı grafiği yalnız açılan kartın çizgisini gösteriyordu; Depo ve Yeni ayrı kartlardı.
- Sonra: dashboard._pair_partners aynı product_url + tracking_id'li zıt koşullu teklifi eşler; pricechart.detail_chart/ranged_chart ikinci çizgiyi (Yeni #8ad8ff, Depo #ffb347) ve açıklamayı çizer, aralık düğmeleri ikisini keser, overview.js balonda iki fiyat gösterir. Tek teklifte eski tek çizgi.
- Dosyalar: web/pricechart.py, web/dashboard.py, static/overview.css, static/overview.js, tests/test_web.py, docs/previews/depo-yeni-cizgi.jpg.

## Test ve doğrulama

- sh tools/check.sh: ruff + 614 test OK. Canlı kanıt aşağıda.

## Veri, yedek ve geri alma

- Veri değişmez. Geri alma: 4.12.0 kaydıyla prepare_code_rollback.sh.

## Canlı kanıt

- install_rpi.sh (yedek b160df65): 4.13.0 started, sağlık ok, veri sayıları korundu, Traceback yok.
