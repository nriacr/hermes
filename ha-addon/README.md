# Hermes

Hermes, Home Assistant üzerinde çalışan çok siteli ürün ve Telegram fırsat takip add-on'udur.

## Özellikler

- Takip edilenler: tek kayıt altında en fazla 5 link izleme
- Linkten otomatik site algılama: Amazon, Hepsiburada, Trendyol, Network, Beymen Club, Nordbron, Zara, H&M, Ben Gurme
- Ürün ve arama linklerini aynı takip kaydı içinde karışık kullanabilme
- Arama linklerinde, takip adını keyword kabul ederek eşleşen sonuçlar arasından en iyi fiyatı seçme
- Arama linklerinde sabit olarak en fazla 60 sonuç tarama
- Pushover bildirimleri
- Telegram fırsat/indirim kanalı dinleme
- Telegram keyword ve exclude keyword takibi
- Telegram Kayıtlı Mesajlar'dan hedef fiyat sorarak hızlı takip ekleme
- 24 saat tekrar bildirimi kontrolü (`notify_once_in_24H`)
- Aktif/pasif kural yönetimi (`active`)
- Log tablosu ve özet dosyası (`/data/latest_price_summary.json`)
- Ingress paneli üzerinden durum ekranı ve Pushover test butonu
- Token ile korunan isteğe bağlı public panel ve public ayarlar ekranı
- Ayarlar ekranında tüm takip ve Telegram değişiklikleri alttaki `Değişiklikleri uygula` ile tek seferde kaydedilir

## Konfigürasyon

`config.yaml` içindeki `options` ve `schema` alanları Home Assistant UI ile uyumludur.

Ana alanlar:

- `interval_seconds`
- `request_delay_min_seconds`
- `request_delay_max_seconds`
- `pushover_user_key`
- `pushover_api_token`
- `takip_edilenler[]`
- `telegram_enabled`
- `telegram_saved_messages_enabled`
- `api_id`
- `api_hash`
- `phone_number`
- `verification_code`
- `session_name`
- `channels[]`
- `keywords[]`
- `exclude_keywords[]`

Takip kartlarında `max_items_to_scan` ayarı artık kullanılmaz. Hermes her arama linkinde en fazla 60 sonucu tarar; eski kayıtlardaki bu alan varsa güvenli biçimde yok sayılır.

Teknik istek zaman aşımı Hermes içinde yönetilir ve günlük kullanımda config ekranında görünmez.

Takip edilenlerde ayrıca site seçilmez. Hermes, girilen linklerden uygun siteyi ve link tipini otomatik algılar. Ürün linkiyse ilgili sitenin ürün okuyucusunu, arama linkiyse ilgili sitenin arama okuma mantığını çalıştırır.

Bir takip kaydı örneği:

- `name`: `Samsung Galaxy Tab S10 FE+`
- `target_price`: `17600`
- `url_1`: Amazon ürün veya arama linki
- `url_2`: Hepsiburada ürün veya arama linki
- `url_3`: başka bir desteklenen site linki

Her link ayrı kontrol edilir ve özet tabloda ayrı satır olarak görünür; ancak ad, hedef fiyat, bildirim ve aktif/pasif ayarı aynı takip kaydından gelir.

Hepsiburada için ürün detay linki yerine arama linki kullanılması önerilir. Örnek:

`https://www.hepsiburada.com/ara?q=Samsung+Galaxy+Tab+S11+Ultra+12GB+256GB`

Bu yapıda Hermes, arama sonuçlarındaki ürün kartlarını okur; taksit ve kupon fiyatlarını eleyerek gerçek ürün fiyatlarını karşılaştırır ve en düşük fiyatı dikkate alır.

Telegram dinleme varsayılan olarak kapalıdır. Aktif edildiğinde Hermes config'teki kanalları dinler; mesajda keyword geçer ve exclude keyword'e takılmazsa Pushover bildirimi gönderir. `telegram_saved_messages_enabled` açıksa Kayıtlı Mesajlar'a gönderilen desteklenen ürün bağlantısı için Hermes önce hedef fiyatı sorar. Uygulamaların gönderdiği kısa bağlantılar da gerçek desteklenen ürün adresine çevrilir. Doğrudan ürün linkinde fiyat yanıtı kaydı oluşturur; arama linkinde ek olarak ürün adını ister. Grup ve beden daha sonra Hermes Ayarlar ekranından eklenebilir. İlk Telegram girişinde kod telefona gönderilir; gelen kod `verification_code` alanına yazılıp Hermes yeniden başlatıldığında session `/data/telegram_keyword_alert` altında kalıcı hale gelir.

## Veri Dosyaları

- `/data/options.json`: Home Assistant tarafından yazılan ayarlar
- `/data/state.json`: son kontrol, hata ve bildirim durumu
- `/data/latest_price_summary.json`: son döngü fiyat özeti
- `/data/telegram_keyword_alert`: Telegram session
- `/data/login_state.json`: Telegram giriş kodu durumu
- `/data/seen_messages.json`: işlenen Telegram mesajları
- `/data/status.json`: Telegram dashboard sayaçları
- `/data/error_events.json`: son 24 saatlik Telegram hata kayıtları
- `/data/telegram_quick_add.json`: Kayıtlı Mesajlar üzerinden başlatılmış, tamamlanmamış takip ekleme adımları

## Çalışma Akışı

1. Config yüklenir ve doğrulanır.
2. Takip edilen linkler siteye göre dengeli sırayla kontrol edilir.
3. Linkin ürün mü arama mı olduğu otomatik anlaşılır.
4. Telegram aktifse ayrı arka plan worker içinde kanal mesajları dinlenir.
5. Gerekirse Pushover bildirimi gönderilir.
6. State ve özet dosyaları güncellenir.

## Sağlayıcı Davranışları ve Performans

Hermes her siteyi kendi sağlayıcısında okur. Bir sitenin fiyat okuma kuralı diğer sitelerin fiyat çıkarımını değiştirmez.

- **Amazon:** Ürün ve arama sayfalarını destekler. Arama sonuçlarında yalnızca takip adıyla eşleşen ürünler değerlendirilir; `All Departments içindeki sonuçlar gösteriliyor` bölümünden sonrası yok sayılır. Her Amazon bağlantısında normal satışlar ve doğrulanmış Amazon Depo ikinci el teklifleri ayrı satırlardır. Depo teklifi için hem ikinci el kanıtı hem de Amazon Depo satıcısı aranır. Varyasyon taraması ürün ayrıntılarını ek olarak okuyabildiği için kontrol süresini uzatabilir.
- **Hepsiburada:** Ürün ve arama sayfalarını destekler. Birden fazla satıcı arasından en düşük geçerli fiyat seçilir; ürün kartı, renk/depolama gibi varyasyonlar ve görünürse Premium fiyat metni ayrı değerlendirilir. Çok varyasyonlu ürünlerde her varyasyonun ayrıntı sayfası okunabileceğinden tarama süresi artabilir.
- **Trendyol:** Ürün sayfasındaki güncel ürün fiyatını ve temel ürün bilgisini okur.
- **Network:** Sayfadaki normal fiyatın yanında `Sepette` fiyatı varsa indirimli sepet fiyatını önceliklendirir. Beden girilmişse yalnızca istenen beden stoktayken fiyatı tabloya alır; `XL`, `xl` ve parantezli beden ekleri eşdeğer kabul edilir.
- **Beymen Club:** Network ile aynı şekilde normal fiyatın yanında `Sepette`, `2 ve üzeri` veya `3 ve üzeri` kampanya fiyatı varsa indirimli fiyatı önceliklendirir. Beden girilmişse yalnızca istenen beden stoktayken fiyatı tabloya alır; büyük/küçük harf farkı yoktur.
- **Nordbron:** Sayfadaki ürün fiyatını okur; bot koruması veya captcha gerçek hata olarak kaydedilir.
- **Zara:** Renk varyasyonlarını ve seçilen bedeni kontrol eder. İstenen beden stokta değilse bu durum hata değil, özet tablodaki `Stokta Olmayanlar` bölümüne yazılır. `6` ve `44` gibi girilen bedenler, sayfadaki yaş/EU ekleriyle uyumlu karşılaştırılır.
- **H&M:** Renk ve beden stok bilgisini siteye özel veri yolu üzerinden okur. Stokta olmayan beden hata olarak değil, `Stokta Olmayanlar` bölümünde gösterilir.
- **Ben Gurme:** Shopify ürün verisindeki canlı stok ve varyant bilgisini okur. Stoktaki her gramaj ayrı satır olarak değerlendirilir; ürün tamamen tükendiyse bu teknik hata sayılmaz ve `Stokta Olmayanlar` bölümünde gösterilir. Ürün tekrar stokta olduğunda hedef fiyattan bağımsız tek bir stok bildirimi gönderilir.

Kontrol sırası aynı siteye art arda istek gelmesini azaltacak biçimde dengelenir. Amazon ve Hepsiburada'nın bot koruması veya değişken sayfa yapısı nedeniyle ek kurtarma denemeleri yalnızca ilk okuma başarısız olduğunda çalışır. Amazon Depo teklifi denetimi otomatik yürür; hızlı çevrim için varyasyon taramasını yalnızca gerçekten ihtiyaç duyulan takiplerde etkinleştirmek en verimli yaklaşımdır.

## Geliştirme Notu

Hermes mimarisi provider tabanlıdır. Her site için parser/fiyat yakalama kodu ayrı dosyadadır:

- `app/hermes/providers/amazon.py`
- `app/hermes/providers/hepsiburada.py`
- `app/hermes/providers/trendyol.py`
- `app/hermes/providers/network.py`
- `app/hermes/providers/beymenclub.py`
- `app/hermes/providers/nordbron.py`
- `app/hermes/providers/zara.py`
- `app/hermes/providers/hm.py`
- `app/hermes/providers/bengurme.py`

Amazon ürün ve Amazon arama içindeki ortak fiyat yakalama yardımcıları `app/hermes/providers/amazon_common.py` altında tutulur. Yeni site eklerken mevcut provider dosyalarını değiştirmek yerine yeni siteye özel ayrı bir provider eklenmelidir.

## Geliştirme ve Kontrol

Yerel geliştirme ortamında bir kez şu bağımlılıkları kur:

```sh
cd ..
python -m venv .venv
.venv/bin/pip install -r ha-addon/app/requirements.txt -r requirements-dev.txt
sh tools/check.sh
```

GitHub Actions aynı kontrolleri ve add-on container build işlemini her `main` gönderiminde yürütür. Bu sayede Docker kurulu olmayan geliştirme makinelerinde de container yapısı doğrulanır.
