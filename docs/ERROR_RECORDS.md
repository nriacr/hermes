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

Ortak hata paneli her başarısız varyantı tam adı, seçeneği, doğrudan bağlantısı
ve son hata zamanı ile gösterir. Aynı sorun çalışma hafızası ve birden fazla
teşhis türünde varsa tek satırda gösterilir. Bağlantılar HTTP/HTTPS ile
sınırlıdır; metinler HTML olarak çalıştırılmaz.

Eski kayıtta ayrıntı yoksa bilinen takip bilgisi gösterilir. Ayrıntı sonraki
okumada kaydedilir; eski hata için tahmini ürün/varyant kimliği üretilmez.
Hata başına ürün fiyatı, stok kararı veya ek ağ isteği eklenmez.
