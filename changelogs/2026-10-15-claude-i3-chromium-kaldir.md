# 2026-10-15 — Claude — I3 Chromium'u imajdan kaldırma (4.5.0)

## Sorumlu ve yetki

- Agent: Claude, hermes-work (dal i3-chromium-kaldir). İnsan talimatı: Nuri optimizasyon listesinde I3'ü onayladı ve "Kaldır" seçeneğini seçti.

## Amaç ve davranış

- Önce: Dockerfile chromium + chromium-driver + fonts-liberation kuruyordu; requirements'ta selenium vardı; AmazonClient.fetch, curl okuması CAPTCHA/429/503 dışında başarısız olunca (ve ağ hatası değilse) aynı adresi Chromium ile bir kez okurdu. I1 ölçümü: 7.204 istekten 7.159'u curl; 45 tarayıcı okuması yalnız DNS kesintisinde oldu (faydasız, ~25 sn).
- Sonra: tarayıcı yolu yok. fetch tek curl/requests okuması yapar; başarısız olursa özgün hata yükselir (çağıran çevrim hatayı mevcut akışla işler, sonraki çevrimde yeniden dener). is_network_error ve providers/amazon/browser.py silindi.
- Dosyalar: Dockerfile, requirements.txt, providers/amazon/client.py, providers/amazon/browser.py (silindi), tests/test_amazon_client.py, docs/ARCHITECTURE.md, ha-addon/README.md, config.yaml (sürüm + yorum), run.sh (yorum), CHANGELOG.

## Test ve doğrulama

| Kontrol | Komut | Sonuç |
|---|---|---|
| Yerel test | sh tools/check.sh | ruff + 603 test OK (tarayıcıya özgü testler çıkarıldı, "başarısız okuma tek istek, ikinci taşıyıcı yok" testi eklendi) |
| Canlı | tools/install_rpi.sh | aşağıda "Canlı kanıt" bölümünde |

## Veri, yedek ve geri alma

- Kullanıcı verisi etkilenmez. Canlı ölçüm (kurulum öncesi, 4.4.0): 13 takip, 364 state kaydı, 644 fiyat noktası, 16.671 okuma; bellek ~477 MB, disk kullanımı 30,5 GB.
- Geri alma: sh tools/prepare_code_rollback.sh 76710c0 (4.4.0).

## Kalan sınırlamalar

- Amazon curl okuması geçici hatada tarayıcı kurtarması artık yok; I1 verisine göre bu yol fiilen hiç işe yaramadı.
