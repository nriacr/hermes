# Hermes

Hermes, Home Assistant üzerinde çalışan çok siteli fiyat ve Telegram fırsat
takip add-on'udur.

- Bir takip kartında en fazla 5 ürün veya arama linki; site ve link türü
  linkten otomatik anlaşılır. Arama linklerinde kart adı aranacak ifadedir.
- Desteklenen siteler: Amazon, Hepsiburada, Trendyol, Network, Beymen Club,
  Zara, H&M, Togg, IKEA, MediaMarkt. Nordbron ve Ben Gurme 4.16.0'da kaldırıldı; bu sitelerin
  yeni linkleri desteklenmeyen site olarak reddedilir.
- Tarama sıklığı: 1 dk, 5 dk, 15 dk, 30 dk, 60 dk, 3 saat, 6 saat, 12 saat.
  Renkli nokta en sık kademede kırmızı, en seyrek kademede yeşildir; bütün ekranlar
  aynı renk tanımını kullanır. Yeni kartlar 1 dk ile başlar. Eski “Her çevrim”
  seçimi 1 dk olarak okunur; diğer kayıtlı süreler ve fiyat/bildirim geçmişi korunur.
  Süre dolunca kart ilk uygun sırada taranır; uzun okumalar aralığı uzatabilir.
- Siteler kendi bağımsız kontrol sıralarında çalışır; bir sitenin uzun turu diğerinin sonraki kontrolünü bekletmez.
- Bildirimler ortak kalıcı Pushover kuyruğundan gelir. Bağlantı kesintisinde sınırlı yeniden deneme yapılır;
  güncelliğini kaybeden fiyat bildirimi gönderilmez. Kritik sorun panel ve Home Assistant uyarısında görünür.
- Çalışma verisi tek SQLite deposunda; fiyat geçmişi ve bildirim hafızası yeniden başlatmada korunur.
  Takip kartının adı/hedefi değişince kalıcı kimliği değişmez.
- Amazon davranışı değişmedi; kendi erişim/varyant kuralları site modülünde korunur.
- Telegram kanallarında keyword takibi ve Kayıtlı Mesajlar'dan hızlı takip
  ekleme. İsteğe bağlı Hermes bot sohbetinde renk/beden çoklu seçim düğmeleri,
  tarama aralığı ve hazır hedef fiyatlar bulunur; manuel fiyat da yazılabilir.
- Panel: özet tablo, istatistik ve ayarlar. Home Assistant
  içinden (ingress) ve isteğe bağlı token'lı public adresten aynı şekilde
  çalışır.

Kurulum: Home Assistant > Add-on Store > Repositories alanına
`https://github.com/nriacr/hermes` ekle.

Ayrıntılar: [add-on kılavuzu](ha-addon/README.md),
[değişiklik günlüğü](ha-addon/CHANGELOG.md),
[mimari](docs/ARCHITECTURE.md). Geliştirme kuralları [AGENTS.md](AGENTS.md)
dosyasındadır.

## Telegram bot bağlantısı

BotFather'da `/newbot` ile bot oluştur. Hermes > Ayarlar > Telegram ayarları
altındaki **Hermes bot erişim anahtarı** alanına BotFather anahtarını girip
kaydet. Telegram takip açık ve mevcut kişisel hesap bağlantısı etkin olmalı.
Bot yalnız bu kişisel hesabın özel mesajlarını kabul eder; kullanıcı adı
büyük/küçük harfine veya ilk mesaj atan kişiye göre yetki verilmez.
Anahtar sayfada tekrar gösterilmez; alanı boş bırakmak mevcut anahtarı korur.
Bot sohbetinde Başlat'a basıp ürün bağlantısını paylaş.

Renk ve bedende birden fazla değer işaretlenebilir; **Seçimi tamamla** ile
ilerlenir. Model ve tarama aralığı tek seçimdir. Hedef fiyat seçenekleri seçili,
stokta olan varyantların en düşük güncel fiyatının %5/10/15/25/40 altından
hesaplanır ve üstteki 50 TL katına yuvarlanır. Aynı çıkan veya mevcut fiyata
eşit/üstündeki tutarlar çıkarılır. Fiyat okunamadığında yalnız manuel giriş
sunulur. Hedefte tek düğmeye basmak veya doğrudan fiyat yazmak kaydı tamamlar.

Yayın erişimi ve otomatik yetki kontrolü: [yayın erişimi kılavuzu](docs/RELEASE_ACCESS.md).

## Ayarlara PIN ile erişim

Anasayfa, ürün bağlantıları ve fiyat ayrıntıları mevcut haliyle görüntülenir.
Ayarlar, istatistikler, bildirim testleri, silme işlemleri ve yeniden başlatma
4 haneli PIN gerektirir. Dördüncü rakam girilince kontrol otomatik yapılır.

İlk kurulumdan sonra Home Assistant'tan Hermes'i aç, Ayarlar simgesine bas ve
**PIN’i Home Assistant üzerinden değiştir** bağlantısıyla dört rakamı iki kez
girip kaydet. Ardından PIN ile ayarları aç. PIN henüz belirlenmemişse dış
bağlantıda yalnız anasayfa kullanılabilir. PIN unutulursa aynı Home Assistant
bağlantısıyla yeni PIN belirlenir. PIN'i sohbete gönderme.

**Kilitle** düğmesi hemen çıkış yapar. 15 dakika işlem yapılmaması otomatik
kilitler; fiyat/statistik yenilemeleri süreyi uzatmaz. Beş yanlış denemeden
sonra giriş bekletilir. Sayaçlar ayrıdır: Home Assistant içinden (ingress)
girişte her beş yanlışta 60 saniye; public adreste her beş yanlışta sırayla
60 saniye, 5 dakika, en çok 30 dakika. Public adresteki saldırı Home Assistant
içinden girişi kilitlemez; doğru PIN o yüzeyin sayacını sıfırlar. Sınırlar
Hermes yeniden başlatıldığında da korunur. PIN değişince veya Hermes yeniden başlayınca
açık ayar oturumları kapanır. Fiyat geçmişi ve takipler etkilenmez.

PIN'in kendisi saklanmaz; tuzlu doğrulayıcı ve deneme sınırı `/data/web_access.json`
içindedir. Bu dosya kullanıcı güvenlik verisidir; Git'e/rapora kopyalanmamalı ve
temizlik işleminde silinmemelidir. Günlük Hermes yedeği tarafından korunur.
Dış bağlantının mevcut HTTPS/tünel koruması kullanılmalıdır. Public ayarlar ekranı
HTTPS olmadan (`X-Forwarded-Proto` https değilse) açılırsa sarı bir uyarı gösterir:
"Bu adres şifresiz (HTTP). Yalnız güvendiğin ağda ya da HTTPS tünel arkasında kullan."
Home Assistant içinden açılışta uyarı çıkmaz.
