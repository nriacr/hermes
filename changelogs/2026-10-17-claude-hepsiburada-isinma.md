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
