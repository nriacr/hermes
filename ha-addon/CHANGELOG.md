# Değişiklik günlüğü

## 4.20.5

- "Değişiklikleri uygula" butonu grup filtrelerindeki yeşil renge (referans renk) getirildi.

## 4.20.4

- Ayarlar sayfasında "Değişiklikleri uygula" butonu koyu zeminde görünmüyordu; yeşilimsi (lime-yeşil) renge çevrildi.

## 4.20.3

- Eski önbellek kopyası süresi 3 dk'dan 30 dk'ya çıkarıldı (uzun boşta kaldıktan sonraki ilk açılış da anında); canlı yenileme birkaç sn içinde güncel veriyi getirir.

## 4.20.2

- Telefondan açılışta 2-7 sn siyah ekran giderildi: anasayfanın özet bloğu kısa süre önbellekte tutulur (8 sn taze; 30 dk'ya kadar eski kopya anında verilir, arkada yenilenir; ayar/sıfırlama işlemleri önbelleği siler). Tarama, bildirim ve veri mantığı değişmedi; yalnız web görünümünün hazırlanışı.

## 4.20.1

- Telegram keyword bildirimleri sessiz gelir (Pushover öncelik -1: ses ve titreşim yok; ekranda görünür). Fiyat fırsatı bildirimleri aynı kaldı. Yalnız bu bildirimin ayarı değişti; tespit, sıra ve gönderim mantığı aynı.

## 4.20.0

- Fırsatlar/fiyatlar sayfası (anasayfa) yeni renk düzenine geçti (yalnız görünüm; veri, bildirim, tarama mantığı değişmedi; ayarlar ve istatistik sayfaları aynı). Kural: kırmızı ve yeşil yalnız fiyat artış/azalış okları ve değişim yüzdelerinde (▼ yeşil `#3ddc84`, ▲ kırmızı `#ff5c6c`; "hedefin altında" etiketi ve `ov-chg down/up` aynı). Sarı (`#ffd35c`) yalnız Depo (Depo kartı/etiketi, detay grafiğinde Depo çizgisi; eski `#ffb347` kalktı). Her site tek ana renkten bir palet kullanır (`--c` ana, `--hi` açık ton, `--lo` koyu ton): Amazon `#2f9bff` (mavi), Hepsiburada `#ff7a1a` (turuncu), Trendyol `#9a7bff`, Network `#19d3d3`, Beymen Club `#d4b896`, Zara `#e8ebf5`, H&M `#e56bff`, Togg `#8d9bb5`, diğer `#a7a3c8`. Fırsat kartı çerçevesi artık sitenin kendi rengi. Küçük çizgi grafik site renginde (yönü ok/yüzde söyler). Detay grafiğinde tek çizgi site rengi; Depo+Yeni çiftinde "Yeni" çizgisi ve açıklama kutusu site rengi, Depo sarı; hedef çizgisi beyaz kesik. Nötr (beyaz/gri/koyu mavi-gri): seçili sekme, "Ürüne git", çevrim noktası, fırsat sayısı, "açık sorun yok", sorun satırları, arka plan lekeleri, logo; tarama sıklığı noktaları anasayfada beyazdan griye tek ton (Ayarlar'daki renkler aynı kaldı). Kod geri dönüşü: `sh tools/prepare_code_rollback.sh dff3940` (4.19.5). Ayrıntı: `changelogs/2026-10-17-renk-duzeni.md`.

## 4.19.5

- Sayfa metinleri sadeleştirildi (yalnız metin; veri, bildirim ve tarama mantığı değişmedi). Kaldırıldı: anasayfa başlık altı cümlesi, fiyat geçmişi ipucu, Ayarlar'daki desteklenen siteler / öncelik / toplu işlem / zamanlama / günlük özet açıklamaları, istatistik giriş ve kutu altı açıklamaları, hata dönemleri ve internet kullanımı alt yazıları, Fırsat geçmişi giriş cümlesi, PIN ekranlarındaki yönlendirme cümleleri. Kısaltıldı: boş durum metinleri ("Yok." / "Henüz ölçüm yok."), HTTP uyarısı ("HTTP (şifresiz bağlantı)"), kaydetme ve yeniden başlatma metinleri, tüm onay pencereleri ("… Devam?"). Gerçek hatalar (ayar hatası, eskimiş sayfa, açık sorunlar) ve silme/sıfırlama/yeniden başlatma onayları korundu. Kod geri dönüşü: `sh tools/prepare_code_rollback.sh 92cafbb` (4.19.4).

## 4.19.4

- Hepsiburada gereksiz başarısız istekleri atlar. 4.19.3 ölçümü (10 dk, 28 okuma): ana sayfa 403 x28, python-requests 403 x84, requests_fresh 403 x84, curl_cffi başarılı x28; yani her okuma 7 reddedilen istekten sonra curl ile okunuyordu (okuma başına 8 istek). Artık curl_cffi bir adresi başarıyla okuduysa aynı adresin sonraki okumaları doğrudan o adresle curl'dan başlar; curl'da herhangi bir hata olursa adres unutulur ve önceki tam sıra (ana sayfa ısınma, requests, requests_fresh, curl) aynen çalışır, yani hiçbir okuma kaybolmaz. Başlıklar, kimlik, okuma sayısı ve sıklığı değişmedi. Günlükteki "Hepsiburada deneme özeti" satırı `curl_first` yöntemini de gösterir. Kod geri dönüşü: `sh tools/prepare_code_rollback.sh 48e380d` (4.19.3 + 4.19.2 kayıt).

## 4.19.3

- Hepsiburada ağ denemeleri ölçülür (yalnızca ölçüm, davranış aynı): her deneme (ana sayfa, requests, requests_fresh, curl) yöntem ve sonuca (200, 403, ... veya hata türü) göre sayılır ve 10 dakikada bir günlüğe "Hepsiburada deneme özeti" satırı yazılır. Amaç, okuma başına neden 5-6 istek yapıldığını görmek. Okuma sayısı, sıra, başlıklar ve kimlik değişmedi. Kod geri dönüşü: `sh tools/prepare_code_rollback.sh fd76e83` (4.19.2).

## 4.19.2

- SSD yazması biraz daha azaltıldı: geri dönüş için tutulan JSON kopyaları (state.json, latest_price_summary.json vb.) en çok 5 dakikada bir yazılır (önceden dakikada bir; durdurma ve çıkışta son hali yine yazılır, değişmeyen içerik zaten yazılmıyor). Asıl kayıt `hermes.db` içinde anında yazılmaya devam eder; bildirim, fiyat geçmişi ve tablo davranışı değişmedi. Yalnız eski sürüme acil dönüşte kullanılan JSON kopyası en çok 5 dk eski olabilir.

## 4.19.1

- Hepsiburada artık her ürün okumasından önce ana sayfaya ısınma isteği atmaz: ana sayfa bir okuma oturumunda (her çevrimde site başına bir oturum) yalnızca ilk okumadan önce istenir, çerezleri oturumda kalır. Ana sayfa isteği başarısız olursa veya bir sayfa okuması başarısız olursa bir sonraki okuma yine ısınma ile başlar; yedek yollar (taze oturum, curl) ve tüm başlıklar/kimlik aynı. Okuma sayısı ve sıklığı değişmez, yalnızca gereksiz ağ isteği azalır (4.18.0 internet sayacıyla ölçülür). Kod geri dönüşü: `sh tools/prepare_code_rollback.sh f64cd64` (4.19.0).

## 4.19.0

- SSD yazması azaltıldı (davranış değişmedi). Takip hafızası (`state.json`, yaklaşık 740 KB) ve fiyat özeti gibi büyük kayıtlar artık `hermes.db` içinde sıkıştırılarak (zlib) saklanır; yazılan değer hemen açılıp orijinalle bayt bayt karşılaştırılır, eşit değilse eski düz metin yöntemiyle yazılır. 4 KB altı kayıtlar ve eski düz metin kayıtlar aynen okunur. Son yazılandan farksız bir kayıt tekrar yazılmaz (işlem başarısız olursa "yazıldı" sayılmaz), geri dönüş için tutulan JSON kopyaları da içerik değişmediyse yeniden yazılmaz. Bildirim kuyruğu, bastırma kayıtları, `synchronous=FULL`, JSON dışa aktarımı ve fiyat geçmişi değişmedi. Teşhis çıktısında `state_snapshot_stored` (diskteki bayt) eklendi.
- Dikkat (geri dönüş): 4.19.0'dan önceki sürümler sıkıştırılmış kaydı okuyamaz ve takip hafızasını bozuk sayar. `prepare_code_rollback.sh` bu yüzden 4.19.0 öncesi bir sürüme dönmeyi varsayılan olarak reddeder; acil durumda Hermes durdurulup `snapshots` tablosundaki `state.json` ve `latest_price_summary.json` satırları silinirse eski sürüm en çok 60 sn eski JSON kopyalarından açılır. Normal geri dönüş için günlük yedek kullanılır.

## 4.18.0

- Yeni: Hermes'in kendi internet kullanımı ölçülür. Her HTTP yanıtı için site bazında istek sayısı, ağdan inen (sıkıştırılmış) bayt, açılmış bayt ve gönderilen bayt sayılır (Amazon, Hepsiburada, H&M, Zara, Trendyol, Beymen Club, Togg, Pushover ve Telegram kısa link açma). Sayaçlar hermes.db içindeki yeni `traffic` tablosuna gün+site başına tek satır olarak dakikada bir yazılır (30 gün saklanır) ve 10 dakikada bir günlüğe "İnternet kullanımı" satırı düşer. İstatistik sayfasında yeni "İnternet kullanımı · Son 7 gün" bölümü: Bugün, Son 7 gün, Günlük ortalama, En çok kullanan, site başına MB/gün çubuğu ve günlük tablo. İstek sayısı, sıklığı, başlıklar, Amazon kimliği ve tekrar denemeler değişmedi; sayaç hata verirse istek etkilenmez. TLS el sıkışması, DNS ve Telegram'ın kendi (Telethon) bağlantısı sayılmaz. Kod geri dönüşü: `sh tools/prepare_code_rollback.sh ee0bdb4` (4.17.1).

## 4.17.1

- Hariç tut filtresi artık anasayfada, listelerde ve özet kartta eski kayıtlara da uygulanır: kartın filtresine uyan başlıklar (ör. Hepsiburada iPhone 17 Pro Max kartında "2 TB") önbellekteki son sonuçlardan, stok tablosundan ve açılışta yayımlanmış tablodan düşer. Fiyat geçmişi ve takip hafızası silinmez; filtre kaldırılınca kayıtlar geri gelir. Filtre yazıldığı gibi çalışır, büyük/küçük harfe duyarsızdır ("2 TB" ile "2048 GB" eşitlenmez). Amazon önbellek ve istek yolları değişmedi.

## 4.17.0

- SSD'ye gereksiz yazma azaltıldı. Bir okumada yalnız zaman damgaları değiştiyse tüm takip hafızası (yaklaşık 740 KB) artık her okumada veritabanına yazılmaz; en çok dakikada bir ve her çevrim sonunda yazılır. Fiyat satırı, fiyat/hata/uyarı değişiklikleri ve bildirim kuyruğu yine anında kaydedilir. Geri alma için tutulan JSON kopyaları (state.json, latest_price_summary.json vb.) her okumada değil en çok dakikada bir yazılır (durdurma ve çıkışta son hali yazılır).
- Yeni: Hermes'in diske verdiği veri 10 dakikada bir günlüğe "Yazma özeti" olarak yazılır (dosya/tablo bazında MB ve adet), böylece asıl yazıcı sayılarla görülür. Takipler, fiyat geçmişi ve bildirim davranışı değişmedi.

## 4.16.1

- İstatistik sayfası artık anında açılır: sayfa arka planda hazırlanıp bellekten gösterilir (dakikada bir yenilenir, uygulama açılışında önceden hazırlanır; bölüm zaman aşımı olursa 10 sn sonra yeniden denenir; "İstatistik geçmişini sil" önbelleği temizler). İstek sayıları satır satır Python'a çekilmek yerine SQLite içinde toplanır; kullanılmayan istek süre ortancası kaldırıldı. Sayfa içeriği, veri ve bildirimler değişmedi.

## 4.16.0

- Nordbron ve Ben Gurme siteleri desteklenen siteler listesinden kaldırıldı: bu sitelerin linkleri artık "desteklenmeyen site" olarak reddedilir (diğer linkler ve kartlar etkilenmez). Bu sitelere ait okuyucular ve test örnekleri silindi. Ayarlarda bu sitelere ait kart yoktu; veritabanında (hermes.db) ve durum dosyasında kalmış eski satırlara dokunulmadı, uygulama bunları sorunsuz taşır. Arama sayfası takibi Amazon ve Hepsiburada için aynen çalışır. Kod geri dönüşü: `sh tools/prepare_code_rollback.sh 47d8687` (4.15.1); kurulum yedeği 42c055d8 (16 Ekim 2026 18:50 UTC, tam).

## 4.15.1

- İstatistik sayfası takılırsa artık açılır: okumalar, istekler, özet kutuları ve Sorun geçmişi ayrı çalışır, her biri en fazla 8 saniye bekler; biri bitmezse o bölüm boş/uyarı olarak gösterilir, sayfanın geri kalanı yine açılır. Yavaş (1 sn üstü) veya bitmeyen bölümler günlüğe "İstatistik bölümü ..." satırıyla yazılır; böylece takılmanın nedeni günlükten okunabilir. Veri, takipler ve bildirimler değişmedi.

## 4.14.0

- Fiyat grafiğindeki hedef çizgisi artık hedef fiyat değiştikçe basamak yapar: Hermes her kartın hedefini değiştiği anda kaydeder ve yeşil kesikli çizgi o tarihte iner/çıkar. Geçmiş hedef değişiklikleri kayıtlı değildir; kayıt bu sürümle başlar, öncesi için ilk kayıtlı hedef düz devam eder. Hiç değişmeyen hedefte çizgi eskisi gibi düzdür. Takipler, bildirimler ve fiyat verisi değişmedi.

## 4.13.0

- Ürün ayrıntı penceresindeki fiyat grafiği: aynı üründe hem Yeni hem Depo teklifi takipteyse iki ayrı çizgi çizilir (Yeni mavi, Depo turuncu, açıklamalı); zaman aralığı düğmeleri ikisini birlikte keser, üzerine gelince balon iki fiyatı birden gösterir. Tek teklifli ürünlerde grafik aynı kalır. Veri ve bildirimler değişmedi.

## 4.12.0

- Tüm Hermes verisi artık veritabanında (hermes.db) asıl kaynak: Amazon erişim durumu ve çerezleri ile Telegram durumu, görülen mesajlar, hata olayları, hızlı ekleme ve giriş durumu da takip hafızası ve fiyat özeti gibi tek işlemle veritabanına yazılır; fiyat geçmişi, okumalar ve bildirim kuyruğu zaten oradaydı. İlk açılışta mevcut JSON dosyaları veritabanına alınır. JSON dosyaları eski sürüme geri dönüş için yedek olarak yazılmaya devam eder. Takipler, fiyatlar ve bildirim davranışı değişmedi.

## 4.11.3

- İç düzenleme: Amazon sağlayıcı dosyası (676 satır) okuma ritmi (rhythm.py), sayfa önbelleği (pagecache.py) ve arama sonucu eşleştirmesi (matching.py) olarak bölündü. Kod satırları aynen taşındı; Amazon okuma zamanlaması, istek sayıları ve takipler değişmedi.

## 4.11.2

- İç düzenleme: ayar kaydetme işlemleri (silme, güncelleme, toplu kaydetme, ekleme) ve kart doğrulaması küçük fonksiyonlara ayrıldı. Davranış, mesajlar ve veri aynı; ayarlar tarafındaki karmaşıklık uyarıları kapandı.

## 4.11.1

- Tanı için dönen günlük dosyası: /data/hermes.log.jsonl (JSON satırları, 5 × 1 MB). Pi günlüğündeki satırlar aynı gizleme kuralıyla (anahtar/token/parola yok) buraya da yazılır; günlük 100 satırı aşınca eski kayıtlar dosyada kalır. Görünür davranış, takipler ve bildirimler değişmedi.

## 4.11.0

- Son satır içi betik (onay penceresi) statik confirm.js dosyasına taşındı; sayfalarda satır içi betik kalmadı. Görünüm ve davranış aynı; ileride sıkı güvenlik başlığı (CSP) için ön koşul tamamlandı.

## 4.10.0

- Ayarlar'da "Yeni takip ekle" bölümü desteklenen siteleri listeler. Desteklenmeyen bir siteden link içeren kart artık sessizce kaydedilmez: kaydetme durur ve hangi kartta, hangi sitenin desteklenmediği ile desteklenen liste gösterilir. Takip verisi ve okuma davranışı değişmedi.

## 4.9.0

- Kart başına "Bildirim zamanı" (Ayarlar): Her zaman (varsayılan), Yalnız gündüz (08–22), Yalnız akşam (18–23), Yalnız hafta sonu. Pencere dışında fiyat hedefin altında olsa da bildirim gönderilmez ve gönderildi diye işaretlenmez; pencere açılınca ilk okumada fiyat hâlâ uygunsa gider. "90 günün en düşüğü" bildirimi de aynı kurala uyar. Stok dönüşü bildirimi ve sistem uyarılarının sessiz saatleri değişmedi; mevcut kartlar "Her zaman" kalır.

## 4.8.0

- Ayarlar'da toplu kart işlemleri: her kartın başında seçim kutusu; "Toplu işlem" bölümünden seçili kartlar için aktif/pasif yapma, öncelik ve grup atama. İşlem yalnız kart alanlarını doldurur, kalıcı olması için "Değişiklikleri uygula" gerekir (tek yeniden başlatma). Gizli (filtrelenmiş) kartlar seçilmez. Veri ve kayıt yolu değişmedi.

## 4.7.0

- Kart detayındaki fiyat grafiğine zaman aralığı düğmeleri: 7 gün, 30 gün, 90 gün ve Tümü (yalnız geçmişten kısa aralıklar gösterilir). Hedef fiyat çizgisi her aralıkta durur; pencere açılırken geçerli olan fiyattan başlar. Veri ve bildirimler değişmedi.

## 4.6.0

- Yeni "Fırsat geçmişi" sayfası (Araçlar > Fırsat geçmişi): son 30 günde gönderilen bildirimler, yeniden eskiye; her biri için zaman, fiyat ve ürünün bugünkü fiyatı / hâlâ geçerli olup olmadığı. Veri mevcut bildirim kaydından okunur, yeni tablo yoktur; takipler, fiyat geçmişi ve bildirim davranışı değişmedi.

## 4.5.0

- Docker imajından Chromium, chromium-driver, yazı tipi paketi ve Selenium kaldırıldı (imaj küçüldü, güncelleme daha hızlı iner, bellek baskısı düşer). Amazon'un tek seferlik "tarayıcıyla yeniden oku" yedeği kalktı; curl ile okuma başarısız olursa hata olduğu gibi yükselir ve sonraki çevrimde normal akışla yeniden denenir.
- CAPTCHA/429/503 davranışı, Amazon kimliği (curl_cffi Chrome TLS), takipler, fiyat geçmişi ve bildirim hafızası değişmedi.

## 4.4.0

- Günlük özet bildirimi: her gün 09:00'dan sonra (22:00'den önce) bir kez tek Pushover mesajı: son 24 saatte okuma ve hata sayısı, en çok hata veren site, hedefte/altında ürün sayısı ve en iyi 3 ürün. Ayarlar > "Günlük özet" kutusundan kapatılabilir (varsayılan açık).
- CAPTCHA ve HTTP 503/429 molaları özette hata olarak sayılmaz ve anılmaz (bilerek sessiz kalan davranış korunur).
- Takipler, fiyat geçmişi ve bildirim hafızası değişmedi.

## 4.3.0

- Kart detayında "Son 90 gün en düşük" ve "Son 90 gün ortalaması" (süreye göre ağırlıklı, en az 7 günlük geçmiş varken) ve fiyatın bu ortalamanın yüzde kaç altında/üstünde olduğunu söyleyen etiket; fiyat dönemin dibindeyse "dip" yazar. Veri mevcut fiyat geçmişinden gelir.
- Yeni isteğe bağlı kart seçeneği "90 günün en düşüğünde de bildir" (varsayılan kapalı, ayar sayfasında): fiyat hedefin üstünde olsa bile son 90 günün (en az 7 gün geçmişle) en düşüğüne inerse tek bir bildirim gelir; aynı veya daha yüksek fiyatta tekrar etmez. Hedef fiyat bildirimlerinin susturma kayıtlarına ve `hermes_firsat` olayına dokunmaz.
- Takipler, fiyat geçmişi ve bildirim hafızası değişmedi; mevcut kartlarda seçenek kapalıdır.

## 4.2.8

- Site başına takılma uyarısı: bir site 45 dakika (veya en uzun takip aralığının 3 katı) boyunca hiç başarılı okuma yapamazsa Pushover (sessiz saatler dışında) ve Home Assistant kalıcı bildirimi bir kez uyarır; site yeniden okuyunca uyarı kapanır. Önceki sağlık denetimi yalnız hiç ilerlemeyen okuyucuyu (Amazon için 3 saat) yakalıyordu.
- CAPTCHA/HTTP 503 molaları bilerek sessiz kalır; bu uyarıyı tetiklemez. Yeniden başlatma eşiği değişmedi.
- Takipler, fiyat geçmişi ve bildirim hafızası değişmedi.

## 4.2.7

- Amazon: DNS/bağlantı hatasında (ağ kesintisi) Chromium yedek okuması artık denenmez; aynı ağ sorunu tarayıcıda da yaşanır ve sadece CPU/süre harcıyordu. Zaman aşımı ve diğer hatalarda tek seferlik tarayıcı yedeği aynen sürer.
- Hepsiburada: "Premium metni bulundu ama fiyat ayrıştırılamadı" uyarısı ürün başına 30 dakikada bir yazılır (günde ~3.000 satır gürültü). Fiyat okuma davranışı değişmedi.
- Takipler, fiyat geçmişi ve bildirim hafızası değişmedi.

## 4.2.6

- Ayar kaydı veya "Hermes'i yeniden başlat" sonrası, Hermes yeniden başlayınca artık PIN kilit ekranı yerine anasayfa açılır (yeniden başlama bellekteki PIN oturumunu sildiği için eski davranış ayarlar sayfasında kilit gösteriyordu).
- "Yeniden başlatılıyor" ilerleme sayfası PIN istemez (içinde hassas veri yok); Ayarlar ve İstatistik yine PIN ister.
- Ingress ve herkese açık adres aynı davranır. Takipler, fiyat geçmişi ve bildirim hafızası değişmedi.

## 4.2.5

- Ayarlar: silme ve güncelleme artık formdaki takip kimliğiyle eşleşir; sayfa eskimişse (liste başka yerde değişmişse) işlem reddedilir ve "sayfayı yenile" denir. Eski sayfa yanlış takibi silemez veya ezemez.
- Ayar kaydı tek kilit altında yapılır (iki kayıt birbirini ezmez); Telegram hızlı ekleme de aynı kilidi kullanır. Takip kartı sayısı en çok 500.
- PIN doğrulamasının yavaş hesabı kilit dışına alındı; deneme sınırı ve kilit davranışı aynı kaldı.
- Herkese açık adres anahtarı için seçenek dosyası en çok 5 saniyede bir okunur (ayar kaydında hemen yenilenir).
- Telegram kısa bağlantıları: yönlendirme her adımda denetlenir; özel/yerel ağ adreslerine yönlenen bağlantı reddedilir.
- Home Assistant: `hassio_role: manager` kaldırıldı; Hermes artık yalnız kendi `/addons/self/...` ucunu kullanır.
- Veritabanı: açılışta `PRAGMA quick_check`; bozuksa dosya silinmez, log'a net uyarı yazılır. WAL dosyası saatte bir `wal_checkpoint(TRUNCATE)` ile küçültülür.
- Çalışma ölçümü: /runtime çıktısına `storage_bytes` (durum, veritabanı, WAL) ve `database_integrity` eklendi.
- Ölü kod temizliği (/settings/restart.js çift yolu, kullanılmayan session_name alanı) ve ruff bulguları (try-except-pass, zip strict, raise from).
- Panel görünümü, fiyat geçmişi, takipler ve bildirim hafızası değişmedi.

## 4.2.4

- PIN deneme sınırı yüzeye göre ayrıldı: public adresteki yanlış denemeler Home Assistant içinden girişi kilitlemez.
- Public adreste kilit süresi kademeli artar (60 sn, 5 dk, 30 dk); doğru PIN sayacı sıfırlar. Ekrandaki bekleme süresi doğru gösterilir.
- Public ayarlar ekranı HTTP ise sarı uyarı gösterir (Home Assistant içinde gösterilmez).
- Docker temel imajı sabit sürüme alındı (python 3.12.15-slim).
- Fiyat geçmişi, takipler ve Amazon davranışı değişmedi.

## 4.2.3

- Güvenlik: requests kütüphanesi 2.34.2 sürümüne güncellendi.
- Kullanılmayan kod temizlendi.
- Site tespiti yalnız alan adından yapılıyor.
- Herkese açık panelin başka bir sayfaya gömülmesi engellendi (X-Frame-Options ve CSP frame-ancestors).
- Fiyat geçmişi, bildirim hafızası ve Amazon davranışı değişmedi.

## 4.2.2

- Takip kartı silindiğinde, kapatıldığında veya bir bağlantısı kaldırıldığında, o karta ait eski fiyat ve stok satırları Hermes açılışında anasayfa tablosundan temizleniyor. Ayarlardan silmek Hermes'i zaten yeniden başlattığı için tablo bu açılışta güncelleniyor.
- Neden: Eski tablo dosyası açılışta olduğu gibi okunuyor ve her turda yeniden birleştiriliyordu; silinen ürünün satırı hiçbir kart onu sahiplenmediği halde kalıyordu.
- Kalan satırların okuma saati, fiyat geçmişi, min/maks ve bildirim hafızası olduğu gibi korunuyor. Her aktif kartın açılışta bir kez okunması (4.1.1) değişmedi.
- Dosyalar: monitor/summary.py (drop_unconfigured_summary_rows), monitor/cycle.py (açılışta çağrı), tests/test_deleted_watch_rows.py, tests/test_monitor.py.
- Geri alma: önceki sürüme dönüldüğünde tablo dosyası bir sonraki turda kendini yeniden kurar; veri kaybı yok.

## 4.2.1

- Tarama sıklığı 8 kademe oldu: 1 dk, 5 dk, 15 dk, 30 dk, 60 dk, 3 saat, 6 saat ve 12 saat.
- Her çevrim kaldırıldı; mevcut seçimler 1 dk’ya taşınıyor. Diğer süreler, takipler, fiyat geçmişi ve bildirim hafızası korunuyor.
- Renkler en sık taramada kırmızıdan en seyrek taramada yeşile ilerliyor; web ve mobil aynı tanımı kullanıyor.

## 4.2.0

- Anasayfa mevcut haliyle açık kalıyor; ayarlar, istatistikler ve tüm değişiklik işlemleri 4 haneli PIN ile korunuyor.
- Dördüncü rakam girilince PIN otomatik kontrol ediliyor. Yanlış denemeler sınırlanıyor; 15 dakika işlemsizlikte ayarlar kilitleniyor.
- PIN ilk kez ve unutulduğunda yalnız Home Assistant içinden belirleniyor; PIN değişikliği ve yeniden başlatma açık ayar oturumlarını kapatıyor.
- Fiyat geçmişi, takipler ve bildirim hafızası korunuyor. Amazon davranışı değişmedi.

## 4.1.3

- Stokta yok listesi her kartın tamamlanan okumasıyla hemen yenileniyor; eski ve artık geçerli olmayan varyant satırları birikmiyor.
- Sırası gelmeyen kartların stok bilgisi ve gerçek kontrol saati, diğer siteler, fiyat geçmişi ve bildirim hafızası korunuyor.
- Amazon davranışı değişmedi.

## 4.1.2

- Hermes her yeniden açılışta bütün aktif takip kartlarını öncelik süresini beklemeden bir kez tarıyor; sonraki kontroller normal öncelik düzeninde devam ediyor.
- Açılış taramasının başlatılan, tamamlanan ve bekleyen kart sayıları ölçülüyor; fiyat geçmişi ve bildirim hafızası korunuyor.
- Amazon davranışı değişmedi.

## 4.1.1

- İstatistik geçmişini sil butonu başarılı ve başarısız okumaları, ağ ölçümlerini, tur sürelerini ve kapanmış sorun geçmişini birlikte temizliyor.
- Fiyat geçmişi, takipler, bildirim hafızası ve açık sorunlar korunuyor; yeni istatistikler sıfırdan birikiyor.
- Amazon davranışı değişmedi.

## 4.1.0

- Açık sorunlar tek satırda kısa ürün adı, varyant, kısa hata türü ve ürün bağlantısıyla gösteriliyor.
  Kapanan sorunlar ve tekrar sayıları İstatistik sayfasındaki Sorun geçmişinde saklanıyor.
- Kısmi okumalar tam başarıdan ayrılıyor; normal stok yokluğu hata sayılmıyor.
  Uzun süren ürün/varyant sorunları Home Assistant üzerinden bildiriliyor.
- Kapanış bütün çalışanlar için ortak süreyle yönetiliyor; yeni istekler duruyor, bekleyen bildirimler korunuyor.
- Tekrarlanan varyant hata günlükleri özetleniyor; ayrıntılı olay kayıtları korunuyor.
- Amazon davranışı değişmedi.

## 4.0.3

- Uygulamanın bildirdiği sürüm kurulum sürümüyle eşitlendi; canlı kurulum doğrulaması tamamlanabiliyor.

## 4.0.2

- Amazon ürününün satın alma alanında "Normalden yüksek fiyat" uyarısı varsa ürün stokta yok kabul ediliyor.
  Fiyat ve Depo teklifleri okunmuyor; bu durum okuma hatası oluşturmuyor. Diğer varyantların taraması devam ediyor.
- Uyarı kalktığında ürün sonraki planlı kontrolde yeniden fiyat takibine giriyor.

## 4.0.1

- Hata kayıtları ürünün tam adını, kapasite/renk seçeneğini ve doğrudan hatalı varyant bağlantısını koruyor.
  Panel bu bilgileri son hata zamanı ile gösteriyor; hangi ürünün etkilendiği açıkça anlaşılabiliyor.
- Aynı hata çalışma hafızası ve teşhis kaydında bulunuyorsa panelde iki kez gösterilmiyor.
  Eski kayıtlar korunuyor; bilinmeyen ürün adı başka bir varyanttan tahmin edilmiyor.
- Amazon davranışı değişmedi.

### 4.0.1 yayın araçları

- Güncellemeler yeni yedek oluşturmuyor; Home Assistant'ın günlük, tam kapsamlı yedeği doğrulanıp kullanılıyor.
  İstenirse ayrıca yedek alınabilir. Geri dönüş aracı şifreli günlük yedekleri de destekliyor.

## 4.0.0

- İzleme çekirdeği; site planlama, ürün değerlendirme, kalıcı kayıt, bildirim gönderimi ve teşhis olarak ayrıldı.
  Bir sitenin uzun turu diğer sitelerin sonraki kontrolünü bekletmiyor; aynı siteye sıralı erişim korunuyor.
- Fiyat, stok ve Telegram bildirimleri ortak kalıcı kuyrukta gönderiliyor. Geçici kesintiler sınırlı tekrar deneniyor;
  gönderilmeden önce fırsatın güncelliği kontrol ediliyor. Bildirim hafızası servis kabul ettikten sonra işleniyor.
- Çalışma hafızası, fiyat gözlemi ve bildirim niyeti tek SQLite işleminde tutuluyor. Eski geçmiş/kimlikler korunuyor;
  JSON dosyaları geri dönüş için dışa aktarılıyor. Bozuk kritik kayıt sessizce boş hafızaya çevrilmiyor.
- Kartlara kalıcı kimlik veriliyor; ad/hedef düzenlemesi geçmişi ayırmıyor. Bir hatalı kart geçerli takipleri durdurmuyor.
- Ürünle ilişkisi doğrulanmamış genel script fiyatı yolları kaldırıldı; öneri/kampanya bölümleri doğru kapsamda okunuyor.
  Belirsiz fiyat tahmin edilmiyor; kaynak, para birimi, koşul ve okuma zamanı kaydediliyor.
- İş/istek ilişkisi, sürüm, planlanan başlangıç, gecikme, süreler ve toparlanma sonucu kaydediliyor;
  kritik gönderim/çalışma sorunları panelde ve Home Assistant kalıcı bildiriminde görünür.
- Ortak web/mobil arayüzün stil, script ve sayfa şablonu ayrı dosyalara taşındı; kullanılan görünüm korundu,
  kullanılmayan eski stil sınıfları temizlendi. Sistem yükü ölçümü doğru adlandırıldı.
- Yayın aracı tam yedek, kalite/config kontrolü, kurulum, yeni sürümde tamamlanan okuma ve geri dönüşü doğruluyor.
  Bağımsız Home Assistant durma uyarısı tanımı eklendi.
- Yayın öncesi GitHub/otomatik kontrol ve Home Assistant yönetim/yedek yetkileri denetleniyor;
  otomatik kontroller derlenen görüntünün açılışını da doğruluyor.
- Amazon davranışı değişmedi.

## 3.15.4

- Takipteki Depo kartları da (fırsat olmayanlar) tamamen sarı; yazılar, etiketler, fiyat çizgisi ve değişim
  yüzdesi koyu antrasit zeminde/yazıda okunaklı kalır.

## 3.15.3

- Depo fırsat kartları tamamen sarı (DEPO etiketiyle aynı sarı); yazılar, fiyat çizgisi ve etiket koyu antrasit,
  böylece Depo fırsatları diğerlerinden bir bakışta ayrılır.

## 3.15.2

- Ayrıntı penceresindeki fiyat grafiğinde fare (telefonda parmak) bir noktanın üstüne gelince o andaki fiyat
  ve tarih-saat bir balonda görünür; grafikte dikey çizgi ve nokta izler.
- "DEPO" etiketi sarı kalır; varyant etiketleri (512 GB, renk) karışmasın diye arka plana yakın koyu gri
  antrasit tonda ve daha sönük.

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
