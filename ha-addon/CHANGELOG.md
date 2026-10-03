# Değişiklik günlüğü

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
