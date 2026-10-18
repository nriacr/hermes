# 2026-10-16 — Claude — Dinamik hedef çizgisi (4.14.0)

## Sorumlu ve yetki

- Agent: Claude (dal preview-hedef-cizgi). İnsan talimatı: Nuri isteği verdi, önizlemeyi gördü ve "canlıya al" dedi (15 Ekim 2026).

## Amaç ve davranış

- Önce: hedef çizgisi her zaman yatay, güncel hedefte.
- Sonra: yeni `targets(card, target, at)` tablosu (history.py); monitor/cycle.py başlangıçta her kartın hedefini son kayıttan farklıysa ekler (kart kimliği tracking_id; ayar kaydı Hermes'i yeniden başlattığı için değişiklik anında yakalanır). web/pricechart.load_targets + target_series hedefi basamak çizgisi olarak çizer; yalnız eski kayıt yoksa düz çizgi. Fiyat sıfırlama/istatistik sıfırlama bu tabloya dokunmaz.
- Dosyalar: history.py, monitor/cycle.py, web/pricechart.py, web/dashboard.py, tests/test_web.py, docs/previews/hedef-cizgisi-dinamik.jpg.

## Test ve doğrulama

- sh tools/check.sh: ruff + 615 test OK. Canlı kanıt aşağıda.

## Veri, yedek ve geri alma

- Yalnız yeni küçük tablo eklenir; eski veri değişmez. Geri alma: 4.13.0 kaydıyla prepare_code_rollback.sh (tablo kalır, eski sürüm yok sayar).

## Canlı kanıt

- install_rpi.sh (yedek b160df65): 4.14.0 started, sağlık ok, veri sayıları korundu, günlükte Traceback ve veritabanı yazma hatası yok. Hedef kaydı başlangıçta alındı; grafik, hedef değişince basamak yapacak.
