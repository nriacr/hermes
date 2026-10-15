# 2026-10-15 — Claude — U5 kart başına bildirim zamanı (4.9.0)

## Sorumlu ve yetki

- Agent: Claude (dal release-u5). İnsan talimatı: Nuri U5'i onayladı, önizlemeyi gördü ve "ok canlıya al" dedi (15 Ekim 2026).

## Amaç ve davranış

- Önce: kart bildirimleri her saat gönderilirdi.
- Sonra: WatchRule.notify_window (constants.NOTIFY_WINDOWS, config.yaml şeması, ayar formu); monitor/alerts.window_open yerel saate göre karar verir; results._record_offers pencere kapalıyken wants_alert/low_alert üretmez (durum işaretlenmez, pencere açılınca gönderilir).
- Dosyalar: constants.py, models.py, config.py, config.yaml, monitor/alerts.py, monitor/results.py, web/settings.py, tests/test_monitor.py, docs/previews/u5-bildirim-zamani.png, CHANGELOG.

## Test ve doğrulama

- sh tools/check.sh: ruff + 608 test OK. Canlı kanıt aşağıda.

## Veri, yedek ve geri alma

- Yeni seçenek isteğe bağlı; mevcut veri değişmez. Geri alma: 4.8.0 kaydıyla prepare_code_rollback.sh.
