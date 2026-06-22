# Hermes

Hermes, Home Assistant üzerinde çalışan çok siteli ürün ve Telegram fırsat takip add-on'udur.

## Özellikler

- Ürün bazlı takip: Amazon, Hepsiburada, Trendyol, Network
- Hepsiburada arama linki takibi: arama sonuçlarındaki ürün kartlarından en düşük fiyatı seçer
- Amazon arama sayfası takibi: birden fazla arama linki ve hedef kuralı
- Pushover bildirimleri
- Telegram fırsat/indirim kanalı dinleme
- Telegram keyword, exclude keyword ve aynı gün aynı keyword+fiyat tekrar engelleme
- 24 saat tekrar bildirimi kontrolü (`notify_once_in_24H`)
- Aktif/pasif kural yönetimi (`active`)
- Log tablosu ve özet dosyası (`/data/latest_price_summary.json`)
- Ingress paneli üzerinden durum ekranı ve Pushover test butonu

## Konfigürasyon

`config.yaml` içindeki `options` ve `schema` alanları Home Assistant UI ile uyumludur.

Ana alanlar:

- `interval_seconds`
- `request_delay_min_seconds`
- `request_delay_max_seconds`
- `pushover_user_key`
- `pushover_api_token`
- `products[]`
- `amazon_search_pages[]`
- `amazon_search_targets[]`
- `telegram_enabled`
- `api_id`
- `api_hash`
- `phone_number`
- `verification_code`
- `session_name`
- `channels[]`
- `keywords[]`
- `exclude_keywords[]`

Teknik istek zaman aşımı Hermes içinde yönetilir ve günlük kullanımda config ekranında görünmez.

Ürünlerde ayrıca site seçilmez. Hermes, ürün linkinden uygun siteyi otomatik algılar ve ilgili provider dosyasındaki fiyat okuma mantığını çalıştırır.

Hepsiburada için ürün detay linki yerine arama linki kullanılması önerilir. Örnek:

`https://www.hepsiburada.com/ara?q=Samsung+Galaxy+Tab+S11+Ultra+12GB+256GB`

Bu yapıda Hermes, arama sonuçlarındaki ürün kartlarını okur; taksit ve kupon fiyatlarını eleyerek gerçek ürün fiyatlarını karşılaştırır ve en düşük fiyatı dikkate alır.

Telegram dinleme varsayılan olarak kapalıdır. Aktif edildiğinde Hermes yalnızca config'teki kanalları dinler; mesajda keyword geçer ve exclude keyword'e takılmazsa Pushover bildirimi gönderir. İlk Telegram girişinde kod telefona gönderilir; gelen kod `verification_code` alanına yazılıp Hermes yeniden başlatıldığında session `/data/telegram_keyword_alert` altında kalıcı hale gelir.

## Veri Dosyaları

- `/data/options.json`: Home Assistant tarafından yazılan ayarlar
- `/data/state.json`: son kontrol, hata ve bildirim durumu
- `/data/latest_price_summary.json`: son döngü fiyat özeti
- `/data/telegram_keyword_alert`: Telegram session
- `/data/login_state.json`: Telegram giriş kodu durumu
- `/data/seen_messages.json`: işlenen Telegram mesajları
- `/data/seen_deals.json`: günlük keyword+fiyat tekrar kayıtları
- `/data/status.json`: Telegram dashboard sayaçları
- `/data/error_events.json`: son 24 saatlik Telegram hata kayıtları

## Çalışma Akışı

1. Config yüklenir ve doğrulanır.
2. Ürün kuralları kontrol edilir.
3. Amazon arama sayfası kuralları kontrol edilir.
4. Telegram aktifse ayrı arka plan worker içinde kanal mesajları dinlenir.
5. Gerekirse Pushover bildirimi gönderilir.
6. State ve özet dosyaları güncellenir.

## Geliştirme Notu

Hermes mimarisi provider tabanlıdır. Her site için parser/fiyat yakalama kodu ayrı dosyadadır:

- `app/hermes/providers/amazon.py`
- `app/hermes/providers/hepsiburada.py`
- `app/hermes/providers/trendyol.py`
- `app/hermes/providers/network.py`

Amazon ürün ve Amazon arama içindeki ortak fiyat yakalama yardımcıları `app/hermes/providers/amazon_common.py` altında tutulur. Yeni site eklerken mevcut provider dosyalarını değiştirmek yerine yeni siteye özel ayrı bir provider eklenmelidir.
