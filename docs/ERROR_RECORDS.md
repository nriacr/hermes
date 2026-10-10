# Ürün ve varyant hata kimliği

4.0.1'de okuyucu, başarısız ürün seçeneğinin `product_title`, `product_url`,
`variant` ve `reason` alanlarını `WatchRead.error_details` içinde döndürür.
Başlık aynı ürün sayfasından alınır. Başarılı kardeş varyantın adı veya
kapasitesi hatalı ürüne kopyalanmaz; bilinmeyen başlık açıkça belirtilir.
Önbellekten tekrar kullanılan başarısız sayfa da aynı kimliği korur.

İzleyici site/takip bilgisi ve bu hataları mevcut olayın `context` alanında
saklar. Eski veritabanına sütun kayıtlar silinmeden eklenir. Metinler sırları
temizlendikten sonra JSON'a çevrilir; temizleme JSON yapısını bozmaz.
Çözülen olaylar mevcut kurtarma akışıyla kapanır.

Ortak hata paneli her başarısız varyantı **kısa ürün adı · varyant · kısa hata türü · ürün linki**
olarak tek satırda gösterir. Tam başlık ve neden veritabanında korunur.
Aynı sorun çalışma hafızası ve teşhis kaydında varsa tek satırda gösterilir.
Bağlantılar HTTP/HTTPS ile sınırlıdır; metinler HTML olarak çalıştırılmaz.

4.1.0 ile olaylar kendi ürün URL kimliğine göre ayrı tutulur. Tamamlanan kısmi
bir taramada artık hata vermeyen varyantlar kapanır; engel yüzünden yarıda kalan
taramada ziyaret edilmeyen varyantın iyileştiği varsayılmaz. Yeniden ortaya çıkan
bir sorun eski kapalı dönemi silmez. Açık sorunlar yeniden başlatmada korunur.
İstatistikteki **Sorun geçmişi** açık/kapalı durum, tekrar sayısı, ilk/son zaman
ve takip kartının son tam okumasını gösterir. Eski kayıtta bu bilgi yoksa tahmin edilmez.

Yeni okumalar `ok`, `stock`, `empty`, `partial`, erişim/okuma hatası veya
`interrupted` olarak ayrılır. Kısmi okumalar başarı sayılmaz; kapanışla durdurulan
okumalar istatistik başarısının paydasına katılmaz. 4.1.0 öncesindeki `ok`
kayıtlarının kısmi olup olmadığı tahminle değiştirilmez; ilk günün oranı bu
eski kayıtları içerir. Kesin %100 yalnız tüm sayılan okumalar tam başarılıysa gösterilir.

En az üç okumada, en az 10 dakika süren ürün/varyant sorunları Home Assistant
kalıcı uyarısına dönüşür; iyileşme uyarıyı kaldırır. Aynı varyantın tekrarlanan
stdout hata satırları 5 dakikalık özetlerle sınırlandırılır; her okumadaki
teşhis kanıtı veritabanında kalır. Yeni hata ve iyileşme hemen kaydedilir.

Eski kayıtta ayrıntı yoksa bilinen takip bilgisi gösterilir. Ayrıntı sonraki
okumada kaydedilir; eski hata için tahmini ürün/varyant kimliği üretilmez.
Hata başına ürün fiyatı, stok kararı veya ek ağ isteği eklenmez.

Canlı kurulum kanıtı: `/runtime` içindeki `current_process_completed_jobs` ve
`current_process_successful_jobs` yalnız mevcut açılış ve sürümün tamamlanan
okumalarını sayar. Önceki açılışın işleri veya kapanışla kesilen iş yeni
kurulumun okuma kanıtı sayılmaz. Eski toplam sayaçlar uyumluluk için korunur.
