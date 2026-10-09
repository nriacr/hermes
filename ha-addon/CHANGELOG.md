# Değişiklik günlüğü

## 3.15.1

- Amazon Depo teklifleri artık kartların üzerinde de "DEPO" etiketiyle görünür (fırsat kartlarında site adının
  yanında, takipteki küçük kartlarda fiyatın üstünde); eskiden etiket yalnız açılan pencerede vardı.

## 3.15.0

- Tüm sayfalar aynı tasarım dilinde: İstatistik ve "yeniden başlatılıyor" sayfaları da Özet Tablo ve
  Ayarlar gibi koyu, cam görünümlü bölümler, Sora/Inter yazı tipleri ve neon vurgularla çiziliyor
  (kartlar, grafik, site sağlık çubukları, tablolar, hata dönemleri). Veriler ve işlevler aynı.

## 3.14.0

- Ayarlar sayfası Özet Tablo ile aynı görünümde: koyu tema, Sora/Inter yazı tipleri, cam görünümlü bölümler,
  neon vurgulu düğmeler, canlı arka plan. Form alanları, kartlar ve kaydetme akışı aynı; yalnız görünüm değişti.
  İstatistik sayfası şimdilik eski görünümde.
- Hermes logosu önizlemedeki gibi: dönen renkli halka içinde "H".
- Ayrıntı penceresinde "Tarama sıklığı" kısa yazılır ("Her çevrim", "30 dk", "60 dk", "3 saat", "6 saat").
- Fiyat değişimi yüzdesi ilk kayıtlı fiyata göre hesaplanır; pencerede "İlk kayıtlı fiyat" ve takip başlangıcı
  görünür, yüzdenin üzerine gelince tarih de yazar.

## 3.13.0

- Özet Tablo baştan tasarlandı (koyu tema, yeni yazı tipleri ve renkler, hareketli geçişler; telefonda ve
  bilgisayarda aynı düzen). Tablo yerine kartlar: hedef fiyatın altındaki ürünler üstte "Fırsatlar" kartlarında,
  altında Takipte, Stokta yok ve Telegram sekmeleri. Eski tablodaki tüm veri korunuyor.
- Her kartta site rengi, kısa ad, fiyat, fiyat geçmişi çizgisi, geçmişe göre yüzde değişim ("yeni" yeni
  takip) ve son güncelleme zamanı. Ürün adı sığmayınca sondan kısalır. Aynı takibin birden çok sonucu
  (renk, hafıza) kendi başlığı altında toplanır ve ayıran kısımlar küçük etiketlerle gösterilir.
- Karta dokununca ayrıntı penceresi: güncel fiyat, hedefe yakınlık çubuğu, en düşük/en yüksek, satıcı, stok,
  tarama sıklığı (öncelik noktası), son güncelleme, takip başlangıcı ve fiyat geçmişi grafiği (hedef çizgisiyle).
  Pencere ekrana sığar, kaydırma çubuğu çıkmaz.
- Sitelere göre süzme düğmeleri (yalnız ürünü olan siteler). Sayfa kendiliğinden yenilenirken seçili sekme,
  süzgeç ve açık pencere korunur; giriş animasyonu yalnız ilk açılışta oynar.
- Yazı tipleri (Sora, Inter) uygulamanın içinde; panel dış bağlantı kullanmaz. İstatistik ve Ayarlar sayfaları
  değişmedi. Fiyat geçmişi mevcut `hermes.db` kayıtlarından okunur, veri yapısı değişmedi.

## 3.12.2

- Yeniden başlatırken (düğme veya ayar kaydı) logda çıkan yanıltıcı "yeniden başlatılamadı: timed out" satırı
  kalktı: Home Assistant Hermes'i cevap vermeden kapattığı için bu zaman aşımı beklenen durum; yerine
  "Yeniden başlatma isteği Home Assistant'a iletildi; Hermes kapanıyor." yazılıyor. Gerçek hatalar yine yazılır.

## 3.12.1

- Ayarlar'ın altındaki düğme satırına "Hermes'i yeniden başlat" eklendi (Home Assistant içinde ve public
  sayfada aynı). Onaydan sonra Hermes, Home Assistant üzerinden uygulama olarak yeniden başlar; ayarlar
  değişmez, "yeniden başlatılıyor" sayfası Hermes hazır olunca Ayarlar'a döner.

## 3.12.0

- Öncelikler artık adla değil süreyle: "Her çevrim", "30 dk", "60 dk", "3 saat", "6 saat"
  (her çevrimde, 30 dk'da, 60 dk'da, 3 saatte, 6 saatte bir taranır). Yüksek/orta/düşük kalktı.
- Öncelik rengi beş kademe: kırmızı (her çevrim), turuncu (30 dk), sarı (60 dk), sarı-yeşil (3 saat),
  yeşil (6 saat). Özet Tablo satırlarında ve Ayarlar'daki her kartın başında yuvarlak işaret.
- Bu sürüme geçişte mevcut tüm takip kartları en düşük önceliğe (6 saat) geçer; yeni kartlar
  (Ayarlar ve Telegram hızlı ekleme) "Her çevrim" ile başlar. Kart bir sonraki kayıtta yeni değerle yazılır.
- Kartlardaki eski "kontrol aralığı (dakika)" alanı artık kullanılmıyor; sıklığı yalnız öncelik belirler.

## 3.11.0

- Ayarlar sayfasının en altında "Zamanlama" bölümü: "Çevrim aralığı", "Bekleme süresi min" ve
  "Bekleme süresi maks" (Home Assistant yapılandırmasındaki interval_seconds ve request_delay_*
  ayarları). Değişiklikleri uygula ile diğer ayarlarla birlikte kaydedilir; sınırlar Home Assistant
  ile aynı, min değer maks değerden büyük olamaz.
- Ayarlar'ın altındaki düğmeler tek satırda: Pushover testi, Bildirim Sıfırla, Min/Maks Sıfırla,
  İstatistik (telefonda 2x2).
- Bağlantı testi ("Test" düğmesi, sayfası ve Amazon'un yalnız-tarayıcı okuma seçeneği) tamamen kaldırıldı.

## 3.10.0

- İstatistik sayfasında "Hata dönemleri": bir sitenin birbirine 15 dakikadan yakın engel ve hataları tek
  satırda (saat aralığı, kaç okuma, hangi tür, en sık neden, o sıradaki Pi yükü). 65 ayrı hata yerine
  "19:01–19:56 · Amazon · 65 okuma · Zaman aşımı" görünüyor.
- Başarısız her okuma veritabanına hata metniyle ve o anki işlemci kullanımı ile boş bellekle kaydediliyor;
  aynı bilgi log satırına da yazılıyor. Pi logu kısa olsa da neden kaybolmuyor.
- Tarayıcının sayfa yükleme zaman aşımı artık "Sayfa okunamadı" değil "Zaman aşımı" olarak sayılıyor.
- Hata sayısı sensörü ve Özet Tablo'daki hata kartı, Hermes yeniden başlamadan önce kalan hataları
  göstermiyor; ürün yeniden okunup yine hata verirse hemen görünür.
- İstatistik sayfasının altında "Hata kayıtlarını sıfırla" düğmesi: onay sorusundan sonra tüm engel ve hata
  okumalarını siler, sayaçlar sıfırdan başlar. Başarılı okumalar, sitelerin ağ isteği sayıları (Amazon'un
  bekleme süresi buradan hesaplanıyor) ve ürünlerin güncel hata durumu yerinde kalır.

## 3.9.5

- Özet Tablo'da "Güncel fiyat" ve "Hedef" sütunları artık tüm tablolarda aynı yerden başlıyor:
  Stokta Olmayanlar tablosundaki Hedef, fiyat tablosundaki Güncel fiyat ile aynı hizada.
  Bilgisayar görünümünde sütun genişlikleri sabit; telefon görünümü değişmedi.

## 3.9.4

- "Stokta Olmayanlar" tablosuna "Son güncelleme" sütunu eklendi: ürünün stokta olmadığının en son
  ne zaman doğrulandığı (ör. "12 dk önce", "2 gün önce") görünüyor. Ingress ve herkese açık
  yüzeyde aynı; eski kayıtlarda süre, kayıtlı son stok/okuma zamanından alınıyor.

## 3.9.3

- Pushover testi, Bildirim Sıfırla ve Min/Maks Sıfırla düğmeleri Özet Tablo'dan kalktı; Ayarlar
  sayfasının en altında (onay soruları aynı). Sonuç mesajı Ayarlar sayfasında görünüyor.
- Ayarlar'daki alt düğmelerden "Özet Tablo" kaldırıldı; Hermes logosu zaten oraya gidiyor.

## 3.9.2

- Üst çubukta artık yalnız Hermes logosu ve yanında dişli simgesi var; logo Özet Tablo'ya,
  dişli Ayarlar'a gidiyor (her sayfada, ingress ve herkese açık yüzeyde aynı).
- Özet Tablo, İstatistik ve Test düğmeleri üst çubuktan kalktı; Ayarlar sayfasının en altında duruyor.

## 3.9.1

- Amazon'da istekler arası rastgele bekleme (ayardaki min/max) artık iki isteğin arasındaki süre
  olarak sayılıyor: önceki sayfanın işlenmesi (Pi'de ortalama 3,5 sn) bu süreye dahil. Eskiden bekleme,
  işlemenin üstüne ekleniyordu ve iki istek arası ortalama 6 sn oluyordu. İstekler arasındaki boşluk
  hiçbir zaman çekilen rastgele değerin altına inmiyor.

## 3.9.0

- Amazon'da kırmızı kartın tüm sayfaları (ana sayfa, ikinci el listesi ve her varyant) her arama
  turunda bir kez okunuyor. Tur, listedeki ilk üründen başlayıp son ürünü bitirip yeniden ilk
  ürüne dönene kadar geçen süre; sarı (saatte bir) ve yeşil (3 saatte bir) kartlar sıraları
  geldiği turu uzatıyor. Hızlı ana sayfa döngüsü kalktı: ana sayfalar isteklerin yarısını
  alıyor, varyantlar ise 8-14 dakika bekliyordu. Her kırmızı sayfa şimdi ~5-6 dakikada bir
  güncelleniyor (1-4 sn bekleme ile).
- Tek sayfalık ürünler (varyantsız) Depo şeridinde turda bir kez, varyantlı aileler tarama
  şeridinde okunuyor; iki şerit sırayla istek atıyor. Varyantların eski fiyatını hafızadan
  gösterme kalktı: her tur hepsi yeniden okunuyor.
- Ölçüm satırındaki "ana sayfa aralığı" artık "kart okuma aralığı (tur)": aynı kartın iki okuması
  arasındaki süre.

## 3.8.3

- 3.8.1'de kırmızı kartlar her turda okunmaya başlayınca Depo şeridi istek sırasını sürekli
  kazanıyor ve varyant taraması 5 dakika yerine 10-39 dakikada bir yapılabiliyordu (iPhone 18
  Pro / Pro Max varyantları 14-18 dakika önce güncellenmiş görünüyordu). İki şerit artık
  sırayla istek atıyor; biri doluyken diğeri en fazla bir istek bekliyor.
- Amazon'daki 5 saniyelik sabit istek aralığı kaldırıldı. Ayarlardaki bekleme min/max değeri
  artık her Amazon isteğinden önce (ürün, varyant, ikinci el listesi, arama detayı)
  rastgele ve ondalıklı olarak uygulanıyor; önbellekten gelen sayfa beklemiyor. Başlangıçta
  ve engelden sonra bekleme 2 ya da 4 katına çıkıyor. Bağlantı testi de aynı beklemeyi kullanıyor.
- Kayan pencere istek sınırı 500'den başlıyor, 700'e kadar çıkabiliyor (sabit aralık kalkınca
  istek hızı arttığı için).

## 3.8.2

- Amazon'da kırmızı bir kartın kendi sayfasında fiyat ya da teklif yoksa (stokta değil), sayfa
  artık 5 dakika bekletilmeden her arama turunda yeniden kontrol ediliyor; stoğa dönüşü en geç
  bir tur sonra görülüyor. Sarı ve yeşil kartlarda ve varyantlarda 5 dakikalık sınır aynen duruyor.

## 3.8.1

- Amazon'da kırmızı kartların sabit 60 saniyelik süresi kalktı: kırmızı kartlar her arama
  turunda okunuyor. Tur, listedeki ilk üründen başlayıp son ürünü bitirip yeniden ilk ürüne
  dönene kadar geçen süre; normalde kırmızı kartları okuma süresi kadar, sarı ya da yeşil
  kartların sırası geldiği turlar daha uzun. Çok az kırmızı kart olan listelerde Amazon'u
  yormamak için en sık 20 saniyede bir okunuyor.
- Engel sonrası otomatik hız kademesi artık istekler arası bekleme süresini de uzatıyor
  (kırmızı kartların süreleri sabit olmadığı için yavaşlama buradan geliyor).

## 3.8.0

- Yeni site: Togg konfigüratörü (configurator.togg.com.tr). Kartın `name` alanındaki
  model (örneğin `T10X V2 RWD Uzun Menzil`) sayfanın model listesinde varsa stokta,
  yoksa `Stokta Olmayanlar` bölümünde görünür; stokta olmaması hata sayılmaz. Model
  listeye girdiğinde tek bir stok bildirimi gelir. Okuma sıklığı kartın öncelik
  kategorisine göre; tüm Togg kartları döngü başına tek istek paylaşır. Diğer sitelere
  dokunulmadı.

## 3.7.0

3.6'dan beri 41 saat ve 15.211 Amazon isteğinde hiç engel olmadı; istek hızı dakikada
4,0'dan 6,2'ye çıktığı halde. Engellerin sebebi hacim değil, Hermes'in karışık tarayıcı
kimliğiymiş (3.6.0'da düzeltildi). Bu sürüm hızı önceliğe çeviriyor.

- Öncelik her zaman kategoriden okunuyor, fiyatın hedefe yakınlığına bakılmıyor (3.6'daki
  "hedefe %15 yakın" kuralı kaldırıldı; hedeften uzak kırmızı kartlar 10 dakikada bir
  okunuyordu). Amazon'da kırmızı kartlar 60 saniyede, sarı kartlar saatte, yeşil kartlar
  3 saatte bir okunuyor. Diğer sitelerde sarı saatte, yeşil 3 saatte bir (eskiden 2 ve 6
  saat), kırmızı yine her çevrimde. Ayarlardaki öncelik yazıları güncellendi.
- Otomatik hız kademesi: Amazon engel verirse önce kısa bir mola (5 → 10 → 20 → 30 dakika),
  ardından tüm kategorilerin okuma aralığı 2 katına (ikinci dalgada 4 katına) çıkıyor; her
  10 dakika engelsiz geçince bir kademe gevşiyor ve kimse dokunmadan eski hıza dönüyor.
  Eski "engelden sonra bir saat yarım hız" kalktı; yavaşlatan yalnızca mola ve kademe.
- Kayan pencere istek sınırı 400'den başlıyor, 600'e kadar çıkabiliyor (300 / 500 idi);
  kırmızı kartların 60 saniyelik ritmi eski sınıra sığmıyordu. Eski erişim dosyası sıfırlanır.
- Değişmeyenler: varyant taraması kırmızı kartlarda 270 saniye, tarayıcı kimliği, yeni
  ziyaretçiyle dönüş, engel dalgası günlüğü.

## 3.6.1

- Engelden sonraki 15 dakikalık istek molasında gönderilmeyen bir istek artık yeni bir
  engel sayılmıyor; mola yalnız kalan süre kadar uzuyor, mola basamağı (15 → 30 → 60)
  yükselmiyor. 3.6.0'a geçişte eski kısa mola istek molasından önce bittiği için bu
  durum molayı bir kez 60 dakikaya çıkarmıştı.

## 3.6.0

4 Ekim günlüğündeki 3.130 Amazon isteği ve 60 engel incelendi. Engel sayfaya değil
ziyaretçiye konuyor: engelden hemen sonraki 60 isteğin 43'ü de engellendi. Bu sürüm
bunun için Amazon'a daha az ve daha tutarlı gidiyor.

- İlk engelde tüm Amazon mola veriyor (15 → 30 → 60 dakika). Önceden yalnız engellenen
  kart dinleniyor, diğer kartlar okunmaya devam ettiği için bir dalgada 5–8 engel
  toplanıyordu. Engelden sonra 15 dakika hiç istek gönderilmiyor; diğer şeritte süren
  okuma da bir sonraki isteğinde duruyor. Mola sürerken gelen ikinci bir engel molayı
  uzatmıyor, mola öncesi başlayıp mola sırasında yanıt alan bir okuma da molayı bitirmiyor.
- Hermes kendini tek ve tutarlı bir tarayıcı olarak tanıtıyor: güncel Chrome 146 profili
  (TLS ve başlık sırası), Linux, Türkçe. "Sayfayı zorla yenile" başlıkları ve çelişkili
  "nereden geldim" bilgisi kaldırıldı.
- Moladan sonra Amazon'a yeni bir anonim ziyaretçi olarak dönülüyor; işaretlenen
  ziyaretçinin çerezleri bırakılıyor (yeniden başlatmada da geri yüklenmiyor).
- Yalnız hedefe %15 yakın ya da Depo teklifi olan kartların ana sayfası ~100 saniyede bir
  okunuyor; diğerlerininki 10 dakikada bir. Varyant taraması değişmedi (~270 sn).
- Her engel dalgası için günlükte tek satır: günün kaçıncı dalgası, engel türü (captcha
  formu, HTTP durumu), son 1 saatteki istek sayısı ve önceki engelden beri geçen süre.
  Ölçüm satırı günün engel dalgası sayısını da gösteriyor.

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
