# Ortak iş durumu

Kısa ve güncel tutulur; iş sonunda ilgili madde güncellenir. Tarih/Agent,
sonuç, gerçek kontrol ve yayın/kurulum bilgisi yeterlidir. Ayrıntı Git geçmişinde.

## Yapılanlar

- 2026-10-23 · Agent: Claude · 4.20.19: lxml geri alındı (4.20.17-18'de Amazon sayfa çözümleme 1,6 → 14-15 sn, işlemci 0,8-0,9 sn; tek parça besleme de düzeltmedi). Hariç sayfa hafızası çalışıyor (yeniden başlatma sonrası 0 boşa okuma). Kontrol: 655 test başarılı. Kurulum: bekliyor.
- 2026-10-23 · Agent: Claude · 4.20.17 (Nuri: Amazon'u da hızlandır, önce yedek): ek yedek ec840e92 (kısmi Hermes, ek). Ölçüm (4.20.16): Amazon sayfası indikten sonra ~6,7 sn işleniyor, 1,6 sn'si HTML okuyucu. lxml okuyucu, hariç sayfa hafızasının diskte saklanması (/data/amazon_excluded_pages.json), özet günlüğü 5 dk sınırı, adım adım süre kaydı. 'Hariç seçeneği hiç açmama' eklenmedi (komşu seçenek kaybı riski). Kontrol: 655 test başarılı. Kurulum: 4.20.17 canlı; lxml parça parça beslendiği için çözümleme 1,6 → 14 sn'ye çıktı, 4.20.18 ile tek parçaya geçildi.
- 2026-10-23 · Agent: Claude · 4.20.15 (Nuri "2": 1 dk'lık Hepsiburada kartları ~12 dk'da bir yenileniyordu): kart her taramada hariç tutulan seçenekler dahil tüm varyant sayfalarını açıp sonuçları kart bitince yazıyordu (~4 dk/kart). Artık hariç tutulan varyant açılmıyor, her varyant okunur okunmaz tabloya yazılıyor. Kontrol: 654 test başarılı. Kurulum: 4.20.15 canlı; canlıda 9 varyant açılmadı, Hepsiburada 1 dk kartları 2-3 dk'da bir yenileniyor, tablo yaşları ≤2 dk. Amazon 1 dk kartlarında 7-11 dk yaş sürüyor (ayrı konu).
- 2026-10-23 · Agent: Claude · 4.20.14 (Nuri: ana sayfa 1 dk'lık ürünlerde 8-9 dk eski veri): 4.20.9'un bellekten durum okuması panelde eski kaydı gösteriyordu; geri alındı, panel yine veritabanını okuyor. Kontrol: 653 test başarılı. Kurulum: 4.20.14 canlı.
- 2026-10-23 · Agent: Claude · 4.20.13 (Nuri: her açışta güncel tablo, siyah ekran yok; 4.20.9-4.20.12 ara sürümler): asıl neden fiyat/hedef geçmişinin satır satır okunmasıydı, fiyat kontrolü sürerken panel her satırda sıra bekliyordu (geçmiş 5-9 sn, işlemci 0,1 sn). 4.20.12 geçmişi tek parça okuyor (0,1-0,4 sn); 4.20.13 eski kopya gösterimini ve arka plan yenilemesini kaldırdı, her açılış taze hazırlanıyor (0,5-1 sn). Durum verisi bellekteki son kayıttan okunuyor (4.20.9), 1,5 sn'yi aşan hazırlık günlüğe adımlarıyla yazılıyor. Kontrol: tools/check.sh 653 test başarılı. Kurulum: 4.20.13 canlı, install_rpi.sh doğrulaması başarılı.
- 2026-10-22 · Agent: Claude · 4.20.8 (Nuri: telefonda hâlâ 3-4 sn siyah): uzun boşta sonra özet bloğu ~3-8 sn hazırlanıyordu (durum verisini açmak); panel kullanıldıkça arkada 90 sn'de bir yenileme eklendi. Kontrol: 653 test başarılı. Kurulum: 4.20.8 canlı (sonra 4.20.13 ile değiştirildi).
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
