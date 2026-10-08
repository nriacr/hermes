# Hermes

Hermes, Home Assistant üzerinde çalışan çok siteli fiyat ve Telegram fırsat
takip add-on'udur.

- Bir takip kartında en fazla 5 ürün veya arama linki; site ve link türü
  linkten otomatik anlaşılır. Arama linklerinde kart adı aranacak ifadedir.
- Desteklenen siteler: Amazon, Hepsiburada, Trendyol, Network, Beymen Club,
  Ben Gurme, Nordbron, Zara, H&M, Togg.
- Öncelik: yüksek (kırmızı) her çevrimde, orta (sarı) saatte, düşük (yeşil) 3 saatte
  bir kontrol edilir; Amazon'da kırmızı kartlar her arama turunda: ana sayfa ve tüm varyantlar liste başından sonuna bir kez okunur, sonra tur yeniden başlar (tur süresi sayfa sayısına bağlı, en sık 20 saniyede bir). Tablodaki renkli daire önceliği gösterir.
- Amazon: varyasyonlar (renk × kapasite/ölçü, en fazla 60), doğrulanmış Amazon
  Depo teklifleri ayrı satırlarda, "yalnızca platformun kendi satıcısı" filtresi,
  ilk engelde tüm Amazon 5 → 10 → 20 → 30 dakika mola verir (tek yoklamayla) ve
  moladan sonra yeni bir anonim ziyaretçi olarak devam eder. Otomatik hız
  kademesi: engel dalgası tüm kategorilerin aralığını 2 (ikinci dalgada 4) katına
  çıkarır, her 10 dakika engelsiz geçince bir kademe gevşer ve kendiliğinden
  normal hıza döner. Ürün sayfası, ikinci el listesi ve varyantlar kategorisine
  göre okunur (fiyatın hedefe yakınlığı etkilemez): kırmızı her turda, sarı
  saatte, yeşil 3 saatte bir. Tek sayfalık ürünler Depo şeridinde, varyantlı
  aileler tarama şeridinde okunur; iki şerit sırayla istek atar.
  Kayan pencere istek sınırı 400'den başlar.
- Bildirimler Pushover ile gelir; CAPTCHA ve HTTP 503 hataları bildirim
  göndermez, panelde görünür.
- Telegram kanallarında keyword takibi ve Kayıtlı Mesajlar'dan hızlı takip
  ekleme.
- Panel: özet tablo, istatistik ve ayarlar. Home Assistant
  içinden (ingress) ve isteğe bağlı token'lı public adresten aynı şekilde
  çalışır.

Kurulum: Home Assistant > Add-on Store > Repositories alanına
`https://github.com/nriacr/hermes` ekle.

Ayrıntılar: [add-on kılavuzu](ha-addon/README.md),
[değişiklik günlüğü](ha-addon/CHANGELOG.md),
[mimari](docs/ARCHITECTURE.md). Geliştirme kuralları [AGENTS.md](AGENTS.md)
dosyasındadır.
