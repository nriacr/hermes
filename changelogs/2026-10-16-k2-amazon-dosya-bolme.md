# 2026-10-16 — Claude — K2 Amazon sağlayıcı dosyasının bölünmesi (4.11.3)

## Sorumlu ve yetki

- Agent: Claude. İnsan talimatı: Nuri optimizasyon önerilerinin tamamını onayladı (15 Ekim 2026); K2 davranışsız taşımadır.

## Amaç ve davranış

- providers/amazon/__init__.py 676 → ~480 satır. Taşınanlar (kod değiştirilmeden): WatchRhythm + RhythmMixin (rhythm.py: due/busy/sweep/aralıklar), PageCacheMixin + önbellek sabitleri (pagecache.py), is_platform_seller/most_specific_matches/offers_from_search_results (matching.py). AmazonProvider mixin'leri miras alır; WatchRhythm, offers_from_search_results ve is_platform_seller paket yolundan hâlâ içe aktarılır.
- Bilinçli olarak bölünmedi: iter_product ve read_search (iç içe durum; bölmek davranış riski taşır, K2 kapsamı dışı bırakıldı), monitor/cycle.py.
- docs/ARCHITECTURE.md Amazon bölümü güncellendi.

## Test ve doğrulama

- sh tools/check.sh: ruff + 612 test OK (testler değişmeden geçti, time.monotonic yamaları aynen çalışıyor). Canlı kanıt aşağıda.

## Veri, yedek ve geri alma

- Veri değişmez. Geri alma: 4.11.2 kaydıyla prepare_code_rollback.sh.

## Canlı kanıt

- install_rpi.sh (yedek b160df65): 4.11.3 started, sağlık ok, veri sayıları korundu, Amazon son okuma 29 sn önce (zamanlama normal), günlükte Traceback yok.
