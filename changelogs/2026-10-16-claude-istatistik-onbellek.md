# 2026-10-16 — Claude — İstatistik sayfası önbellek ve SQL toplama

## Sorumlu ve yetki

- Agent ve konu/çalışma kopyası: Claude, hermes-work, istatistik sayfası
- İnsan talimatı ve kapsamı: Nuri, "ok" (4.15.1 günlüğü okumalar/istekler bölümlerinin 8 sn'de bitmediğini, sayfanın 19,3 sn'de hazırlandığını gösterdi; önerilen önbellek + SQL toplama onaylandı).
- Başlangıç commiti: 47d8687 (canlı 4.15.1). Sürüm: 4.16.1 (ilk push'ta sürüm boş kaldı: 66ff949; bu commit düzeltir. O commit canlıya 4.16.0 etiketiyle girmiş olabilir)

## Amaç ve davranış

- Sorun: sayfa her açılışta ~20 bin okuma + on binlerce istek satırını Pi'de Python'a çekiyordu.
- Önce: her istek veritabanını okuyordu; 8 sn sınırı aşılınca bölümler boş kalıyordu.
- Sonra: sayfa arka planda hazırlanıp bellekten gösterilir (60 sn tazelik, bayat sayfa hemen gösterilir, yenisi arka planda hazırlanır, başlangıçta ısıtılır); istek sayıları GROUP BY ile SQLite'ta toplanır (history.read_request_counts; eski read_site_requests ve kullanılmayan typical_ms kaldırıldı).
- Değişen dosyalar: web/statistics.py (önbellek), history.py, web/server.py (silmede önbellek temizliği), app.py (ısıtma), tests/test_web.py, tests/test_history.py, tests/test_statistics_guard.py, sürüm dosyaları, CHANGELOG.

## Test ve doğrulama

| Kontrol | Komut/yöntem ve kapsam | Sonuç / kanıt dosyası |
|---|---|---|
| Yerel test | sh tools/check.sh (620 test, ruff) | başarılı |
| Canlı davranış | kurulum sonrası günlük + sayfa süresi | kurulum notuna bakın |
| Fiziksel doğrulama | Nuri sayfayı açar | bekliyor |

## Yayın ve kurulum

- 66ff949 yanlışlıkla boş sürümle push edildi (ha-version başka bir yayın yüzünden başarısız olmuştu); o sırada başka bir thread 4.16.0'ı kurdu ve bu kod 4.16.0 etiketiyle canlıya girmiş olabilir. Düzeltme: 538060a, sürüm 4.16.1, sh tools/install_rpi.sh ile kuruldu (add-on started, sürüm 4.16.1, 610 test geçti).
- Canlı kanıt (günlük): açılışta arka plan hazırlığı 24 sa 5,9 sn, 7 gün 5,0 sn; bölümler 8 sn sınırının altında. Sayfa bellekten sunulur. Nuri'nin açış süresi bekleniyor.

## Veri, yedek ve geri alma

- Veri etkisi yok (yalnız okuma). Geri alma: sh tools/prepare_code_rollback.sh 47d8687 (rollback_rpi.sh kullanılmaz).

## Kalan durum

- Okumalar sorgusu (~20 bin satır) hâlâ ilk hazırlıkta yavaş olabilir; süre günlükten izlenir.
