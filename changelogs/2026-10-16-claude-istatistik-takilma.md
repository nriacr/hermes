# 2026-10-16 — Claude — İstatistik sayfası takılması (tanı + koruma)

## Sorumlu ve yetki

- Agent ve konu/çalışma kopyası: Claude, hermes-work, istatistik sayfası
- İnsan talimatı ve kapsamı: Nuri, Hermes Ayarlar > İstatistik açılmıyor (sekmede dönen simge, sayfa değişmiyor); "ok" ile 4.15.1 tanı/koruma sürümü onaylandı.
- Başlangıç commiti: 7ba824c (canlı 4.14.0). Sürüm: 4.15.1

## Amaç ve davranış

- Somut sorun: PIN'le girilmiş /statistics isteği hiç cevap vermiyor.
- Önceki davranış: sayfa tek parça hazırlanıyordu; bir veri kaynağı takılırsa tüm istek sonsuza dek bekliyordu.
- Sonraki davranış: okumalar, istekler, özet kutuları, Sorun geçmişi ayrı iş parçacığında, 8 sn sınırla; takılan bölüm boş gösterilir, sayfa açılır. 1 sn üstü yavaş veya biten bölümler ve 2 sn üstü toplam süre günlüğe yazılır.
- Kök neden: kanıtlanamadı (Pi'den veriye ve PIN'li sayfaya salt-okunur erişim yok; sahte veride 0,2 sn). Bu sürüm nedeni günlükte görünür kılar; asıl düzeltme günlük okununca ayrı sürüm olur.
- Değişen dosyalar: web/statistics.py (guarded), tests/test_statistics_guard.py (yeni), sürüm dosyaları, CHANGELOG.
- Bağımlılık yok.

## Test ve doğrulama

| Kontrol | Komut/yöntem ve kapsam | Sonuç / kanıt dosyası |
|---|---|---|
| Yerel test | sh tools/check.sh (618 test + yeni 3 test, ruff) | başarılı (kurulum öncesi çalıştırıldı) |
| Canlı davranış | kurulum sonrası eklenecek | bkz. kurulum notu |
| Fiziksel kullanıcı doğrulaması | Nuri sayfayı bir kez açar | bekliyor |

## Yayın ve kurulum

- Commit 9b89c88, GitHub main'e itildi. tools/install_rpi.sh ile 4.15.1 kuruldu; add-on started, sürüm 4.15.1, runtime sağlıklı (günlük yedek doğrulaması kurulum aracı tarafından yapıldı, yedek 42c055d8).
- Canlı doğrulama: sayfa PIN'li olduğundan ben açamadım; Nuri'nin bir kez açıp süreyi söylemesi, ardından günlükteki 'İstatistik bölümü' satırları okunacak.

## Veri, yedek ve geri alma

- Kullanıcı verisine etkisi: yok (yalnız okuma ve günlük).
- Geri alma: sh tools/prepare_code_rollback.sh 7ba824c (rollback_rpi.sh kullanılmaz; installer'ın yazdığı rollback_rpi.sh önerisi kullanılmadı).

## Kalan durum

- Kök neden günlükten okunacak.
