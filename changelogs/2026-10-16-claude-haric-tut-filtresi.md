# 2026-10-16 — Claude — Hariç tut filtresi eski kayıtlara da uygulanır

## Sorumlu ve yetki

- Agent ve konu/çalışma kopyası: Claude, hermes-excl
- İnsan talimatı ve kapsamı: Nuri, Hepsiburada iPhone 17 Pro Max arama kartında "2 TB" hariç tutulduğu halde anasayfada 2 TB görünmesini engelleme; sonra "2 TB = 2048 GB eşitlemesi istemiyorum, yalnız büyük/küçük harf fark etmesin" ve "kur".
- Sürüm: 4.17.1 (taban d20589c, 4.17.0)

## Amaç ve davranış

- Önceki davranış: filtre yalnız yeni okumalara uygulanıyordu; önbellekten gelen satırlar, stok tablosu ve açılışta yayımlanmış tablo filtresizdi.
- Sonraki davranış: bu yollar kartın hariç tut filtresine uymayan başlıkları göstermez; state ve fiyat geçmişi korunur, filtre kalkınca satırlar döner. Filtre büyük/küçük harfe duyarsızdır, depolama yazımları eşitlenmez.
- Canlı 4.17.0 anasayfası kurulum öncesi okundu: 2 TB satırı yoktu.
- Dosyalar: monitor/summary.py (cached_summary_rows filtered parametresi, cached_stock_rows), tests/test_exclusion_filter.py (yeni), tests/test_monitor.py (stub), ha-addon/CHANGELOG.md, config sürümleri.

## Test ve doğrulama

| Kontrol | Komut/yöntem ve kapsam | Sonuç |
|---|---|---|
| Yerel test | sh tools/check.sh | 621 test OK |
| Canlı davranış | kurulumdan sonra anasayfa okuma | aşağıda/sohbet raporunda |

## Veri, yedek ve geri alma

- Veri etkisi yok (silme yok). Yedek: kurulum betiği en yeni doğrulanmış günlük tam yedeği kullanır; yeni yedek alınmadı.
- Geri alma: sh tools/prepare_code_rollback.sh d20589c (4.17.0). rollback_rpi.sh kullanılmaz.

## Yayın ve kurulum

- Yayımlandı: 4.17.1, push edildi (commit: git log). Kuruldu: 4.17.1 çalışıyor, healthy, 14 aktif kart, price_points 687.
- Kullanılan doğrulanmış günlük yedek: 42c055d8. Kurulum sonrası anasayfa okuması: 2 TB / 2048 GB satırı yok.
