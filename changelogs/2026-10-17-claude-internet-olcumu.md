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
