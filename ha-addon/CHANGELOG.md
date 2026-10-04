# Değişiklik günlüğü

## 3.5.2

- Depo şeridi artık varyant taramasından gerçekten bağımsız. Taraması 270 saniyeden
  eski olan (tarama şeridi pencere sınırında bekliyor olsa bile) bir kartın ana
  sayfasını da okuyor; eski varyantların fiyatlarını kendi hafızasından, gerçek okunma
  zamanıyla gösteriyor. Varyantsız ürünlerin tüm sayfası da Depo şeridinde okunuyor.
  Şerit, turun başında sırası gelmemiş kartlara da bakıyor; böylece uzun bir tur
  boyunca da her kartın ana sayfası ~100 saniyede bir okunuyor (3.5.1'de 39 dakikada
  yalnız 14 depo isteği vardı).
- Depo payı artık boşuna ayrılmıyor: tarama şeridi, Depo'ya son 35 dakikada kullandığı
  kadarını (en az 12 istek, en çok sınırın %28'i) bırakıyor. Eskiden Depo kullanmasa da
  sınırın %28'i boş tutuluyor ve tarama şeridi 151/151'de bekliyordu.
- Aynı turda yeniden okunan bir kart tablodaki eski satırlarının yerine geçiyor; eski,
  daha düşük bir fiyat yeni okunan yüksek fiyatın önüne geçmiyor.
- Erişim molasında yalnız Depo şeridinin okuduğu kartlar tablodan kaybolmuyor ve mola
  satırı dakikada bir yazılmaya devam ediyor.
- Ölçüm satırı tarama şeridinin sınırını ve Depo şeridinin son 35 dakikadaki istek
  sayısını gösteriyor.

## 3.5.1

- Yeniden başlatma Amazon'un istek sayımını sıfırlamıyor: açılışta son 35
  dakikanın istekleri (ve son 1 saatin istek/engel sayısı) veritabanından geri
  yükleniyor. Önceden açılıştan hemen sonra gelen bir engel, yalnız açılıştan
  beri sayılan istekleri (ör. 240 yerine 47) eşik olarak kaydediyordu.
- "Amazon ölçüm" satırı artık uzun bir varyant turu sürerken de 10 dakikada bir
  yazılıyor; önceden yalnız tur başında yazıldığı için 20 dakikayı aşan
  boşluklar oluyordu.
- Bekleme süreleri, engel kuralları, istek sınırının kuralları ve fiyat okuma
  değişmedi.

## 3.5.0

- Amazon'da Depo şeridi ayrıldı. Ürün sayfası ve ikinci el listesi okumaları artık
  varyant taramasından bağımsız, kendi iş parçacığında dönüyor: uzun bir varyant
  taraması sürerken bile her ürünün ana sayfası ~100 saniyede bir yeniden okunuyor.
  Tarama kendi şeridinde kendi hızında sürüyor. Aynı kart aynı anda yalnız bir
  şeritte okunur.
- İki şerit tek istek bütçesini paylaşır: istekler tek tek ve Depo önce gider, kayan
  pencere ortaktır (Depo hepsini kullanabilir, tarama Depo'nun henüz kullanmadığı
  %28'lik payı hariç tutar ve Depo yer beklerken kenara çekilir), bir engel iki
  şeridi de durdurur, karantina ve iki farklı sayfa kuralı aynen geçerlidir.
- Depo şeridi her okumada sayfayı yeniden indirir; aynı turda taramanın daha önce
  indirdiği sayfa ona asla önbellekten verilmez.
- Ölçüm satırına ana sayfa aralığı (medyan/p90) ve şerit başına istek sayısı eklendi.

## 3.4.0

İstatistik sayfası baştan tasarlandı.

- Merkezdeki ölçü artık "kontrol sıklığı": yüksek öncelikli bir ürün tipik
  olarak kaç dakikada bir okunuyor. Eski "çevrim süresi", site sıraları ve
  öncelikler yüzünden 1 saniye ile 19 dakika arasında oynadığı için
  kaldırıldı.
- Üstte dört kutu: kontrol sıklığı, son tur, başarı oranı, engel ve hata.
- Tek bir "Son 24 saat / Son 7 gün" düğmesi tüm sayfayı değiştiriyor.
- Grafik: saat ya da gün başına bir çubuk; çubuğun üstünde o dilimdeki engel
  ve hata sayısı.
- Her site için bir kart: başarılı / engel / hata renk çubuğu, kontrol
  sıklığı, okuma süresi, son okuma; Amazon'da ağ isteği dökümü.
- Engel ve hata türleri tablosu: captcha, 503, 429, zaman aşımı, bağlantı
  hatası, sayfa okunamadı, diğer; site başına sayı ve en son ne zaman
  görüldüğü. Bunun için her okumada hata türü ve hangi kartın okunduğu da
  kaydediliyor (önceki kayıtlarda bu bilgi boş).
- Günlük geçmiş en altta, kapalı duruyor. Tek tek tur listesi kaldırıldı.
- Telefonda kutular ikişerli, grafik yana kaydırılabiliyor.

## 3.3.1

- Tek bir sayfanın engeli artık tüm Amazon'u durdurmuyor. Tüm Amazon yalnızca iki
  FARKLI sayfa art arda engellenirse mola veriyor (3 → 6 → 12 → 20 dk). Bir sayfa
  tek başına engellenirse ilk engelde yalnız o turdaki okuması bitiyor, 6 saat içinde
  ikinci engelde 30 dk, üçüncüde 60 dk yalnız o kart dinleniyor; diğer kartlar
  okumaya devam ediyor. Engellenen kart tablodan kaybolmuyor, son fiyatı okunma
  zamanıyla kalıyor. Yakın zamanda engellenmiş kart turda en sona sıralanıyor; böylece
  moladan sonraki yoklama cevap veren bir sayfaya gidiyor.
- Pencere sınırı engel başına düşmüyor: yalnız ilk engelde bir kez %85'e iner (en az
  200), sonraki engeller yalnız eşiği kaydeder; engelsiz her saatte %5 yeniden
  yükselir (en çok 500). 3.3.0'ın gece düşürdüğü sınır (119) açılışta 300'e sıfırlandı.
- Depo şeridi (ürün sayfası okumaları) pencere sınırının %20 üstüne kadar okuyabilir;
  varyant taramaları yüzünden pencere dolsa da Depo okumaları yavaşlamaz.

## 3.3.0

- Amazon engeli azaltıldı. İlk CAPTCHA/503'te artık tüm Amazon kartları durur
  (kart başına değil); mola 3 → 6 → 12 → 20 dakika, her molanın sonunda tek bir
  yoklama isteği atılır. Yoklama engellenirse bir üst basamağa çıkılır, bir
  okuma başarılı olunca merdiven baştan başlar. Mola sürerken kartlar son
  fiyatlarını göstermeye devam eder.
- Kayan istek penceresi: son 35 dakikada en çok 300 Amazon isteği. Sınır, engelsiz
  her saatte (pencere sınıra yaklaşmışsa) %5 yükselir (en çok 500); engel
  gelince o anki pencere sayısı "eşik" olarak kaydedilir, sınır eşiğin %85'ine
  iner ve orada sabit kalır. Engelden sonra ilk saat, ayrıca her açılışta ilk
  10 dakika yarım hızla (istek aralığı iki katı) çalışılır. Sınır, eşik ve yarım
  hız süresi yeniden başlatmada kaybolmaz (`/data/amazon_access.json`).
- Amazon çerezleri `/data/amazon_cookies.json` dosyasında saklanır; yeniden
  başlatma Amazon'a yeni bir ziyaretçi gibi görünmez.
- Ürün sayfası ve ikinci el (Depo) listesi her ~100 saniyede, varyant taraması
  ~270 saniyede bir okunur. Taramalar arasında diğer varyantların son fiyatları
  gerçekte okundukları zamanla gösterilir; bu fiyatlar yeni fiyat noktası ya da
  yeni bildirim sayılmaz. Hızlı okumalar uzun taramalardan önce yapılır.
- Hariç tut filtresine takılan varyant sayfası bir kez okunup komşuları 15–45
  dakika hatırlanır; sayfa her turda yeniden istenmez (loglarda isteklerin
  %43'ü 1 TB / 2 TB sayfalarıydı).
- Log: 10 dakikada bir `Amazon ölçüm:` satırı (pencere, sınır, eşik, anlık
  istek/dk, son 60 dk istek ve engel, Depo kontrolü ve doğrulanan teklif sayısı).

## 3.2.3

- Sayfa betiklerinin adresine sürüm eklendi. Tarayıcı eski sürümün betiğini
  bir gün boyunca önbellekten kullanmaya devam ediyordu; bu yüzden İstatistik'te
  bir güne dokunmak çevrimleri yüklemeyebiliyordu.

## 3.2.2

- İstatistik sayfası artık yalnız gün özetlerini gönderiyor; bir günün tek tek
  çevrimleri o güne dokununca yükleniyor (JavaScript yoksa "Tüm çevrimler"
  bağlantısı ayrı sayfada açar). Grafik aynı görünüyor ama her yatay nokta
  için yalnız en uzun çevrimi çiziyor. Sayfa çok daha hızlı açılıyor.
- Hiçbir kart okunmayan turlar (sırası gelen kart yok ya da hepsi Amazon
  koruma beklemesinde) artık çevrim istatistiğine girmiyor; Özet'teki çevrim
  süresi son gerçek turu gösteriyor. Eski sürümlerden kalan bu tür kısa
  kayıtlar veritabanından bir kez temizlendi (`cycle_history.json`'daki
  asılları duruyor).

## 3.2.1

- Amazon kartları koruma beklemesindeyken geçen kısa turlar da loga artık
  başlık yazmıyor; başlık yalnız gerçekten bir kart okunduğunda çıkıyor.
  Bekleme satırı ("... atlandı (captcha) | kalan=...") dakikada bir aynen
  görünmeye devam ediyor.

## 3.2.0

- Fiyat geçmişi ve çevrim istatistikleri artık bir SQLite veritabanında
  (`hermes.db`) tutuluyor. İlk açılışta mevcut geçmiş otomatik aktarılıyor;
  eski JSON dosyaları silinmiyor ve `state.json` aynen yazılmaya devam ediyor,
  böylece eski sürüme dönmek mümkün. Fiyat her değiştiğinde bir kayıt
  ekleniyor (ileride ürün başına fiyat grafiği için).
- İstatistik sayfasında yeni "Site ölçümleri" bölümü: son 24 saat ve 7 gün için
  her sitede kaç okuma yapıldığı, kaçının başarılı olduğu, kaçında koruma
  (captcha/503/429) veya hata çıktığı ve tipik süre. Amazon için her isteğin
  ayrı dökümü (captcha, 503, 429, tarayıcı yedeği).
- Boşta geçen kısa turlar loga artık yazılmıyor; fiyat tablosu yalnız
  değiştiğinde ya da en fazla yarım saatte bir yazılıyor. Hata ve koruma
  satırları aynen kalıyor.

## 3.1.2

- İstatistik sayfası 5 dakikada bir güncelleniyor (yedi günlük özet olduğu
  için daha sık gerekmiyor); Özet sayfası 15 saniyede bir. Grafik dakikaya
  sabitlendi, böylece değişmeyen istatistik yeniden gönderilmiyor.

## 3.1.1

- Canlı güncelleme veri değişmediyse sayfayı yeniden göndermiyor; İstatistik
  sayfası birkaç MB olduğu için bu önemli. Büyük sayfalar sıkıştırılarak
  gönderiliyor, telefonda daha az veri harcanıyor.

## 3.1.0

- Özet ve İstatistik sayfaları artık her dakika baştan yüklenmiyor; veriler
  15 saniyede bir yerinde güncelleniyor. Açtığın gruplar açık kalıyor, sayfa
  kaydırdığın yerden oynamıyor. Sekme arka plandayken istek gitmiyor. Hem
  Home Assistant içindeki panelde hem public adreste aynı şekilde çalışıyor;
  JavaScript kapalıysa eskisi gibi dakikada bir yenileniyor.
- Hepsiburada ayrıştırıcısı (tek dosyada 1.431 satır) konu başlıklarına göre
  beş dosyaya bölündü: ortak parçalar, fiyatlar, varyantlar, arama ve ürün
  sayfası. Davranış birebir aynı; bakım ve hata ayıklama kolaylaştı.

## 3.0.1

- Home Assistant sensörleri yalnızca içerikleri değiştiğinde güncelleniyor;
  `sensor.hermes_son_tur` en fazla dakikada bir yazılıyor. Böylece boşta geçen
  kısa çevrimler Home Assistant veritabanına her birkaç saniyede kayıt yazmıyor.

## 3.0.0

Hermes baştan yeniden düzenlendi. Takip kartların, fiyat geçmişi, bildirim
hafızası, Telegram oturumu, ayar anahtarları ve adresler aynı kalır; veri
dosyalarının biçimi değişmediği için 2.5.48'e geri dönmek mümkündür.

**Yeni**

- Her site kendi sırasında kontrol ediliyor. Amazon yavaşladığında veya
  korumaya girdiğinde Hepsiburada, Zara ve diğerleri artık beklemiyor. Her
  sitenin (Amazon dahil) tek bir sırası ve kendi istek aralığı var.
- Home Assistant'ta Hermes sensörleri: `sensor.hermes_firsat_sayisi`
  (fırsatların listesiyle), `sensor.hermes_son_tur`, `sensor.hermes_hata_sayisi`.
  Her fırsat bildiriminde `hermes_firsat` olayı tetikleniyor; HA
  otomasyonlarında ve panolarında kullanılabilir. Pushover aynen çalışıyor.

**Düzeltmeler**

- Panel ve izleyici artık tek uygulama. "Bildirim Sıfırla" ikinci bir tarama
  başlatıp durum dosyasına aynı anda yazmıyor; sıfırlama taramalar arasında
  uygulanır ve hemen yeni bir kontrol başlar.
- Ayarlarda bir hata olduğunda Hermes kapanmıyor: panel açık kalır, hata ana
  ekranda görünür ve Ayarlar'dan düzeltilebilir.
- Beklenmeyen bir hata tüm izlemeyi durdurmuyor; o çevrim kaydedilip sonraki
  çevrimde devam edilir. Home Assistant watchdog'u Hermes donarsa onu yeniden
  başlatır.
- Pushover anlık olarak cevap vermezse okunan fiyat artık hata sayılıp
  tablodan silinmiyor; bildirim bir sonraki okumada yeniden denenir.
- Ben Gurme'de "stok geri geldi" bildirimi gönderilen turda ürünün fiyatları
  da tabloya yazılıyor (önceden o tur boş kalıyordu).
- Telegram bağlantısı koparsa dinleme kendiliğinden yeniden bağlanıyor.
- Ayarlar'da "Kayıtlı Mesajlar'dan hızlı takip ekleme" kutusunun işareti
  kaldırıldığında özellik gerçekten kapanıyor.
- Dosyalar kesintiye dayanıklı yazılıyor, public panel anahtarı güvenli
  karşılaştırılıyor, kapanışta Chromium temiz kapatılıyor.

**Yapı**

- Her mağaza kendi okuyucusunda hem indirme hem ayrıştırma yapıyor; ana akış
  mağazadan bağımsız.
- Ingress ve public panel tek ortak yönlendiriciden çalışıyor; bütün sayfalarda
  aynı menü var.
- Amazon ölçüm/deney kodları (okuyucu karşılaştırmaları, tarayıcı erken okuma
  ve kapsam denetimi, aşama süresi ölçümleri) kaldırıldı. Amazon'un koruma ve
  bekleme kuralları aynen korunuyor; tarayıcı yedeği sayfanın tam yüklenmesini
  bekliyor.
