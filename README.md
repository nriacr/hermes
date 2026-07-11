# Hermes

Hermes, Home Assistant üzerinde çalışan çok siteli fiyat ve Telegram fırsat takip add-on'udur.

Takip edilen alanlar:
- `takip_edilenler`: tek kayıt altında en fazla 5 ürün veya arama linki
- Hermes linkten siteyi ve link tipini otomatik algılar
- Ürün linklerinde `name` boş bırakılabilir; Hermes ürün adını linkten okur. Arama linklerinde `name`, aranacak keyword olarak zorunludur.
- Telegram kanalları: keyword ve exclude keyword tabanlı fırsat bildirimi
- Arama bağlantılarında en fazla 60 sonuç otomatik taranır.

Bildirimler Pushover üzerinden gönderilir.  
Ingress paneli üzerinden durum, özet tablo ve test bildirimi yönetilebilir. İsteğe bağlı public panel; güvenli bir token ve ters proxy/tünel ile dışarıdan da kullanılabilir.

## Home Assistant Repository

Home Assistant > Add-on Store > Repositories alanına:

`https://github.com/nriacr/hermes`

ekleyerek kurulabilir.

Detaylı kullanım, veri dosyaları ve geliştirme kontrolleri için [add-on kılavuzuna](ha-addon/README.md) bakabilirsin.
