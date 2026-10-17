# 2026-10-17 — Claude — Hepsiburada ısınma isteği (4.19.1)

## Sorumlu ve yetki

- Agent ve konu/çalışma kopyası: Claude, `hermes-hbwarm` (dal `claude-hb-warmup`).
- İnsan talimatı: Nuri, 4.18.0 internet sayacının bulgusundan sonra "Hepsiburada her okumadan önce ana sayfaya ısınma isteği atıyor... bunu değerlendir, uygunsa devreye al" dedi. Koşul: kontrol sayısı azalmaz, bot korumasına etkisi riskli/belirsizse dokunulmaz.
- Başlangıç commiti f64cd64 (4.19.0, SSD thread'inin sıkıştırma sürümü); sürüm 4.19.0 → 4.19.1.

## Değerlendirme

- Kod: `fetch_hepsiburada_page` her çağrıda `session.get(HOME_URL)` yapıyordu (3.0 yeniden yazımından beri, git log ve changelog'da gerekçesi yok; hiçbir hata/engel kaydı onu gerekçelendirmiyor). Oturum zaten çerezleri taşıyor (her çevrimde site başına bir `MeasuredSession`), yani aynı oturumda ikinci ana sayfa isteği yeni çerez getirmez. Gerçek tarayıcı da her ürün sayfası öncesi ana sayfayı yeniden yüklemez; bu davranış tersine daha doğal.
- Sayaç (4.18.0, 16:16-16:26 yerel, 10 dk): Hepsiburada 249 istek / 5,46 MB (Amazon 57 istek / 13,5 MB). Okuma başına bir ana sayfa isteği olduğundan isteklerin büyük kısmı ısınmaydı; ana sayfa başına düşen bayt Hepsiburada için tipik sayfadan küçüktür (ortalama 22 KB).
- Bot koruması riski: ısınma tamamen kaldırılmadı; oturumun ilk okumasında ve her başarısızlıktan sonra aynen yapılır. Başlıklar, kimlik, yedek yollar, sıra/gecikme değişmedi. Risk düşük ama sıfır değil; bu yüzden kurulumdan sonra Hepsiburada başarı/engel oranı sayaçla izlenir, bozulursa geri alınır.

## Davranış ve dosyalar

- Önceki: her sayfa okumasından önce ana sayfa isteği. Sonraki: oturum başına bir ısınma; ısınma başarısızsa (istisna veya durum ≥ 400) ya da oturumdaki bir sayfa okuması başarısız olursa bir sonraki okuma yeniden ısınır (`hermes_hepsiburada_warmed` oturum özniteliği).
- Dosyalar: `providers/hepsiburada/__init__.py` (`_warm_up`, başarısızlıkta sıfırlama), `tests/test_hepsiburada_warmup.py` (4 yeni test: tek ısınma, yeni oturumda ısınma, başarısız ısınma tekrar, başarısız okuma sonrası tekrar), `config.yaml`/`constants.py` (sürüm), `CHANGELOG.md`.

## Test ve doğrulama

| Kontrol | Komut/yöntem ve kapsam | Sonuç / kanıt |
|---|---|---|
| Yerel test | `python -m unittest discover` (644 test), `ruff check` | hepsi geçti |
| Canlı davranış | aşağıdaki bölüm | |

## Veri, yedek ve geri alma

- Kullanıcı verisine etkisi yok (yalnız ağ isteği azalır).
- Geri alma: `sh tools/prepare_code_rollback.sh f64cd64` (4.19.0). 4.19.0'dan eski koda DÖNME: sıkıştırılmış state'i okuyamaz.

## Canlı doğrulama (2026-10-17)

- Kurulum: 4.19.1, kurulan kayıt 2393453, `install_rpi.sh` bin/ha-exclusive altında, yedek e7a080d7 (13:26 UTC tam; yeni yedek alınmadı, AGENTS.md kuralı). Sağlık ok, 4.19.1 çevrimleri çalışıyor, günlükte Traceback yok.
- Önce/sonra (aynı sayaç, 10 dk pencere): 4.18.0 16:16-16:26 ve 17:02-17:12 pencereleri Hepsiburada 249 ve 248 istek, 5,46 MB; 4.19.1 pencereleri (17:12-17:22, 17:22-17:32) 224 ve 224 istek, 4,90 ve 4,89 MB. Düşüş yaklaşık 24 istek (%10) ve 0,55 MB (%10), yani yaklaşık 70 MB/gün. Isınma isteği beklenen "yarı" değildi: asıl kalabalık başka yerde (aşağı bakın).
- Engel/hata: izleme penceresinde (17:12-17:34) günlükte Hepsiburada için captcha/bot koruması veya "Hepsiburada teşhis" satırı yok (grep ile tarandı). Durum kodu kırılımı sayaçta yok; PIN'li istatistik sayfası okunamadı.
- Gözlem (kanıt değil, çıkarım): Hepsiburada ortalama istek boyutu 22 KB, gerçek ürün sayfası bundan çok büyük. Günlükte dakikada yaklaşık 4 "Hepsiburada teklifleri" başarısı var (yaklaşık 40 başarılı okuma/10 dk) ve 224 istek/10 dk ise okuma başına yaklaşık 5-6 istek demek: ısınma + requests + requests_fresh denemeleri (url_variants ile çarpılı) + curl. Muhtemelen python-requests denemeleri başarısız (küçük yanıtlar), curl_cffi başarılı. Doğrulamak için deneme başına durum/yöntem kaydı gerekiyor.
- Çapraz kontrol: Hermes konteyneri 103 sn'de rx 3,20 MB (31 KB/s, yaklaşık 2,7 GB/gün), sayaç 10 dk'da 16,6 MB (27,7 KB/s); sayaç konteynerin %89'u.
