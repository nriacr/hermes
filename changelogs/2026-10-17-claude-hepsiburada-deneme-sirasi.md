# 2026-10-17 — Claude — Hepsiburada deneme sırası (4.19.3 ölçüm, 4.19.4 uygulama)

## Sorumlu ve yetki

- Claude, `hermes-hbdiag` (4.19.3) ve `hermes-hborder` (4.19.4). Nuri "evet hepsiburada optimizasyonunu yap" dedi (önce ölçüm, sonra uygulama; sıfır kayıp kuralı).

## Ölçüm (4.19.3, 18:07 yerel, 10 dk)

- Günlük satırı: `Hepsiburada deneme özeti (son 10 dk): curl: ok=28 | home: 403=28 | requests: 403=84 | requests_fresh: 403=84`.
- Yani 28 okumanın her biri: ana sayfa ısınma 403, python-requests 3 adres varyantı 403, requests_fresh 3 varyant 403, sonra curl_cffi başarılı = 8 istek (toplam 224 istek/10 dk, 4,9 MB).
- Sonuç: python-requests Hepsiburada tarafından her zaman 403 alıyor; ısınma hiç başarılı olmuyor (4.19.1'in "başarılıysa bir kez" mantığı bu yüzden bir şey değiştirmedi, çünkü ısınma hep 403). Tek çalışan yol curl_cffi chrome124. Hipotez doğrulandı.

## Davranış ve dosyalar

- Önceki: her okumada tam sıra. Sonraki: curl_cffi bir adresi başarıyla okuduysa aynı url'nin sonraki okuması önce yalnızca o adresle curl'ı dener (`curl_first`); başarısızlıkta adres unutulur ve tam özgün sıra aynen çalışır. Başlıklar, kimlik, curl oturumu (okuma başına yeni), okuma sayısı/sıklığı aynı. Yeni bağımlılık yok.
- Dosyalar: `providers/hepsiburada/__init__.py` (`_curl_winner`, `_curl_first`, `note_attempt`), `tests/test_hepsiburada_warmup.py`, `CHANGELOG.md`, sürüm dosyaları.
- Beklenen etki: okuma başına 8 → 1 istek (başarılı durumda), yani Hepsiburada yaklaşık 224 → 28-30 istek/10 dk; bayt kazancı küçük (başarısız yanıtlar küçük): yaklaşık 0,5-1 MB/10 dk.

## Test

| Kontrol | Yöntem | Sonuç |
|---|---|---|
| Yerel test | `python -m unittest discover` (650 test), `ruff check` | hepsi geçti |
| Yeni testler | ilk okuma tam sıra ve hatırlama, hatırlanan adres doğrudan curl (başka istek yok), başarısız kısayol tam sırayı çalıştırıp adresi unutur ve okuma kaybolmaz, curl yoksa hiçbir şey hatırlanmaz | geçti |
| Canlı | aşağıda | |

## Yedek ve geri alma

- Yedek: install_rpi.sh'nin doğruladığı en yeni tam yedek (yeni yedek alınmadı). Veriye etkisi yok.
- Geri alma: `sh tools/prepare_code_rollback.sh 48e380d`; 4.19.0'dan eski koda dönülmez.

## Canlı doğrulama (2026-10-17)

- Kurulum: 4.19.4 (92cafbb), yedek e7a080d7 (yeni yedek alınmadı). Sonradan başka işler 4.19.5 ve 4.20.0 olarak üstüne kuruldu; bu kod hepsinde var.
- Önce (4.19.3, 10 dk, 28 okuma): 224 istek, 4,9 MB; ana sayfa 403 x28, requests 403 x84, requests_fresh 403 x84, curl ok x28 (okuma başına 7 reddedilen + 1 başarılı).
- Geçiş penceresi (4.19.5, restart sonrası): curl_first 8-9, tam sıra 24 okuma (adresler yeni öğreniliyordu).
- Sonra (4.20.0, 18:49-18:59): Hepsiburada 31 istek, 4,97 MB; `curl_first: ok=31`, tam sıra 0, hiç 403 yok. Toplam Hermes 268 → 83 istek/10 dk. Hepsiburada isteği 224 → 31 (-%86), bayt -%10 (reddedilen yanıtlar küçüktü).
- Başarılı okuma sayısı: 31 (önce 28-33); engel/hata/"teşhis" satırı yok, Traceback yok. Sağlık: 4.20.0 started, CPU %26, bellek 380-410 MB. Konteyner rx 100 sn'de +2,9 MB.
- Sınır: 60 dk izleme yapılmadı; iki restart (başka sürümler) pencereleri böldü. Ek bayt kazancı küçük, asıl kazanç başarısız/reddedilen istekleri %86 azaltmak (bot koruması için de daha iyi).
- Geri alma: `sh tools/prepare_code_rollback.sh 48e380d` (4.19.3 öncesi); 4.19.0'dan eski koda dönülmez.
