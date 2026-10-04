# Hermes

Hermes, Home Assistant üzerinde çalışan çok siteli fiyat ve Telegram fırsat
takip add-on'udur.

- Bir takip kartında en fazla 5 ürün veya arama linki; site ve link türü
  linkten otomatik anlaşılır. Arama linklerinde kart adı aranacak ifadedir.
- Desteklenen siteler: Amazon, Hepsiburada, Trendyol, Network, Beymen Club,
  Ben Gurme, Nordbron, Zara, H&M.
- Öncelik: yüksek her çevrimde, orta en az 2 saatte, düşük en az 6 saatte bir
  kontrol edilir. Tablodaki renkli daire önceliği gösterir.
- Amazon: varyasyonlar (renk × kapasite/ölçü, en fazla 60), doğrulanmış Amazon
  Depo teklifleri ayrı satırlarda, "yalnızca platformun kendi satıcısı" filtresi,
  iki farklı sayfa art arda engellenirse tüm Amazon 3 → 6 → 12 → 20 dakika mola
  (tek yoklamayla), tek sayfa tek başına engellenirse yalnız o kart 30–60 dk
  dinlenir; kayan pencere istek sınırı ve engelden sonra ilk saat yarım hız. Ürün sayfası ve ikinci el
  listesi (Depo şeridi) her ~100 sn'de, varyant taraması kendi şeridinde ~270 sn'de bir okunur.
- Bildirimler Pushover ile gelir; CAPTCHA ve HTTP 503 hataları bildirim
  göndermez, panelde görünür.
- Telegram kanallarında keyword takibi ve Kayıtlı Mesajlar'dan hızlı takip
  ekleme.
- Panel: özet tablo, istatistik, bağlantı testi ve ayarlar. Home Assistant
  içinden (ingress) ve isteğe bağlı token'lı public adresten aynı şekilde
  çalışır.

Kurulum: Home Assistant > Add-on Store > Repositories alanına
`https://github.com/nriacr/hermes` ekle.

Ayrıntılar: [add-on kılavuzu](ha-addon/README.md),
[değişiklik günlüğü](ha-addon/CHANGELOG.md),
[mimari](docs/ARCHITECTURE.md). Geliştirme kuralları [AGENTS.md](AGENTS.md)
dosyasındadır.
