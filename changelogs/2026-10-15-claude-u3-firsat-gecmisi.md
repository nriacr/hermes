# 2026-10-15 — Claude — U3 Fırsat geçmişi sayfası (4.6.0)

## Sorumlu ve yetki

- Agent: Claude (dal release-u3, preview-u3 üzerinden). İnsan talimatı: Nuri U3'ü onayladı, önizlemeyi gördü ve "u3 devreye al" dedi (15 Ekim 2026).

## Amaç ve davranış

- Önce: gönderilen bildirimlere panelden bakılamazdı.
- Sonra: web/opportunities.py sayfayı hermes.db outbox'ından (30 gün) ve yayımlanmış özetten üretir; Araçlar'da "Fırsat geçmişi" bağlantısı. Yeni tablo/veri yok; results.py'de küçük bağlantı değişikliği.
- Dosyalar: web/opportunities.py, web/pages.py, web/server.py, monitor/results.py, tests/test_opportunities.py, CHANGELOG, config sürümü.

## Test ve doğrulama

| Kontrol | Komut | Sonuç |
|---|---|---|
| Yerel test | sh tools/check.sh | aşağıda |
| Canlı | tools/install_rpi.sh | aşağıda |

## Veri, yedek ve geri alma

- Kullanıcı verisi değişmez (yalnız okuma). Geri alma: sh tools/prepare_code_rollback.sh 4.5.0 sürümünün kaydı (ca0d75a).
