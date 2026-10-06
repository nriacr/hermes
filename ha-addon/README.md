# Hermes

Hermes, Home Assistant üzerinde çalışan çok siteli ürün ve Telegram fırsat takip add-on'udur.

## Özellikler

- Takip edilenler: tek kayıt altında en fazla 5 link izleme
- Linkten otomatik site algılama: Amazon, Hepsiburada, Trendyol, Network, Beymen Club, Nordbron, Zara, H&M, Ben Gurme, Togg
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

`interval_seconds`, her tam tarama çevriminin ardından beklenecek süredir ve 1 saniye ile 24 saat arasında ayarlanabilir. Çevrimin toplam süresi, taramanın sürdüğü süreye bu bekleme süresinin eklenmesiyle oluşur.

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

## Home Assistant'ta Hermes

Hermes her çevrimden sonra Home Assistant'a şu sensörleri yazar:

- `sensor.hermes_firsat_sayisi`: hedef fiyatın altındaki ürün sayısı. `firsatlar`
  özelliğinde site, ürün, fiyat, hedef, fark, depo ve link bulunur (en fazla 25).
- `sensor.hermes_son_tur`: son çevrimin bittiği zaman; süre, ürün ve stok dışı
  sayıları özelliklerde.
- `sensor.hermes_hata_sayisi`: son 24 saatte okunamayan takip sayısı ve hatalar.

Her fırsat bildiriminde `hermes_firsat` olayı tetiklenir (site, takip, ürün,
fiyat, hedef, fark, depo, satıcı, link). Örnek otomasyon tetikleyicisi:

```yaml
trigger:
  - trigger: event
    event_type: hermes_firsat
```

Sensörler Home Assistant yeniden başladığında bir sonraki çevrim bitince
yeniden oluşur. Home Assistant'a ulaşılamaması izlemeyi etkilemez.

## Veri Dosyaları

- `/data/options.json`: Home Assistant tarafından yazılan ayarlar
- `/data/state.json`: son kontrol, hata ve bildirim durumu
- `/data/latest_price_summary.json`: son döngü fiyat özeti
- `/data/cycle_history.json`: son yedi günde tamamlanan çevrimlerin süreleri; İstatistik sayfasının veri kaynağı
- `/data/telegram_keyword_alert`: Telegram session
- `/data/login_state.json`: Telegram giriş kodu durumu
- `/data/seen_messages.json`: işlenen Telegram mesajları
- `/data/status.json`: Telegram dashboard sayaçları
- `/data/error_events.json`: son 24 saatlik Telegram hata kayıtları
- `/data/telegram_quick_add.json`: Kayıtlı Mesajlar üzerinden başlatılmış, tamamlanmamış takip ekleme adımları

## Çalışma Akışı

Hermes tek bir uygulama olarak çalışır: izleyici, Home Assistant paneli (8099),
public panel (8100) ve Telegram dinleme aynı süreçtedir.

1. Ayarlar yüklenir ve doğrulanır. Ayarlarda hata varsa izleme başlamaz; panel
   açık kalır ve hatayı ana ekranda gösterir.
2. Her site kendi sırasında, aynı anda kontrol edilir; bir sitenin yavaşlığı
   veya koruması diğerlerini bekletmez. Her sitede takipler önceliğe göre
   (yüksek → orta → düşük) ve sitenin kendi istek aralığıyla okunur.
3. Linkin ürün mü arama mı olduğu otomatik anlaşılır.
4. Hedef altındaki fırsat hemen bildirilir ve tablo o anda güncellenir.
5. Çevrim sonunda tablo, istatistik ve durum dosyası kaydedilir.

Panelden yapılan sıfırlamalar izleyiciye iletilir ve taramalar arasında
uygulanır; "Bildirim Sıfırla" ardından hemen yeni bir kontrol başlatır.
Home Assistant watchdog'u izleyici durursa veya bir çevrim üç saatten uzun
sürerse Hermes'i yeniden başlatır.

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

- **Togg:** Konfigüratör sayfasının kullandığı herkese açık model listesini okur (dakikada en fazla bir istek, tüm Togg kartları için ortak). Kartın `name` alanına izlenecek model yazılır (örneğin `T10X V2 RWD Uzun Menzil`); model listede varsa stokta, yoksa `Stokta Olmayanlar` bölümünde görünür. Stokta olmaması hata sayılmaz. Model listeye girdiğinde tek bir stok bildirimi gönderilir. Fiyat takibi yoktur; kart biçimi gereği istenen hedef fiyat yüksek bir sayı girilebilir.

Her site kendi sırasıyla ve istekler arasında ayarlanan bekleme süresiyle okunur. Amazon ve Hepsiburada'nın bot koruması veya değişken sayfa yapısı nedeniyle ek kurtarma denemeleri yalnızca ilk okuma başarısız olduğunda çalışır. Amazon CAPTCHA/429/503 döndürürse, arama bağlantılarındaki 503 yanıtları dahil, Hermes yalnızca etkilenen takip bağlantısını önce 15 dakika, yinelenirse 30 ve en fazla 60 dakika bekletip kendiliğinden yeniden dener. Hata görünür kalır, başarılı okumada bekleme sıfırlanır; diğer bağlantılar taranmaya devam eder. Koruma, fiyat okunmuş bir varyanttan sonra gelirse o doğrulanmış teklif korunur. Amazon Depo teklifi denetimi otomatik yürür; hızlı çevrim için varyasyon taramasını yalnızca gerçekten ihtiyaç duyulan takiplerde etkinleştirmek en verimli yaklaşımdır.

Amazon aramalarında başlığı `Hariç tut` terimleriyle eşleşen sonuçlar, ürün ayrıntısı isteği açılmadan elenir. Varyasyonlu ürünlerde bulunan her varyasyon her uygun çevrimde yeniden okunur; varyasyon bağlantıları veya fiyatları önbellekten atlanmaz.

Amazon'a giden her istek günlüğe tek satırla yazılır (yöntem, sonuç, süre).
Aynı Amazon ürün linki aynı çevrimde birden fazla kartta varsa sayfa bir kez
okunur; bu önbellek çevrim sonunda silinir ve sonraki çevrimde fiyat tekrar
ağdan okunur. Her çevrim sonunda öncelik başına başlayan, sırası gelen ve
ertelenen kart sayıları kaydedilir.

Amazon'un HTTP okuması başarısız olursa (CAPTCHA/429/503 dışında) aynı adres
Pi'deki Chromium ile bir kez, sayfanın tamamı yüklenerek okunur. Ana belgenin
ağ yanıtı ve ürün kimliği doğrulanmadan fiyat kullanılmaz.

## Geliştirme Notu

Her site kendi okuyucusunda (`app/hermes/providers/`) hem sayfayı indirir hem
fiyatı ayrıştırır: `amazon/`, `hepsiburada/`, `trendyol.py`, `network.py`,
`beymenclub.py`, `nordbron.py`, `zara.py`, `hm.py`, `bengurme.py`, `togg.py`. Yeni site
eklerken mevcut okuyuculara dokunulmaz; yeni bir okuyucu yazılıp
`providers/registry.py` dosyasına eklenir. Mimari ayrıntılar
[`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md) dosyasındadır.

## Geliştirme ve Kontrol

Yerel geliştirme ortamında bir kez şu bağımlılıkları kur:

```sh
cd ..
python -m venv .venv
.venv/bin/pip install -r ha-addon/app/requirements.txt -r requirements-dev.txt
sh tools/check.sh
```

Testler `tests/` klasöründedir. Hermes'i Home Assistant dışında denemek için
`HERMES_DATA_DIR` ile geçici bir veri klasörü verilebilir:

```sh
cd ha-addon/app
HERMES_DATA_DIR=/tmp/hermes-data python -m hermes
```

GitHub Actions aynı kontrolleri ve add-on container build işlemini her `main` gönderiminde yürütür. Bu sayede Docker kurulu olmayan geliştirme makinelerinde de container yapısı doğrulanır.

### Amazon Depo ve varyasyon taraması (2.5.9)

`Varyasyonları ekle` açık olduğunda renk ve kapasite/ölçü seçeneklerinin gerçek
ürün bağlantıları izlenir; keşfedilen her ASIN bir turda yalnızca bir kez okunur
(en fazla 60). Yeni ürün seçeneği pasif olsa da depo teklifi bulunabileceği için
bağlantı taranır. Her birleşimin fiyatı kendi sayfasından alınır.

Ürün sayfasındaki “Kullanılmış ve yeni gibi” kutusu, kendi fiyatı ve Amazon Depo
satıcısı birlikte doğrulanırsa doğrudan kullanılır. Gerekirse ayrı ikinci el
teklif listesi okunur. Depo teklifleri sıfır fiyatından ve takas tutarından ayrıdır.
Hedef fiyat ve mevcut filtreleri sağlayan teklif, diğer varyantların bitmesi
beklenmeden “Amazon Depo fırsatı” bildirimiyle iletilir. 24 saatlik bildirim
baskılama ve düşük fiyat istisnası korunur.

Bu akış taramalar arasındaki süreyi ortadan kaldırmaz: yeniden kontrol süresi
diğer takipler, istek aralıkları ve tur sonu beklemesine bağlıdır. Çok kısa
süreli stokların tamamını yakalama garantisi yoktur. `Test` sayfası aynı
varyasyon okuyucusunu kullanır, kayıt veya bildirim oluşturmaz.

### Tablo başlıkları

Fırsat ve stok dışı tablolarındaki ürün adları en fazla 60 karakter gösterir;
kesilen adın sonuna üç nokta eklenmez. Varyasyon sonuçlarını açan grup başlıkları
en fazla 70 karakter gösterir. Tam ürün adı veya grup adı hücrenin üzerine
gelindiğinde görünür; bağlantı ve takip verisi kısaltılmaz.

Amazon'un normal araması ile Amazon Depo araması aynı teklifi ayrı kaynaklardan
getirse bile tablo bunu tek satırda gösterir. Yeni ürün ve Depo teklifi ise
ayrı satırlar olarak kalır.
