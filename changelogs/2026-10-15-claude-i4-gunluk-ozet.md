# 2026-10-15 — Claude — I4 günlük özet bildirimi (4.4.0)

## Sorumlu ve yetki

- Agent: Claude, hermes-work. İnsan talimatı: Nuri optimizasyon listesindeki tüm maddeleri onayladı (I4).

## Amaç ve davranış

- Önce: günlük durumu görmek için istatistik sayfasına bakmak gerekiyordu.
- Sonra: monitor/daily.py, reads tablosu (son 24 sa) + yayınlanmış özet satırlarından tek mesaj üretir; runner 5 sn'lik yardımcı döngüde 09:00–22:00 arasında günde bir kez kuyruğa alır (state _meta.last_daily_summary ile bir kez). Anahtar daily_summary_enabled (config.yaml, OPTION_DEFAULTS, HermesConfig, Ayarlar > Günlük özet onay kutusu; varsayılan açık). CAPTCHA/503/429 sayılmaz. Yardımcı kontroller artık try/except içinde (isteğe bağlı bildirim izleyiciyi durduramaz).
- Dosyalar: monitor/daily.py (yeni), monitor/runner.py, config.py, models.py, web/settings.py, config.yaml, tests/test_daily_summary.py, CHANGELOG.

## Test ve doğrulama

| Kontrol | Komut | Sonuç |
|---|---|---|
| Yerel test | sh tools/check.sh | ruff + 612 test OK |
| Canlı | kurulum sonrası aşağıda | |

## Veri, yedek ve geri alma

- Kullanıcı verisi etkilenmez; _meta'ya tek tarih alanı eklenir. Geri alma: sh tools/prepare_code_rollback.sh c7d77cd (4.3.0).
- Ayarlardan kapatma: Ayarlar > Günlük özet.

## Kalan durum

- Saat sabit 09:00; ayarlanabilir değil.
