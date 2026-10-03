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
- CAPTCHA ve HTTP 503 hataları bildirim göndermez ve toplu arama erişim uyarısına dahil edilmez; loglarda ve hata görünümünde kalır. Aktif takiplerde bu hatalar sürerken tablo ürün sayısı düşüşü için de bildirim gönderilmez. Okunmuş geçerli fırsatlar, aynı kartın başka varyantında CAPTCHA olsa bile normal şekilde bildirilir.
- Amazon bağlantı ve çerez oturumları servis çalıştığı sürece çevrimler arasında korunur; fiyat/ürün önbellekleri yalnızca ilgili çevrimde geçerlidir. Chromium gerektiğinde aynı anonim profil ve açık tarayıcı sürecini kullanır; servis yeniden başlatıldığında yeni oturum açılır.
- Gerçek doğrulama formu/metni veya HTTP 429/503 geldiğinde o sayfa için tekrar istek, çerez sıfırlama veya başka yöntemle kurtarma yapılmaz. Diğer bağlantı/yanıt hatalarında aynı adres için en fazla bir Chromium denemesi yapılabilir. Sayfadaki bir script veya ürün metninde `captcha` geçmesi tek başına koruma sayılmaz.
- Koruma sonrası tek kontrol tamamlandığında geçerli boş sonuç da kurtarma sayılır; orta/düşük kartlar normal 2/6 saat zamanlamasına döner. Eski koruma kaydı her çevrimde tekrar tarama yaptıramaz. HTTP 503 servis hatası, HTTP 429 istek sınırı ve gerçek doğrulama sayfası loglarda ayrı sınıflandırılır.
- Bir varyantın fiyatı bulunamaması, o sayfadan keşfedilen kardeş ASIN’lerin taranmasını kesmez. Yeni ürün fiyatı yoksa bile varsa ayrı depo teklif listesi okunur. Amazon’un seçili ürün için açıkça bildirdiği stok yokluğu normal `Stokta yok` sonucudur; CAPTCHA, bozuk sayfa veya stokta olan ürünün okunamayan fiyatı operasyonel hata olarak korunur. Stok satırları kartın sonraki kontrol sırası gelene kadar tutulur.
- Fiyat/teklif bulunmayan Amazon ürün varyantı 5 dakika boyunca yeniden istenmez. Bu kısa süreli kayıtta yalnızca başarısız/stoksuz sonuç ve keşfedilmiş ASIN bağlantıları tutulur; başarılı fiyatlar sonraki çevrimlere önbellekten taşınmaz. Diğer stoklu varyantlar kendi öncelikleriyle taranır. Bütün varyantları bu durumda olan bir kartın kontrolü yeniden deneme zamanına kadar ertelenir; sonraki kontrol zamanı her çevrimde ileri itilmez. Kullanıcı kartı düzenlediğinde yeni kontrol yapılabilir. Bu aralık, o varyantta yeni beliren fırsatın fark edilmesini de geciktirebilir. Erişim/CAPTCHA hataları bu kayıtla karıştırılmaz.
- `Amazon Depo içinde … için sonuç bulunamadı` gibi açık arama uyarıları normal `ürün bulunamadı` sonucudur; hata bildirimi oluşturmaz ve stok/bulunamayan sonuçlar tablosunda sorgunun adıyla kalır. Bu uyarıda genel kategori ürünlerine geçilmez; `Tüm Kategoriler içindeki sonuçlar gösteriliyor` başlığının altındaki kartlar da elenir. Açık boş sonuç için yeniden istek en erken 5 dakika sonra yapılır; orta/düşük önceliklerin daha uzun aralıkları korunur. Bağlantı testi aynı durumda kırmızı hata yerine normal sonuç bildirir.
- `Test` sayfasındaki gerçek tarayıcı seçeneği, yalnızca ilgili testte Pi’deki Chromium’u kullanır; masaüstü tarayıcısını açmaz ve takip/bildirim kaydı oluşturmaz. Ana izleyici varsayılan olarak HTTP kullanmayı sürdürür; tarayıcıya geçiş karşılaştırma sonucuna bağlıdır. Chromium ile eşleşen sürücü aynı sistem paketlerinden kurulur ve doğal tarayıcı kimliği kullanılır. Ürün/arama belgeleri her okumada sunucuya doğrulatılır; resim, stil ve betiklerin normal tarayıcı önbelleği korunur. Ağdan doğrulanmayan ana belge fiyat kaynağı olarak kabul edilmez. CAPTCHA otomatik çözülmez.
- `Test` sayfasından 24 saatlik Amazon okuyucu karşılaştırması başlatılabilir: normal taramalarda saatlik `Chromium → mevcut okuyucu → mevcut okuyucu → Chromium` dönemleri tekrarlanır. Ek ürün sorgusu yapılmaz; öncelik, hariç tut, satıcı filtreleri, koruma beklemeleri ve normal fırsat bildirimleri korunur. Süre sonunda mevcut okuyucuya otomatik dönülür. Sonuçlar yalnızca aynı ayarlarla iki yöntemle de ağ üzerinden okunmuş kartları karşılaştırır; CAPTCHA/503, fiyat okunan kontroller, süre, varyant ve depo kapsamı ayrı gösterilir. Mevcut okuyucunun normal tarayıcı yedeği de gerçek istek yöntemleriyle ölçülür. Bu test CAPTCHA’yı önleme garantisi vermez.
- `Aynı sorgu aralığıyla 6 saat karşılaştır`, aynı normal takiplerde iki okuyucunun Amazon istek başlangıçlarına en az 18 saniyelik ortak aralık uygular. İlk üç saatte tarayıcı–HTTP–HTTP, sonraki üç saatte HTTP–tarayıcı–tarayıcı okunur; her yöntem toplam üç saat ölçülür. Üç saat arayla eşleşen dönemler günün saati etkisini tamamen dengelemez. Gerçek aralıklar ayrıca ölçülür; ortak alt sınır eşit trafik veya güvenli hız garantisi değildir. Bekleme ağ/sayfa okuma süresine eklenmez, kart/çevrim süresinde kalır. Saat sınırını aşan veya önceki dönemin yöntemiyle devam eden kontroller saat karşılaştırmasından ayrılır; aynı kart/ayar eşleşir. Aktif test ve kullanıcı ayarları sıfırlanmaz. Süre sonunda veya durdurulduktan sonraki çevrimde HTTP ve normal sorgu ayarlarına dönülür. Ek ürün sorgusu yapılmaz; sorgular bu geçici deneyde daha yavaş olabilir. JSON raporunda ağdan okunmuş kartların istek zamanı, yöntem, aralık, kabul edilmiş ASIN ve kapsam kayıtları yer alır; çerez/HTML/kimlik bilgisi içermez.
- Pi tarayıcısı, fiyat/satıcı ve varyant/depo bölgeleri hazır ve kararlı olduğunda tam sayfa yüklemesini beklemeden okuyabilir. Her bağlantının ilk ve her onuncu hızlı okuması aynı gezintinin tam yüklenmiş haliyle mevcut fiyat/varyant/depo okuyucuları kullanılarak karşılaştırılır. Sonradan değişen fiyat, satıcı, stok, varyant veya depo verisi varsa tam veri kullanılır ve o bağlantı servis oturumu boyunca tam okumaya alınır. Kapsam kontrolü ilave Amazon isteği göndermez; her gelecekteki değişikliği veya bütün ürün ailesinin eksiksizliğini garanti etmez.
- `Test` sayfasındaki `Pi tarayıcısını 1 saat doğrula`, sırası gelen normal takipleri iyileştirilmiş tarayıcıyla ölçer ve süre sonunda mevcut HTTP okuyucuya döner. Aynı sayfanın kapsam kontrolleri, geç veri farkları ve kart süreleri ayrı raporlanır; koruma/önbellek beklemeleri başarı sayılmaz. Tamamlanan önceki karşılaştırma kayıtları yeni doğrulama başlarken arşivlenir. Tüm liste için kalıcı tarayıcı geçişi yapılmaz.
- Pi tarayıcısının aşama süreleri ayrıca loglanır: gezinme, hazır veri kontrol betiği, kararlılık beklemesi, HTML aktarımı, ana belge doğrulaması ve kapsam denetiminin ayrıştırma/bekleme süreleri. Gözlem, imza değişimi ve HTML okuma sayıları da kaydedilir. Bu ölçüm ilave sayfa sorgusu yapmaz; önbellek isabeti yeni okuma sayılmaz. Varyantın normal fiyat/teklif ayrıştırması mevcut varyasyon aşama loglarında ayrıca bulunur. Tarayıcıdaki toplam süre saf ağ gecikmesi değildir.
- Pi tarayıcısı ana belge kimliğini mevcut gezinme olaylarından alır; olay eksikse belge ağacını okur. Aynı gezinmenin son kontrolünde bu kimlik yeniden kullanılır; sonradan başlayan doğrulanmamış gezinme, 503 veya önbellek yanıtı kabul edilmez. Son adres bir kez okunur, sayfa zaman aşımı yalnızca değer veya tarayıcı değiştiğinde ayarlanır. İlk/her onuncu kapsam kontrolü korunur. Doğrulama testlerinde sayfa aşama süreleri kart kaydına da eklenir; eski loglar silinse bile ölçüm korunur. Bu değişiklik hız kazanımını veya CAPTCHA’sızlığı tek başına kanıtlamaz.
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
