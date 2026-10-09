# Hermes

Hermes, Home Assistant üzerinde çalışan çok siteli fiyat ve Telegram fırsat
takip add-on'udur.

- Bir takip kartında en fazla 5 ürün veya arama linki; site ve link türü
  linkten otomatik anlaşılır. Arama linklerinde kart adı aranacak ifadedir.
- Desteklenen siteler: Amazon, Hepsiburada, Trendyol, Network, Beymen Club,
  Ben Gurme, Nordbron, Zara, H&M, Togg.
- Öncelik süresiyle seçilir: her çevrim (kırmızı), 30 dk (turuncu), 60 dk (sarı), 3 saat
  (sarı-yeşil), 6 saat (yeşil); Amazon'da her çevrim kartları her arama turunda: ana sayfa ve tüm varyantlar liste başından sonuna bir kez okunur, sonra tur yeniden başlar (tur süresi sayfa sayısına bağlı, en sık 20 saniyede bir). Tablodaki renkli daire önceliği gösterir.
- Siteler kendi bağımsız kontrol sıralarında çalışır; bir sitenin uzun turu diğerinin sonraki kontrolünü bekletmez.
- Bildirimler ortak kalıcı Pushover kuyruğundan gelir. Bağlantı kesintisinde sınırlı yeniden deneme yapılır;
  güncelliğini kaybeden fiyat bildirimi gönderilmez. Kritik sorun panel ve Home Assistant uyarısında görünür.
- Çalışma verisi tek SQLite deposunda; fiyat geçmişi ve bildirim hafızası yeniden başlatmada korunur.
  Takip kartının adı/hedefi değişince kalıcı kimliği değişmez.
- Amazon davranışı değişmedi; kendi erişim/varyant kuralları site modülünde korunur.
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
