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
  ilk engelde tüm Amazon 15 → 30 → 60 dakika mola verir (tek yoklamayla) ve
  moladan sonra yeni bir anonim ziyaretçi olarak devam eder; kayan pencere istek
  sınırı ve engelden sonra ilk saat yarım hız. Hedefe %15 yakın ya da Depo
  teklifi olan kartların ürün sayfası ve ikinci el listesi (Depo şeridi) her
  ~100 sn'de, diğerlerininki 10 dakikada bir; varyant taraması kendi şeridinde
  ~270 sn'de bir okunur.
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
