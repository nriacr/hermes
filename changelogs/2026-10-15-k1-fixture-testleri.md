# 2026-10-15 — Claude — K1 gerçek sayfa (fixture) testleri

## Sorumlu ve yetki

- Agent: Claude, hermes-work. İnsan talimatı: Nuri optimizasyon listesindeki tüm maddeleri onayladı (K1).
- Sürümsüz araç/test değişikliği; uygulama sürümü 4.2.7 kalır, yeniden kurulum gerekmedi.

## Amaç ve davranış

- Önce: ayrıştırıcı testleri yalnız elle yazılmış HTML parçacıklarıydı.
- Sonra: tests/fixtures/<site>/<ad>.html (gerçek sayfadan temizlenmiş) + <ad>.expected.json; tests/test_fixtures.py her sayfayı ayrıştırıp kayıtlı başlık/fiyatla karşılaştırır, hata mesajı hangi site/sayfanın bozulduğunu söyler.
- tools/capture_fixture.py: sayfayı indirir (veya --file ile kayıtlı sayfayı alır) ve stil/svg/görsel/izleme/jeton benzeri betikleri atar; yalnız ayrıştırıcıların okuduğu kısım kalır. Oturum açmış sayfa yakalanmaz.
- Eklenen sayfalar: bengurme/kan-uzumu (100 KB), nordbron/stark-sirt-cantasi (34 KB), 15 Ekim 2026'da herkese açık sayfadan.
- Sınır: Hepsiburada (403), Zara (boş yanıt), Network/H&M/Beymen/Trendyol ürün sayfaları Mac'ten alınamadı veya ürün adresi elimde yok; bu siteler için sayfa, Pi'den/Nuri'nin takibinden --file ile eklenebilir. Amazon sayfası ek istek oluşturmamak için yakalanmadı.

## Test ve doğrulama

| Kontrol | Komut | Sonuç |
|---|---|---|
| Yerel test | sh tools/check.sh | ruff + 596 test OK |

## Yayın ve kurulum

- Yalnız test/araç; kurulum gerekmez. Veri etkisi yok. Geri alma: commit'i geri al.

## Kalan durum

- Diğer siteler için fixture eklenmedi (yukarıdaki sınır).
