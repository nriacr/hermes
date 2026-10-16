# 2026-10-16 — Claude — SSD yazma azaltma ve yazma sayacı

## Sorumlu ve yetki

- Agent ve konu/çalışma kopyası: Claude, hermes-work, SSD yazma
- İnsan talimatı ve kapsamı: Nuri, "SSD'ye sürekli ve gereksiz yere yazdığımızı düşündüğün bir şey var mı?" sorusu üzerine yaptığım ölçümden sonra "ok" (sayaç + yazma azaltma).
- Başlangıç commiti: 2662736 (canlı 4.16.1). Sürüm: 4.17.0

## Amaç ve davranış

- Ölçüm (öncesi): /proc/diskstats sda, 3 dk ortalama 42 MB/dk (≈60 GB/gün); açılıştan beri ortalama ~74 GB/gün. HA kaydedici günde yalnız ~76 bin satır yazıyor, asıl neden değil.
- Önce: her okuma sonucunda tüm state (~738 KB) SQL snapshot + girintili state.json + fiyat özeti JSON; okuma başına tahmini 3-4 MB.
- Sonra: yalnız zaman damgası değiştiyse state snapshot en çok dakikada bir (fiyat/hata/uyarı değişikliği, çevrim sonu ve bildirim kuyruğu anında); JSON kopyaları en çok dakikada bir (zamanlayıcı, durdurma ve çıkışta flush); 10 dakikada bir "Yazma özeti" günlük satırı.
- Not (kabul edilen risk): okuma ortasında çökme olursa en çok 60 sn'lik zaman damgası güncellemesi kaybolur; fiyat, hata, uyarı ve bildirim durumu anında kayıtlı. Kart-başı SQL satırı (daha büyük yeniden yapılanma) bu sürümde yapılmadı; sayaç sonucuna göre karar verilecek.
- Değişen dosyalar: writestats.py (yeni), storage.py, database.py, history.py, monitor/results.py, tests/support.py (testte gecikme 0), tests/test_write_reduction.py (yeni), sürüm dosyaları, CHANGELOG.

## Test ve doğrulama

| Kontrol | Komut/yöntem ve kapsam | Sonuç / kanıt dosyası |
|---|---|---|
| Yerel test | sh tools/check.sh (617 test, ruff) | başarılı |
| Canlı davranış | kurulum sonrası sda yazma ölçümü ve günlükteki Yazma özeti | kurulum notuna bakın |

## Yayın ve kurulum

- Kurulum sonrası eklenir.

## Veri, yedek ve geri alma

- Kullanıcı verisi: SQL asıl kaynak aynı; JSON kopyaları ≤60 sn geriden gelebilir. Geri alma: sh tools/prepare_code_rollback.sh 2662736 (rollback_rpi.sh kullanılmaz).

## Kalan durum

- Kalan SSD yazımı Hermes dışından geliyorsa (Supervisor/Docker, mosquitto, vb.) bu sürüm onu azaltmaz; ölçümle ayrıştırılacak.
