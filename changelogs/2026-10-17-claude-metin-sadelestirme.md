# 2026-10-17 — Claude — Sayfa metinlerinin sadeleştirilmesi (4.19.5)

## Sorumlu ve yetki

- Agent: Claude. Çalışma kopyası: `hermes-work` (main).
- İnsan talimatı: Nuri, `reports/hermes-metin-onerileri-2026-10-17.md` listesindeki tüm önerileri onayladı ("kur"). K = kaldır, Kı = kısalt, Tut = kalsın; onay pencereleri "… Devam?" şeklinde kısaltıldı.
- Tarih ve sürüm: 2026-10-17, 4.19.4 → 4.19.5 (başlangıç commiti 92cafbb). Sürüm `bin/ha-version` ile ayrıldı.

## Amaç ve davranış

- Önce: sayfalarda birçok açıklama/yardım cümlesi vardı.
- Sonra: açıklamalar kaldırıldı, boş kalan sarmalayıcı öğeler temizlendi. Veri, hata mesajı, silme/sıfırlama/yeniden başlatma onayları durur (kısaltıldı). Bildirim, veri toplama ve tarama mantığına dokunulmadı.
- Değişen dosyalar (yalnız metin): `web/dashboard.py`, `web/settings.py`, `web/statistics.py`, `web/opportunities.py`, `web/pin.py`, `web/pages.py`, `web/static/settings.js`, `web/static/confirm.js`; testler `tests/test_web.py`, `tests/test_web_access.py`, `tests/test_traffic.py` (kaldırılan metni arayan beklentiler güncellendi); `config.yaml`, `constants.py` (sürüm); `CHANGELOG.md`.
- Kaldırılanlar: anasayfa başlık altı; kart "Fiyat geçmişi ilk değişiklikten…" notu; Ayarlar'da desteklenen siteler, kart altı öncelik/satıcı notu, toplu işlem notu, 3 zamanlama notu, günlük özet notu, yeniden başlatma sayfası ilk cümle; İstatistik giriş, "kontrol sıklığı" kutu altı, hata dönemleri notu, internet kutu altı üç satır; Fırsat geçmişi giriş; PIN: 15 dk kilit cümleleri (iki yerde), "unuttuysan" cümlesi (bağlantı duruyor), "4 haneli PIN…" cümleleri, "PIN değişince…" altbilgisi.
- Kısaltılanlar: boş durumlar → "Yok." / "Henüz ölçüm yok."; alt bar → "Uygulayınca Hermes yeniden başlar."; kaydetme → "Kaydediliyor, Hermes yeniden başlıyor…"; HTTP uyarısı → "HTTP (şifresiz bağlantı)"; internet dipnotu; tüm onaylar "Devam?" ile biter (Bildirim Sıfırla, Min/Maks Sıfırla, yeniden başlat, istatistik geçmişi sil, takip sil, genel varsayılan).
- Korunanlar: "Ayarlarda hata var…", "Ayar sayfası eskimiş…", "Önce en az bir kart seç.", açık sorunlar, "Çok fazla deneme…", hata mesajındaki "Desteklenen siteler" listesi.

## Test ve doğrulama

| Kontrol | Komut | Sonuç |
|---|---|---|
| Tam paket | `sh tools/check.sh` (ruff + 650 test) | OK |

## Yayın, kurulum ve canlı doğrulama

- Aşağıda kurulumdan sonra doldurulur.

## Veri, yedek ve geri alma

- Kullanıcı verisine etkisi yok (yalnız sayfa metni).
- Kurulum öncesi adlandırılmış tam HA yedeği: **8c6a7a22** ("Metin sadeleştirme öncesi - 2026-10-17", full, Hermes içerir, 476,83 MB).
- Kod geri alma: `sh tools/prepare_code_rollback.sh 92cafbb` (4.19.4), ardından push ve `tools/install_rpi.sh`. Veri geri yüklemesi gerekmez.
