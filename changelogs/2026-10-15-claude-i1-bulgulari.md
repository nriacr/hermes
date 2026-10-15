# 2026-10-15 — Claude — I1 ölçüm bulgularından küçük sürüm (4.2.7)

## Sorumlu ve yetki

- Agent ve konu/çalışma kopyası: Claude, hermes-work (main).
- İnsan talimatı ve kapsamı: Nuri, reports/hermes-optimizasyon-onerileri-2026-10-15.md maddelerinin tek tek yapılmasını onayladı; ilk adım I1 bulgularından iki düzeltme.
- Sürüm: 4.2.6 → 4.2.7.

## Amaç ve davranış

- Sorun: 15 Ekim 15:53–16:32 DNS kesintisinde 45 kez Chromium yedeğine düşüldü (~20–25 sn/okuma, faydasız); Hepsiburada Premium uyarısı günde ~3.080 satır.
- Önceki: curl hatasında (DNS dahil) tek seferlik Chromium okuması. Uyarı her okumada yazılıyordu.
- Sonraki: DNS/bağlantı hatasında tarayıcı atlanır, özgün hata yükselir (log: "tarayıcı yedeği atlandı"). Premium uyarısı ürün başına 30 dk'da bir (ProblemLog, tekrar sayısıyla).
- Dosyalar: providers/amazon/client.py (is_network_error), providers/hepsiburada/detail.py, tests/test_amazon_client.py, tests/test_parsers.py, ha-addon/CHANGELOG.md, config sürümü.

## Test ve doğrulama

| Kontrol | Komut/yöntem ve kapsam | Sonuç / kanıt dosyası |
|---|---|---|
| Yerel test | sh tools/check.sh (ruff + 594 test) | OK |
| Canlı davranış | aşağıda "Yayın ve kurulum" | bkz. |

## Yayın ve kurulum

- Kurulum sonucu aşağıda eklenir.

## Veri, yedek ve geri alma

- Kullanıcı verisi: dokunulmadı.
- Yedek: installer en yeni günlük yedeği doğrular.
- Geri alma: sh tools/prepare_code_rollback.sh ac888ba (4.2.6 kodu).

## Kalan durum

- Canlıda DNS kesintisi yeniden oluşmadıkça tarayıcı atlama yolu canlıda görülemez; birim testle doğrulandı.
