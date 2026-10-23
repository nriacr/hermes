# Hermes 23 Ekim hız ve tutarlılık denetimi

Agent: Claude · Canlı sürüm: 4.20.23 · Ölçüm zamanı: 23.10.2026 14:07 (TSİ)
Yedek: günlük tam fb7f3635 + ek kısmi ec840e92 (lxml denemesi öncesi)

## Kısa sonuç

- Kaçan fırsat: yok. Son ~20 saatlik günlükte (3.567 okuma) hiçbir ürün hedef fiyatın altına inmedi. Bildirim yolu (tarama sırasında kuyruğa alma) bugün hiç değiştirilmedi.
- Eski fiyat: tablo her açılışta veritabanından o anki veriyle hazırlanıyor; 1 dk'lık Amazon ve Hepsiburada kartlarının hepsi ölçüm anında 3 dk'dan yeni.
- Engel/ban: bugün Amazon'da engel 0 (bugünkü engel dalgası 0); Hepsiburada'da captcha/403/429 yok. Hepsiburada istek sayısı azaldı, Amazon'un saatlik isteği bir miktar arttı ama Hermes'in kendi sınırının (500/saat penceresi) çok altında.

## Süre kazancı (aynı kalite, ölçülen)

| Ölçüm | Bu sabah | Şimdi |
|---|---|---|
| Hepsiburada 1 dk kartı, ortalama yenilenme aralığı | 7,5 dk | 1,8 dk |
| Amazon kartı, ortalama yenilenme aralığı | 8,8 dk | 1,8 dk |
| Amazon sayfası işleme (indikten sonra) | ~9,4 sn | ~3,5 sn |
| Amazon Depo (ikinci el) doğrulama | ~2,3 sn/sayfa | ~0,15 sn/sayfa |
| Yeniden başlatma sonrası boşa Amazon okuması (ilk 10 dk) | 28 / 53 | 0-7 |
| Ana sayfa açılışı (telefon, siyah ekran) | 2-9 sn | çoğu 1,3-1,6 sn |
| Ana sayfadaki tablo | 30 dk'ya kadar eski kopya gösterebiliyordu (4.20.3-4.20.12 arası) | her açılışta güncel |

Yenilenme aralıkları günlükteki "Kontrol edildi" satırlarından ölçüldü: sabah 07:00-10:00 (değişiklik öncesi), öğleden sonra 13:30-14:10.

## İstek hızı ve engel riski

| Site | Değişiklik öncesi (saatlik) | Şimdi (13:00-14:00) | Not |
|---|---|---|---|
| Amazon | ~272 istek | ~309 istek | İşleme hızlandığı için arttı; Hermes'in Amazon sınırı aynen geçerli, engel 0 |
| Hepsiburada | ~352 sayfa | ~253 sayfa | Hariç tutulan seçenekler artık açılmıyor; daha az yük, daha düşük engel riski |

Öneri: Amazon'un artan hızını birkaç gün izlemek yeterli; engel görülürse mevcut otomatik yavaşlatma devreye giriyor.

## Bugünkü değişikliklerin tek tek denetimi

1. **Ana sayfa (4.20.2-4.20.14):** fiyat geçmişi tek parçada okunuyor (aynı sıra, testle doğrulandı). Eski kopya gösterimi kaldırıldı. 4.20.9'daki "bellekten okuma" geri alındı. Bildirimlere etkisi yok.
2. **Hepsiburada (4.20.15, 4.20.22):** her seçenek okunur okunmaz tabloya yazılıyor. Kartın hariç terimi seçeneğin kendi adında geçiyorsa o sayfa açılmıyor. Ana sayfa başlığı artık bu karara karışmıyor; atlanan 15 seçeneğin hepsi günlükten kontrol edildi, tamamı gerçekten hariç terimi taşıyor. Hata durumları önceki gibi.
3. **Amazon hariç hafızası (4.20.17):** zaten hariç tutulan sayfaların komşu listesi diske yazılıyor (/data/amazon_excluded_pages.json). Süresi (15-45 dk) ve "kartın hariç listesi değişirse yeniden oku" kuralı aynı.
4. **lxml denemesi (4.20.17-18):** Pi'de yavaşlattı, 4.20.19'da tamamen geri alındı.
5. **Yüksek fiyat uyarısı (4.20.21):** sayfada bir kez ve tek geçişte bakılıyor. Gizli, ikinci el ve betik metinlerini dışlama kuralı aynı (testli).
6. **Amazon sayfa içi dizin (4.20.22-23):** basit seçiciler dizinden, diğerleri eski yöntemle yanıtlanıyor. Parser'ın kullandığı bütün seçiciler için iki yöntemin aynı sonucu verdiği test ediliyor. Silinen bölümler (ikinci el bölümü) dizinden de düşüyor.
7. **Depo doğrulama (4.20.23):** iç içe kutularda yalnız en içteki satır okunuyor (testli; geliştirme sırasında ters çevrilmiş hâli yakalanıp düzeltildi). Depo kartları panelde güncel geliyor.
8. **Günlük:** özet tablosu en çok 5 dk'da bir; Amazon adım süreleri 20 sayfada bir, yavaş panel hazırlığı (1,5 sn üstü) tek satır.

Kontrol: tools/check.sh 659 test başarılı; ruff temiz.

## Kalan riskler ve öneriler

- Sayfa içi dizin sentetik sayfada ve mevcut testlerde doğrulandı, gerçek Amazon sayfasında ayrıca karşılaştırılmadı. Öneri: 50 sayfa boyunca iki yöntemi yan yana çalıştırıp farkları günlüğe yazan geçici bir "gölge karşılaştırma" (davranış değişmez).
- Amazon saatlik isteği ~%14 arttı; birkaç gün engel sayacı izlenmeli.
- Ölçüm satırları açık; hafif ama istenirse kaldırılabilir.
