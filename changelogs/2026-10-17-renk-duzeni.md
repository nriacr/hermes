# 2026-10-17 — Claude — Fırsat/fiyat sayfası renk düzeni (4.20.0)

## Sorumlu ve yetki

- Agent ve konu/çalışma kopyası: Claude, hermes-work (main), renk düzeni.
- İnsan talimatı ve kapsamı: Nuri onaylı renk düzenine "devreye al" dedi; kodlama, test ve kurulum kapsamda. Yalnız fırsatlar/fiyatlar (overview) sayfası; ayarlar ve istatistik sayfalarına dokunulmadı.
- Başlangıç: 4.19.5 (dff3940, kayıt d7c8f96). Sonuç: 4.20.0 (commit bu dosyayla birlikte; kesin numara ha-addon/CHANGELOG.md ve devir notunda).

## Amaç ve davranış

- İstek: renk anlamlı olsun; kırmızı/yeşil yalnız fiyat yönünü, sarı yalnız Depo'yu söylesin; her site kendi renginde; Amazon ile Hepsiburada net ayrışsın.
- Önce: sitelerde pembe/yeşil/turuncu karışık renkler, fırsat kartı yeşil, çizgi grafik yön rengi, logo ve düğmelerde lime-cyan degrade, Depo çizgisi `#ffb347`, hedef çizgisi `#b6ff3b`.
- Sonra: kural ve renk kodları ha-addon/CHANGELOG.md 4.20.0 girdisinde. Palet: `--c` ana, `--hi` = ana %55 beyazla, `--lo` = ana %30 koyu (`#0b0916`) ile karışım.
- Değişen dosyalar:
  - ha-addon/app/hermes/web/static/overview.css — site renkleri ve `--hi/--lo`; lime/pink/violet/cyan değişkenleri kaldırıldı, nötr `--soft`; kart/etiket/sekme/düğme/çubuk/uyarı renkleri; `ov-chg` yeşil/kırmızı; Depo kartı `--c:#ffd35c`; logo nötr degrade (dönen konik halka ve `ov-spin` kaldırıldı); `.ov .priority-*` gri rampa (base.css'e dokunulmadı, ayarlar sayfası aynı).
  - ha-addon/app/hermes/web/pricechart.py — `PAIR_COLORS` = Yeni `currentColor`, Depo `#ffd35c`; hedef çizgisi beyaz kesik (`#f4f2ff`, %80).
  - ha-addon/app/hermes/web/dashboard.py — yalnız docstring.
  - tests/test_web.py — renk metinleri güncellendi, anasayfa gri rampa testi eklendi, logo testi nötr duruma uyarlandı.
  - ha-addon/config.yaml, constants.py — sürüm 4.20.0 (bin/ha-version --change minor).
- Bağımlılık/birleşim: 4.19.5 (başka thread, metin sadeleştirme) üstüne çalışıldı.

## Test ve doğrulama

| Kontrol | Komut/yöntem ve kapsam | Sonuç / kanıt dosyası |
|---|---|---|
| Yerel test | `sh tools/check.sh` (650 test) | Geçti |
| Kurulum öncesi canlı doğrulama | install_rpi.sh (test, yedek, sürüm, sağlık) | Bkz. devir/rapor |
| Görsel doğrulama | Canlı sayfa ekran görüntüsü masaüstü+telefon | Kurulum sonrası yapılır |

Not: onaylı taslak CSS (oneri-taslak.css) bu makineden okunamadı; renk kuralları ve kodları talimat metninden işlendi.

## Yayın ve kurulum

- Kurulum `bin/ha-exclusive` altında `sh tools/install_rpi.sh` ile yapılır.

## Veri, yedek ve geri alma

- Kullanıcı verisine etkisi: yok (yalnız CSS/SVG renkleri).
- Yedek: bugün alınan YENİ tam yedek, adı "Hermes 4.20.0 oncesi renk duzeni 2026-10-17", kod `bea8b4ae`.
- Kod geri alma: `sh tools/prepare_code_rollback.sh dff3940` (rollback_rpi.sh önerilmez). Veri geri yükleme ayrı insan yetkisi gerektirir.

## Kalan durum

- Görsel ince ayar Nuri'nin ekran görüntüsüne göre gerekebilir.
