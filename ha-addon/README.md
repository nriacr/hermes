# Hermes

Hermes, Home Assistant uzerinde calisan cok siteli urun takip add-on'udur.

## Ozellikler

- Urun bazli takip: Amazon, Hepsiburada, Trendyol, Network
- Amazon arama sayfasi takibi (birden fazla arama linki + hedef kurallari)
- Pushover bildirimleri
- 24 saat tekrar bildirimi kontrolu (`notify_once_in_24H`)
- Aktif/pasif kural yonetimi (`active`)
- Log tablosu ve ozet dosyasi (`/data/latest_price_summary.json`)
- Ingress paneli uzerinden durum ekrani ve Pushover test butonu

## Konfigurasyon

`config.yaml` icindeki `options` ve `schema` alanlari Home Assistant UI ile uyumludur.

Ana alanlar:

- `interval_minutes`
- `request_timeout_seconds`
- `pushover_user_key`
- `pushover_api_token`
- `products[]`
- `amazon_search_pages[]`
- `amazon_search_targets[]`

## Veri Dosyalari

- `/data/options.json`: Home Assistant tarafindan yazilan ayarlar
- `/data/state.json`: son kontrol, hata ve bildirim durumu
- `/data/latest_price_summary.json`: son dongu fiyat ozeti

## Calisma Akisi

1. Config yuklenir ve dogrulanir.
2. Urun kurallari kontrol edilir.
3. Amazon arama sayfasi kurallari kontrol edilir.
4. Gerekirse Pushover bildirimi gonderilir.
5. State ve ozet dosyalari guncellenir.

## Gelistirme Notu

Hermes mimarisi provider tabanlidir. Her site icin parser/fiyat yakalama kodu ayri dosyadadir:

- `app/hermes/providers/amazon.py`
- `app/hermes/providers/hepsiburada.py`
- `app/hermes/providers/trendyol.py`
- `app/hermes/providers/network.py`
