# 2026-10-17 — Claude — Hermes internet kullanımı ölçümü (4.18.0)

## Sorumlu ve yetki

- Agent ve konu/çalışma kopyası: Claude, `hermes-bytes` (dal `claude-bytes`).
- İnsan talimatı ve kapsamı: Nuri, Pi'nin internet kullanımını analiz etmeyi, özellik kaybettirmeden iyileştirmeyi ve önce Hermes'in internet kullanımını ölçen bir yapı kurmayı istedi; sonucu istatistik sayfasında bilgi amaçlı gösterge olarak görmek istedi. Önizleme sonrası "kur" dedi. Bu iş yalnız ölçüm ekler, hiçbir optimizasyon yapmaz.
- Tarih ve sürüm: 2026-10-17, 4.17.1 → 4.18.0 (başlangıç commiti ee0bdb4).

## Amaç ve davranış

- Somut istek: Hermes'in sitelere yaptığı internet trafiği tahmin değil gerçek sayı olsun (site ve gün bazında MB).
- Önceki davranış: istek sayısı/süresi kaydediliyordu, bayt kaydedilmiyordu. Pi ölçümü (Supervisor konteyner sayacı, tek 2 dk örnek) Hermes için yaklaşık 3,4 GB/gün inen gösterdi; site kırılımı bilinmiyordu.
- Sonraki davranış: yeni `traffic.py` her yanıtı sayar (requests: ağdan okunan sıkıştırılmış gövde + başlıklar, açılmış gövde, istek satırı+başlık+gövde; curl_cffi: download_size+header_size, request_size+upload_size). Yönlendirme basamakları ayrı sayılır. Site, iş parçacığına bağlı (`traffic.bind(site)`); bağlı olmayanlar "diger". Dakikada bir `history.record_traffic` ile hermes.db `traffic(day, site, requests, wire_rx, body_rx, tx)` tablosuna eklenir (UPSERT), yazma başarısızsa sayılar bir sonraki yazmaya saklanır. 10 dakikada bir günlük satırı. İstatistik sayfasında yeni bölüm (guarded, 8 sn sınırlı).
- Değişen dosyalar: `ha-addon/app/hermes/traffic.py` (yeni), `history.py` (tablo, record_traffic, read_traffic, 30 gün budama), `providers/http.py` (MeasuredSession sayar), `providers/hepsiburada/__init__.py`, `providers/beymenclub.py`, `providers/hm.py`, `providers/amazon/client.py` (curl sayımı, sayan oturumlar), `monitor/cycle.py` (site bağlama, Depo şeridi sayan oturum, sayaç başlatma), `notifier.py` (Pushover), `telegram/listener.py` (kısa link), `web/statistics.py` + `web/static/base.css` (bölüm), `tests/test_traffic.py` (13 yeni test), `docs/previews/u9-*.png` (önizleme, örnek veri), `config.yaml`/`constants.py` (sürüm), `CHANGELOG.md`.
- Bağımlılıklar ve diğer işler: yeni bağımlılık yok. "Pi SSD kullanımı" thread'inin olası state.json/yazma değişiklikleriyle çakışma yok (ayrı dosyalar); main'de yeni commit yoktu.
- Bilinen sınırlar: TLS el sıkışması, DNS, TCP/IP başlıkları ve Telegram MTProto (Telethon) sayılmaz; gerçek hat kullanımı sayılandan yüksektir. curl yönlendirme zincirinde yalnız son aktarım boyutları bilinir.

## Test ve doğrulama

| Kontrol | Komut/yöntem ve kapsam | Sonuç / kanıt dosyası |
|---|---|---|
| Yerel test | `python -m unittest discover` (634 test), `ruff check` | İlk koşuda 2 test başarısızdı, ikisi de değişiklikten önceki main'de (ee0bdb4) de aynıydı: test_app.test_monitor_panel_and_reset_work_together ve test_monitor.test_commands_are_applied_between_cycles_and_can_start_one_now. Neden: günlük özet 09:00-22:00 arası gönderildiği için bu saatlerde fazladan bildirim/outbox satırı çıkıyor (gece geçerler). Düzeltme: iki testte `daily_summary_enabled=False`; ürün kodu değişmedi. Düzeltmeden sonra paket temiz (sonuç aşağıda). |
| Yeni testler | `tests/test_traffic.py` (gerçek gzip'li yerel sunucu, yönlendirme, iş parçacığı bağlama, bozuk yanıt, depolama, sayfa) | 13/13 geçti |
| Önizleme | örnek veriyle statistics sayfası, masaüstü + telefon görüntüsü | docs/previews/u9-internet-*.png |
| Canlı davranış | aşağıdaki "Canlı doğrulama" bölümünde |  |

## Yayın ve kurulum

- Yayın/kurulum bilgileri aşağıdaki "Canlı doğrulama" bölümünde (kurulum sonrası doldurulur).

## Veri, yedek ve geri alma

- Kullanıcı verisine etkisi: yalnız yeni `traffic` tablosu eklenir; fiyatlar, takipler, bildirim hafızası, outbox değişmez. Eski sürümler tabloyu yok sayar. İstatistik sıfırlama bu tabloya dokunmaz.
- Kullanılan mevcut yedek: 42c055d8 (Home Assistant manuel tam yedek, 16 Ekim 2026 18:50 UTC). Yeni yedek alınmadı (Hermes AGENTS.md: sahip açıkça istemedikçe yeni yedek yok).
- Kod geri alma: `sh tools/prepare_code_rollback.sh ee0bdb4` (4.17.1), ardından push ve `tools/install_rpi.sh`.
- Veri geri yükleme ayrı insan yetkisi gerektirir; gerekmez.

## Canlı doğrulama (2026-10-17)

- Yayın: GitHub main 73d3e03 (4.18.0) + 39f3f4a (iki zamana bağlı testin düzeltmesi; ürün kodu aynı). Kurulan kayıt: 39f3f4a.
- Kurulum: `HA_AGENT=claude sh tools/install_rpi.sh` (bin/ha-exclusive altında). İlk kurulum denemesi `tools/check.sh` içindeki 2 teste takıldı (gündüz saatlerinde günlük özet fazladan bildirim üretiyor); kontrol atlanmadı, testler düzeltilip yeniden çalıştırıldı, ikinci denemede tüm paket geçti ve kuruldu (16:16 yerel). Hermes 4.18.0 başladı.
- Kullanılan tam yedek: eb87939c (install_rpi.sh'nin doğruladığı en yeni yedek, 17 Ekim 02:44 UTC). Yeni yedek alınmadı.
- Çalışma zamanı okuması: version 4.18.0, healthy true, 14 aktif takip, 702 fiyat noktası, 369 state girişi (kayıp yok), outbox sent 134 / failed 1 (kurulum öncesinden), açık sorun 2, Amazon/Hepsiburada/Togg okumaları 13-37 sn önce.
- İlk ölçüm (16:16-16:26 yerel, 10 dk): indirilen 19,25 MB (açılmış 107,75 MB), gönderilen 0,28 MB, 318 istek; Amazon 13,52 MB/57 istek (yaklaşık 237 KB/istek), Hepsiburada 5,46 MB/249 istek (yaklaşık 22 KB/istek), Togg 0,27 MB/9 istek. Hız yaklaşık 2,8 GB/gün (indirilen, sıkıştırılmış). Veritabanı yazma satırı "internet kullanımı kayıtları SQL 9x" görüldü (dakikada bir toplu yazma).
- Çapraz kontrol: Supervisor konteyner sayacı aynı sürede rx 25,3 MB, tx 1,65 MB. Sayılan 19,5 MB bunun yaklaşık %77'si; fark TLS/TCP başlıkları, el sıkışmaları, DNS ve Home Assistant ile yerel konuşmadır. Sayaç makul ve alt sınırdır.
- Kaynak: bellek 420 MB, CPU %26 (kurulumdan önce %26 ve 495 MB); günlükte Traceback/yazma hatası yok.
- Doğrulanamayan: istatistik sayfası PIN'li olduğu için sayfanın açılma hızını ve yeni bölümün canlı görünümünü ben görmedim; ortam testiyle (önizleme) doğrulandı. Nuri sayfayı açıp bölümü görmeli.
- Gözlem (bu işin kapsamı dışı): aynı günlükte "Yazma özeti" state.json SQL 111,9 MB / 10 dk (151 yazma) gösteriyor; SSD yazma konusu diğer thread'in işi.
- Geri alma: `sh tools/prepare_code_rollback.sh ee0bdb4` (4.17.1 koduna dönüş, yeni yama sürümü olarak), ardından push ve `tools/install_rpi.sh`. Veri geri yüklemesi (`rollback_rpi.sh eb87939c`) yalnız Nuri'nin açık izniyle ve gerekmez: traffic tablosu eski sürümlerce yok sayılır.
