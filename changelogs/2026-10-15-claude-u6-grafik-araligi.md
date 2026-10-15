# 2026-10-15 — Claude — U6 fiyat grafiği zaman aralığı (4.7.0)

## Sorumlu ve yetki

- Agent: Claude (dal release-u6). İnsan talimatı: Nuri U6'yı onayladı; önizlemeyi gördükten sonra "yayına al, sonra rafine ederiz" dedi (15 Ekim 2026).

## Amaç ve davranış

- Önce: detayda tek, tüm geçmişi gösteren grafik.
- Sonra: pricechart.ranged_chart 7/30/90 gün ve Tümü düğmeleri üretir (window_points: pencere başındaki geçerli fiyattan başlar); overview.js düğmeyle paneli değiştirir; CSS düğme stili. Depo/yeni fiyat ayrı çizgi bu sürümde yok (fiyat geçmişi tek seri).
- Dosyalar: web/pricechart.py, web/dashboard.py, static/overview.js, static/overview.css, docs/previews/u6-90gun.png, CHANGELOG, config sürümü.

## Test ve doğrulama

Aşağıdaki canlı kanıt bölümü kurulumdan sonra doldurulur.

## Veri, yedek ve geri alma

- Yalnız sunum; veri değişmez. Geri alma: 4.6.0 kaydı (4a2e1f0) ile prepare_code_rollback.sh.
