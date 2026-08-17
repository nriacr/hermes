# Hermes

Hermes, Home Assistant üzerinde çalışan çok siteli fiyat ve Telegram fırsat takip add-on'udur.

## Cursor ile geliştirme

Cursor'a geçiş için gerekli kalıcı proje bağlamı repoya eklenmiştir:

- [`AGENTS.md`](AGENTS.md): bağlayıcı mimari, güvenlik, test ve yayın kuralları
- [`.cursor/rules/hermes.mdc`](.cursor/rules/hermes.mdc): Cursor'un her sohbette otomatik okuyacağı kurallar
- [`docs/CURSOR_HANDOFF.md`](docs/CURSOR_HANDOFF.md): güncel mimari, davranışlar, site algoritmaları ve riskler
- [`docs/CURSOR_START_PROMPT.md`](docs/CURSOR_START_PROMPT.md): ilk Cursor Agent sohbetine yapıştırılacak hazır metin

Cursor'da bu repo klasörünü açın ve başlangıç metnini ilk Agent sohbetine
gönderin. Kullanıcı verileri ve gizli anahtarlar repoda değil, Home Assistant
`/data` alanında kalır.

Takip edilen alanlar:
- `takip_edilenler`: tek kayıt altında en fazla 5 ürün veya arama linki
- Hermes linkten siteyi ve link tipini otomatik algılar
- Ürün linklerinde `name` boş bırakılabilir; Hermes ürün adını linkten okur. Arama linklerinde `name`, aranacak keyword olarak zorunludur.
- Amazon ürün linklerinde `Varyasyonları ekle` seçilirse, stokta olan renk seçenekleri ayrı satırlar halinde takip edilir.
- Ana ekrandaki `Test` sayfası bağlantıyı anlık ve geçici olarak okur; arama anahtar kelimesi, beden, hariç tut ve varyasyon seçenekleri bu test için ayrıca uygulanabilir.
- Telegram kanalları: keyword ve exclude keyword tabanlı fırsat bildirimi
- Telegram Kayıtlı Mesajlar: bağlantıyı gönder, Hermes hedef fiyatı sorup takibi ekler
- Arama bağlantılarında en fazla 60 sonuç otomatik taranır.

Bildirimler Pushover üzerinden gönderilir.  
Ingress paneli üzerinden durum, özet tablo ve test bildirimi yönetilebilir. İsteğe bağlı public panel; güvenli bir token ve ters proxy/tünel ile dışarıdan da kullanılabilir.

## Home Assistant Repository

Home Assistant > Add-on Store > Repositories alanına:

`https://github.com/nriacr/hermes`

ekleyerek kurulabilir.

Detaylı kullanım, veri dosyaları ve geliştirme kontrolleri için [add-on kılavuzuna](ha-addon/README.md) bakabilirsin.
