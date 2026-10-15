# 2026-10-15 — Claude — Yeniden başlama sonrası anasayfa

## Sorumlu ve yetki

- Agent: Claude, `hermes-work` main. İnsan talimatı: Nuri, 15 Ekim 2026: "ayarlarda bir değişiklik yaptıktan sonra kilit ekranı geliyor, onun yerine anasayfaya dönsün".
- Sürüm 4.2.6 (`bin/ha-version`).

## Amaç ve davranış

- Kök neden: PIN oturumları bellekte tutulur; kayıt/yeniden başlatma Hermes'i yeniden başlatınca oturum silinir. İlerleme sayfası bittiğinde `settings` sayfasına dönüyordu (yalnız düğmeyle "ana ekran"a dönülebiliyordu), oturum olmadığı için kilit ekranı açılıyordu.
- Sonra: kayıt ve yeniden başlat yönlendirmeleri her zaman `return_to_main=1` taşır; `render_restart_page` varsayılanı anasayfa (`return_to_main=0` ayarlar sayfasını seçer). `/restarting` ve `/settings/restarting` oturumsuz GET'e açıldı (yalnız ilerleme metni, hassas veri yok); `/settings`, `/statistics` ve tüm POST işlemleri PIN ister. Ingress ve public aynı yönlendiriciyi kullanır. Kullanılmayan `should_return_to_main` kaldırıldı.
- Dosyalar: web/server.py, web/settings.py, ha-addon/config.yaml, constants.py (sürüm), CHANGELOG.md, tests/test_web.py, tests/test_web_access.py.

## Test ve doğrulama

| Kontrol | Komut/yöntem | Sonuç |
|---|---|---|
| Yerel | `sh tools/check.sh` (591 test, ruff) | Geçti |
| Regresyon | ilerleme sayfası oturumsuz açılır, varsayılan dönüş anasayfa (ingress+public), kayıt/yeniden başlat yönlendirmesi, ayarlar/istatistik hâlâ kilitli | Geçti |
| Canlı | Kurulum sonrası eklenecek | — |

## Yayın, veri, geri alma

- Kurulum durumu aşağıda güncellenir. Veri etkisi yok. Geri alma: `sh tools/prepare_code_rollback.sh <4.2.5 commit c4c1153 öncesi kod commit'i>` (kod; veri geri yüklemesi yok).

## Kalan durum

- Kurulum Nuri'nin onayını bekliyor.
