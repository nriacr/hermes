# Hermes

Hermes, Home Assistant üzerinde çalışan çok siteli fiyat ve Telegram fırsat takip add-on'udur.

## Cursor ile geliştirme

Cursor'a geçiş için gerekli kalıcı proje bağlamı repoya eklenmiştir:

- [`AGENTS.md`](AGENTS.md): bağlayıcı mimari, güvenlik, test ve yayın kuralları
- [`.cursor/rules/hermes.mdc`](.cursor/rules/hermes.mdc): Cursor'un her sohbette otomatik okuyacağı kurallar
- [`docs/CURSOR_HANDOFF.md`](docs/CURSOR_HANDOFF.md): güncel mimari, davranışlar, site algoritmaları ve riskler
- [`docs/CURSOR_START_PROMPT.md`](docs/CURSOR_START_PROMPT.md): ilk Cursor Agent sohbetine yapıştırılacak hazır metin

Cursor'da bu repo klasörünü açın ve başlangıç metnini ilk Agent sohbetine
gönderin. Kullanıcı verileri ve gizli anahtarlar repoda değil, Home Assistant
`/data` alanında kalır.

Takip edilen alanlar:
- `takip_edilenler`: tek kayıt altında en fazla 5 ürün veya arama linki
- Hermes linkten siteyi ve link tipini otomatik algılar
- Ürün linklerinde `name` boş bırakılabilir; Hermes ürün adını linkten okur. Arama linklerinde `name`, aranacak keyword olarak zorunludur.
- Amazon ürün linklerinde `Varyasyonları ekle` seçilirse, renk × kapasite/ölçü birleşimleri gerçek ürün kimlikleri üzerinden tek tek taranır (en fazla 60 varyant). Sıfır ve Amazon Depo teklifleri ayrı tutulur; uygun depo teklifi bulunduğunda kalan varyantlar beklenmeden bildirilir.
- Her takip kartında öncelik seçilebilir: yüksek her çevrimde, orta en az 2 saatte, düşük en az 6 saatte kontrol edilir. Yüksek öncelikli işler önce yürütülür; yeni eklenen veya tek kart olarak düzenlenen takip hemen kontrol edilir.
- Sonuç tablosunda normal ürünlerin adının başındaki renkli daire kontrol önceliğini gösterir: yüksek kırmızı, orta sarı, düşük yeşil. Amazon Depo satırları bu göstergeden etkilenmez.
- `Yalnızca platformun kendi satıcısı` filtresi Amazon sıfır ürünlerinde yalnızca `Amazon.com.tr` satıcısını tutar. Satıcı bilgisi okunamayan teklifler elenir; doğrulanmış Amazon Depo teklifleri her zaman korunur. Diğer mağazalar için filtre desteği ileride eklenecektir.
- Amazon'un bağlantı yerine düz metin olarak sunduğu `Amazon.com.tr` satıcı bilgisi de resmi satıcı filtresinde tanınır.
- Ana ekrandaki `Test` sayfası bağlantıyı anlık ve geçici olarak okur; arama anahtar kelimesi, beden, hariç tut ve varyasyon seçenekleri bu test için ayrıca uygulanabilir.
- Telegram kanalları: keyword ve exclude keyword tabanlı fırsat bildirimi
- Telegram Kayıtlı Mesajlar: bağlantıyı gönder, Hermes hedef fiyatı sorup takibi ekler
- Arama bağlantılarında en fazla 60 sonuç otomatik taranır.

Bildirimler Pushover üzerinden gönderilir.  
Ingress paneli üzerinden durum, özet tablo ve test bildirimi yönetilebilir. İsteğe bağlı public panel; güvenli bir token ve ters proxy/tünel ile dışarıdan da kullanılabilir.

## Home Assistant Repository

Home Assistant > Add-on Store > Repositories alanına:

`https://github.com/nriacr/hermes`

ekleyerek kurulabilir.

Detaylı kullanım, veri dosyaları ve geliştirme kontrolleri için [add-on kılavuzuna](ha-addon/README.md) bakabilirsin.
