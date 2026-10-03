# Değişiklik günlüğü

## 3.0.0

Hermes baştan yeniden düzenlendi. Takip kartların, fiyat geçmişi, bildirim
hafızası, Telegram oturumu, ayar anahtarları ve adresler aynı kalır; veri
dosyalarının biçimi değişmediği için 2.5.48'e geri dönmek mümkündür.

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
