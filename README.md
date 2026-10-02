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
- Ana tablodaki `Son güncelleme` sütunu her fiyatın kaç dakika önce okunduğunu gösterir; sonraki çevrime ertelenen ürünlerin ve eski fiyat kayıtlarının başarılı okuma zamanı korunur. `İstatistik` sayfasında günlük özet ilk sıradadır: çevrim sayısı, tipik süre, orta %50 süre aralığı ve 10 dakikayı aşan çevrim sayısı kompakt tabloda görünür. Güne dokununca ortalama, en kısa/en uzun süreler ve tek tek çevrim kayıtları açılır. Yedi günlük genel en kısa/en uzun/ortalama değerleri ve çevrim grafiği de korunur.
- Mobil görünümde ürün kartının fiyat geçmişi ve son güncelleme alanları kart genişliğini kullanır; günlük istatistik özetleri dar ekranlarda iki sütun halinde okunur.
- `Yalnızca platformun kendi satıcısı` filtresi Amazon sıfır ürünlerinde yalnızca `Amazon.com.tr` satıcısını tutar. Satıcı bilgisi okunamayan teklifler elenir; doğrulanmış Amazon Depo teklifleri her zaman korunur. Diğer mağazalar için filtre desteği ileride eklenecektir.
- Amazon'un bağlantı yerine düz metin olarak sunduğu `Amazon.com.tr` satıcı bilgisi de resmi satıcı filtresinde tanınır.
- Amazon CAPTCHA/koruma sayfası gösterdiğinde yalnızca etkilenen takip 15 dakika bekletilir; tekrarında 30, ardından en fazla 60 dakika sonra otomatik yeniden denenir. Diğer takipler çalışır, başarısız okumanın eski fiyatı güncel diye gösterilmez.
- Amazon bağlantı ve çerez oturumları servis çalıştığı sürece çevrimler arasında korunur; fiyat/ürün önbellekleri yalnızca ilgili çevrimde geçerlidir. Chromium gerektiğinde aynı anonim profil ve açık tarayıcı sürecini kullanır; servis yeniden başlatıldığında yeni oturum açılır.
- Gerçek doğrulama formu/metni veya HTTP 429/503 geldiğinde o sayfa için tekrar istek, çerez sıfırlama veya başka yöntemle kurtarma yapılmaz. Diğer bağlantı/yanıt hatalarında aynı adres için en fazla bir Chromium denemesi yapılabilir. Sayfadaki bir script veya ürün metninde `captcha` geçmesi tek başına koruma sayılmaz.
- Koruma sonrası tek kontrol tamamlandığında geçerli boş sonuç da kurtarma sayılır; orta/düşük kartlar normal 2/6 saat zamanlamasına döner. Eski koruma kaydı her çevrimde tekrar tarama yaptıramaz. HTTP 503 servis hatası, HTTP 429 istek sınırı ve gerçek doğrulama sayfası loglarda ayrı sınıflandırılır.
- Bir varyantın fiyatı bulunamaması, o sayfadan keşfedilen kardeş ASIN’lerin taranmasını kesmez. Yeni ürün fiyatı yoksa bile varsa ayrı depo teklif listesi okunur. Amazon’un seçili ürün için açıkça bildirdiği stok yokluğu normal `Stokta yok` sonucudur; CAPTCHA, bozuk sayfa veya stokta olan ürünün okunamayan fiyatı operasyonel hata olarak korunur. Stok satırları kartın sonraki kontrol sırası gelene kadar tutulur.
- `Test` sayfasındaki gerçek tarayıcı seçeneği, yalnızca ilgili testte Pi’deki Chromium’u kullanır; masaüstü tarayıcısını açmaz ve takip/bildirim kaydı oluşturmaz. Ana izleyici varsayılan olarak HTTP kullanmayı sürdürür; tarayıcıya geçiş karşılaştırma sonucuna bağlıdır. Chromium ile eşleşen sürücü aynı sistem paketlerinden kurulur, doğal tarayıcı kimliği kullanılır ve yeni çevrim fiyatları için tarayıcı önbelleği kapatılır. CAPTCHA otomatik çözülmez.
- Amazon kayıtları oturum yaşını, denemeler arasındaki süreyi ve son 60 saniyedeki deneme sayısını gösterir. Engel anındaki ölçümler `state.json` içinde son 7 gün için en fazla 1.000 kayıt olarak saklanır. Deneme sayısı başlatılan HTTP çağrılarını/tarayıcı gezintilerini ölçer; tarayıcının ek dosya isteklerini kapsamaz. HTTP yönlendirmeleri ayrıca kaydedilir. Bu ölçümler Amazon'un açıklamadığı engelleme gerekçesini kesin olarak belirlemez.
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
