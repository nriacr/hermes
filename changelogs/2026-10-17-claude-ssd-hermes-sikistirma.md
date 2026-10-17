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
| Canlı: Hermes yazması | Hermes "Yazma özeti" (10 dk) önce/sonra | 13,36 MB/dk → 1,10 MB/dk (state.json SQL 116,3 MB/157x → 1,7 MB/31x; fiyat özeti SQL 8,2 → 0,9 MB) |
| Canlı: SSD toplam | /proc/diskstats sda, HA ayarları zaten canlıyken: önce 22,6 MB/dk (253 sn), sonra 7,3 MB/dk (540 sn, kurulumdan hemen sonra) | ~%68 düşüş (HA öncesi 24,4 MB/dk) |
| Canlı: davranış | Kurulum sonrası günlük, Hermes 4.19.0 started, outbox/gecikme sağlıklı, hata/bozuk kayıt satırı yok; fan ve UPS sensörleri sağlıklı | Geçti. Bildirim/tablo birebir karşılaştırması: kurulumda yeni bildirim olmadı, anlık görüntü eşitliği her yazmada bayt bayt doğrulanıyor |

## Yayın ve kurulum

- Yayımlandı: c04545b (origin/main). Kuruldu: 2026-10-17 ~13:43 UTC, `ha-exclusive sh tools/install_rpi.sh`; test, yedek doğrulama, sürüm 4.19.0 ve canlı okuma geçti. Önizleme ortamı yok; ön doğrulama yerel testlerle yapıldı.

## Veri, yedek ve geri alma

- Kullanıcı verisine etkisi: veri silinmez; `hermes.db` snapshot satırlarının kodlaması değişir (geriye dönük okunur). JSON kopyaları yerinde kalır.
- Yedek: HA tam yedeği "SSD optimizasyonu öncesi - 2026-10-17 HA" (slug e7a080d7, 13:26 UTC, Hermes dahil).
- Geri alma: kod geri dönüşü `sh tools/prepare_code_rollback.sh <4.18.0 commiti 65bb3ab>` varsayılan olarak reddeder (sıkıştırılmış kaydı eski kod okuyamaz); bilinçli kullanım için önce Hermes'i durdurup `snapshots` tablosundaki state.json ve latest_price_summary.json satırlarını silme gerekir (eski kod JSON kopyasından açılır) ve `HERMES_ALLOW_PRE_COMPRESSION_ROLLBACK=evet` verilir. Veri geri yükleme ayrı insan yetkisi ister.

## Kalan durum

- Kalan yazma ~7 MB/dk: HA çekirdeği/zigbee2mqtt/diğer; Hermes JSON kopyası (state.json) dakikada ~0,8 MB hâlâ yazılıyor (geri dönüş uyumluluğu için). İleride JSON kopya aralığı 60 sn → 300 sn yapılabilir (~0,7 MB/dk kazanç), şimdilik yapılmadı.
