# Hermes

Hermes, Home Assistant üzerinde çalışan çok siteli ürün takip add-on'udur.

## Özellikler

- Ürün bazlı takip: Amazon, Hepsiburada, Trendyol, Network
- Amazon arama sayfası takibi: birden fazla arama linki ve hedef kuralı
- Pushover bildirimleri
- 24 saat tekrar bildirimi kontrolü (`notify_once_in_24H`)
- Aktif/pasif kural yönetimi (`active`)
- Log tablosu ve özet dosyası (`/data/latest_price_summary.json`)
- Ingress paneli üzerinden durum ekranı ve Pushover test butonu

## Konfigürasyon

`config.yaml` içindeki `options` ve `schema` alanları Home Assistant UI ile uyumludur.

Ana alanlar:

- `interval_minutes`
- `request_timeout_seconds`
- `pushover_user_key`
- `pushover_api_token`
- `products[]`
- `amazon_search_pages[]`
- `amazon_search_targets[]`

Ürünlerde ayrıca site seçilmez. Hermes, ürün linkinden uygun siteyi otomatik algılar ve ilgili provider dosyasındaki fiyat okuma mantığını çalıştırır.

## Veri Dosyaları

- `/data/options.json`: Home Assistant tarafından yazılan ayarlar
- `/data/state.json`: son kontrol, hata ve bildirim durumu
- `/data/latest_price_summary.json`: son döngü fiyat özeti

## Çalışma Akışı

1. Config yüklenir ve doğrulanır.
2. Ürün kuralları kontrol edilir.
3. Amazon arama sayfası kuralları kontrol edilir.
4. Gerekirse Pushover bildirimi gönderilir.
5. State ve özet dosyaları güncellenir.

## Geliştirme Notu

Hermes mimarisi provider tabanlıdır. Her site için parser/fiyat yakalama kodu ayrı dosyadadır:

- `app/hermes/providers/amazon.py`
- `app/hermes/providers/hepsiburada.py`
- `app/hermes/providers/trendyol.py`
- `app/hermes/providers/network.py`

Amazon ürün ve Amazon arama içindeki ortak fiyat yakalama yardımcıları `app/hermes/providers/amazon_common.py` altında tutulur. Yeni site eklerken mevcut provider dosyalarını değiştirmek yerine yeni siteye özel ayrı bir provider eklenmelidir.
