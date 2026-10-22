# Ortak iş durumu

Kısa ve güncel tutulur; iş sonunda ilgili madde güncellenir. Tarih/Agent,
sonuç, gerçek kontrol ve yayın/kurulum bilgisi yeterlidir. Ayrıntı Git geçmişinde.

## Yapılanlar

- 2026-10-22 · Agent: Claude · 4.20.8 (Nuri: telefonda hâlâ 3-4 sn siyah): uzun boşta sonra özet bloğu ~3-8 sn hazırlanıyordu (durum verisini açmak); panel kullanıldıkça arkada 90 sn'de bir yenileme eklendi. Kontrol: 653 test başarılı. Kurulum: 4.20.8 canlı, install_rpi.sh doğrulaması başarılı.
- 2026-10-20 · Agent: Claude · 4.20.2 (Nuri: telefonda Hermes açılışı 2-3 sn siyah): ana sayfa özet bloğu sunucuda 1,6-7,8 sn üretiliyordu; kısa önbellek + eski kopyayı anında verip arkada yenileme eklendi, bildirim/tarama değişmedi. Kontrol: tools/check.sh 652 test başarılı. 4.20.2 kuruldu (önbellekli açılış 0,1-0,2 sn, ilk soğuk açılış ~9 sn); 4.20.3 ile eski kopya süresi 30 dk, 4.20.3 kuruldu, install_rpi.sh doğrulaması başarılı.
- 2026-10-19 · Agent: Claude · 4.20.1: Telegram keyword bildirimleri sessiz (Pushover öncelik -1); fiyat fırsatı bildirimleri değişmedi. Kontrol: tools/check.sh 651 test başarılı. Kurulum: 4.20.1 canlı, install_rpi.sh doğrulaması başarılı; kod geri dönüşü: sh tools/prepare_code_rollback.sh 6918e21 (veri geri yükleme gerekmez); günlük yedek 00ed4add.
- 2026-10-18 · Agent: Codex · Kredi verimliliği ortak öncelik olarak kurallara
  işlendi; iş başına rapor yerine bu listeye geçildi. Belge değişikliği;
  uygulama sürümü ve canlı sistem değişmedi. Kontrol: bağlantılar ve Git farkı başarılı.

## Planlananlar

- Bu düzenleme kapsamında yeni geliştirme planlanmadı; mevcut diğer işler hakkında çıkarım yapılmaz.

## Yapılamayanlar / engeller

- Bu düzenleme kapsamında engel yok. Canlı test/kurulum belge işi için uygulanmaz.

## Son kontroller ve yayın

- Kapsam: ortak kurallar, yönlendirme/şablon ve bu liste. Gerekli kontrol:
  belge bağlantıları ve git diff --check başarılı; runtime testleri gerekmiyor.
- Yayın bilgisi bu dosyanın Git commitinde ve origin/main durumunda görülür;
  yalnız yayın sonucunu tekrar raporlamak için ayrı belge/commit oluşturulmaz.
- Yedek/geri alma: canlı yazma yok; sistem yedeği gerekmiyor. Belge commitini
  geri almak yeterli; kimlik bilgileri ve kullanıcı verisi değişmedi.
