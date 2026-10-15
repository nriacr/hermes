# 2026-10-16 — Claude — Tüm verinin SQLite'a alınması (4.12.0)

## Sorumlu ve yetki

- Agent: Claude. İnsan talimatı: Nuri "tüm datayı, geçmişi, verileri sqlite e taşı" dedi (15 Ekim 2026; K5 seçeneği c).

## Amaç ve davranış

- Önce: state.json ve latest_price_summary.json zaten veritabanı snapshot'ından okunuyordu (JSON yalnız dışa aktarımdı); geçmiş/okuma/istek/bildirim kuyruğu SQLite'taydı. Yalnız Amazon erişim+çerez, Telegram durum/görülen mesaj/hata olayı/hızlı ekleme/giriş durumu dosya-asıllıydı.
- Sonra: storage.SQL_BACKED kümesindeki 9 dosya (state, özet, amazon_access, amazon_cookies, status, seen_messages, error_events, telegram_quick_add, login_state) snapshots tablosunda asıl kaynak; save_json önce SQL, sonra JSON dışa aktarımı yazar. adopt_json_files (app.main başında) snapshot'ı olmayan mevcut dosyaları bir kez veritabanına alır (idempotent, dosyalar korunur).
- Bilinçli dışarıda: options.json (Home Assistant yönetir), web_access.json (doğrudan okunan güvenlik durumu), Telethon oturum dosyası (kendi SQLite'ı), cycle_history.json (geçmiş zaten `cycles` tablosunda; JSON yalnız ilk göç için okunur).
- Geri dönüş notu: eski sürüme dönülürse eski sürüm JSON dosyalarını okur (yazılmaya devam ettiği için güncel); eski sürümde bu dosyalara yapılan değişiklikler yeniden yükseltmede veritabanını geçmez (küçük dosyalar; kabul edildi).
- Dosyalar: storage.py, app.py, tests/test_diagnostic_log.py (yeni test), tests/test_amazon_access.py (test dosyayı doğrudan yazıyordu; save_json'a çevrildi).

## Test ve doğrulama

- sh tools/check.sh: ruff + 613 test OK. Canlı kanıt aşağıda.

## Veri, yedek ve geri alma

- Veri kopyalanır, silinmez. Geri alma: 4.11.3 kaydıyla prepare_code_rollback.sh veya rollback_rpi.sh.

## Canlı kanıt

- (kurulumdan sonra eklenecek)
