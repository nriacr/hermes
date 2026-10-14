# 2026-10-14 — Claude — Silinen takip satırları anasayfada kalıyordu

## Sorumlu ve yetki

- Agent ve konu/çalışma kopyası: Claude, `hermes-deleted-watch-work`, dal `claude/deleted-watch-summary`.
- İnsan talimatı ve kapsamı: Nuri, 14 Ekim 2026: arama kartından silinen ürünlerin sonuçları anasayfada duruyor, düzelt; silince tablo kendiliğinden güncellensin; Hermes elle yeniden başlatılınca farklı sıklıktaki tüm kartlar bir kez taransın. ChatGPT'nin yazdığı bekleyen taslak yerine bu düzeltmenin kullanılması ve "bu düzeltmeyi sen kur" talimatı.
- Sürüm: 4.2.1 → 4.2.2 (`bin/ha-version`, patch). Temel commit 926ad1f.

## Amaç ve davranış

- Sorun: Silinen veya kapatılan kartın eski fiyat/stok satırları anasayfada saatlerce kalıyordu.
- Neden: Tablo dosyası her turda önceki dosyayla birleştiriliyor; hiçbir aktif kart sahiplenmese de eski satır dosyada kalıyordu.
- Önceki davranış: Satırlar yeniden başlatmada da kalıyordu.
- Sonraki davranış: Açılışta tablo yalnız aktif kartların sahip olduğu satırlara indirilir. Ayarlardan takip silmek Hermes'i zaten yeniden başlattığı için tablo o açılışta güncellenir. Kalan satırların okuma saati, fiyat geçmişi, min/maks ve bildirim hafızası değişmez. Açılışta her aktif kartın bir kez okunması 4.1.1'den beri vardı, değişmedi.
- Dosyalar: `monitor/summary.py` (`drop_unconfigured_summary_rows`), `monitor/cycle.py` (açılışta çağrı), `tests/test_deleted_watch_rows.py` (yeni), `tests/test_monitor.py` (sahipsiz satır artık tutulmadığı için "Başka site" gerçek bir takip olarak tanımlandı), `ha-addon/CHANGELOG.md`, `docs/ARCHITECTURE.md`, `ha-addon/config.yaml` ve `constants.py` (sürüm).
- Ayrıca `AGENTS.md`, `tools/install_rpi.sh`, `tools/rollback_rpi.sh` ChatGPT'nin hazırladığı koordinatörsüz akış sürümüyle birebir alındı (hermes-work içinde commitlenmemiş duruyordu); kurulum bu betiklerle yapılabilsin diye.
- Bağımlılık: Başka agent işiyle çakışma yok. Bekleyen eski iş (aynı konuda ChatGPT taslağı, dal `codex/deleted-watch-summary`) kullanılmadı; koordinatör kaldırıldığı için kuyruktan da çalışmaz.

## Test ve doğrulama

| Kontrol | Komut/yöntem ve kapsam | Sonuç / kanıt |
|---|---|---|
| Yerel test | `tools/check.sh`: ruff + 560 birim test (yeni 2 test dahil), kurulum betiği de yeniden çalıştırır | Geçti (ön çalıştırma); kurulum çıktısı aşağıda |
| Canlı davranış | `install_rpi.sh` canlı okuma ve `/health` | Geçti (aşağıda) |

## Yayın ve kurulum

- Yayımlandı: `origin/main` 926ad1f → 6c4179f (4.2.2), 14 Ekim 2026 ~23:05.
- Kurulum: `bin/ha-exclusive sh tools/install_rpi.sh`. İlk deneme Home Assistant mağaza yenilemesi zaman aşımı yüzünden durdu (canlıya dokunmadı); aynı komut tekrarlanınca 4.2.2 kuruldu, `/health` ok.
- Canlı kanıt: Hermes 4.2.2, sağlıklı, 14 aktif kart, 331 durum kaydı, 474 fiyat noktası, 0 açık olay, yeni sürümle tamamlanmış okumalar var. Anasayfa şu an 33 ürün / 6 stokta yok gösteriyor.

## Veri, yedek ve geri alma

- Kullanıcı verisi: fiyat geçmişi, takipler, bildirim hafızası değişmiyor; yalnız tablo dosyasındaki sahipsiz satırlar siliniyor.
- Yedek: `install_rpi.sh` Home Assistant'ın mevcut günlük tam yedeğini doğrular.
- Geri alma: `sh tools/rollback_rpi.sh YEDEK_KODU` veya önceki sürüme dönmek; tablo bir sonraki turda kendini yeniden kurar.

## Kalan durum

- Silinen ürünlerin tablodan gittiğini Nuri anasayfadan gözle doğrulayacak (sahipsiz satırlar listesi canlıda ayrıca okunamadı).
- `hermes-work` klasöründe `AGENTS.md`, `tools/install_rpi.sh`, `tools/rollback_rpi.sh` commitlenmemiş değişiklik olarak duruyor; içerikleri artık main ile aynı. `git checkout -- <3 dosya>` sonra `git pull` yeterli.
