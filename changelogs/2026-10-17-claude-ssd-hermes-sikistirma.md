# 2026-10-17 — Claude — Hermes SSD yazma azaltma (durum kaydı sıkıştırma)

## Sorumlu ve yetki

- Agent ve konu/çalışma kopyası: Claude, `hermes-work` (main).
- İnsan talimatı ve kapsamı: Nuri, "özellik eksiltmeden fonksiyon kısıtlamadan" SSD yazmasını azaltmayı onayladı (Hermes: durum kaydını sıkıştır, değişmeyeni tekrar yazma; synchronous=FULL kalır; bildirim/tablo davranışı birebir aynı kalacak). Koordinatör notu bu kapsamı aktardı; 4.18.0 üzerine inşa edildi.
- Tarih: 2026-10-17; sürüm 4.19.0.

## Amaç ve davranış

- Sorun: SSD'ye ~24 MB/dk yazılıyor; Hermes'in kendi verdiği ~13,35 MB/dk, bunun 11,6 MB/dk'sı `state.json` tam anlık görüntüsü (156 yazma × ~0,74 MB / 10 dk), 0,92 MB/dk fiyat özeti SQL, 0,82 MB/dk state JSON kopyası.
- Önceki: büyük anlık görüntüler düz JSON metin olarak SQL'e ve JSON dosyasına yazılıyordu.
- Sonraki: ≥4 KB anlık görüntüler `HZ1` işaretli zlib baytı olarak yazılır (yazmadan önce açılıp orijinalle karşılaştırılır; uyuşmazlıkta düz metin); okuma `decode_payload` ile hem eski düz hem sıkıştırılmış satırı okur. Aynı içerik tekrar yazılmaz (cache yalnız COMMIT sonrası güncellenir). JSON kopyası aynı içerikse yeniden yazılmaz.
- Dosyalar: `database.py` (encode/decode, yazma atlama), `delivery.py` ve `diagnostics.py` (doğrudan SELECT okumaları decode eder), `storage.py` (JSON aynı içerik atlama), `tools/prepare_code_rollback.sh` (4.19.0 öncesine dönüşte uyarı/ret), `tests/test_snapshot_compression.py`, `config.yaml`/`constants.py` sürüm, `CHANGELOG.md`.
- Bağımlılık: 4.18.0 (internet sayacı, diğer thread) üzerine.

## Test ve doğrulama

| Kontrol | Komut/yöntem ve kapsam | Sonuç / kanıt dosyası |
|---|---|---|
| Yerel test | `sh tools/check.sh` (ruff + 640 test, 6'sı yeni) | Geçti |
| Canlı davranış | Kurulum sonrası: Yazma özeti, /proc/diskstats (sda) önce/sonra, bildirim/tablo karşılaştırma | Aşağıda "Canlı kanıt" bölümü kurulumdan sonra doldurulur |

## Yayın ve kurulum

- Kurulum yapılana kadar bu kayıt "yayımlanmadı" sayılır; durum kurulumdan sonra aşağıya yazılır.

## Veri, yedek ve geri alma

- Kullanıcı verisine etkisi: veri silinmez; `hermes.db` snapshot satırlarının kodlaması değişir (geriye dönük okunur). JSON kopyaları yerinde kalır.
- Yedek: HA tam yedeği "SSD optimizasyonu öncesi - 2026-10-17 HA" (slug e7a080d7, 13:26 UTC, Hermes dahil).
- Geri alma: kod geri dönüşü `sh tools/prepare_code_rollback.sh <4.18.0 commiti 65bb3ab>` varsayılan olarak reddeder (sıkıştırılmış kaydı eski kod okuyamaz); bilinçli kullanım için önce Hermes'i durdurup `snapshots` tablosundaki state.json ve latest_price_summary.json satırlarını silme gerekir (eski kod JSON kopyasından açılır) ve `HERMES_ALLOW_PRE_COMPRESSION_ROLLBACK=evet` verilir. Veri geri yükleme ayrı insan yetkisi ister.

## Kalan durum

- Canlı ölçüm sonuçları kurulumdan sonra eklenecek.
