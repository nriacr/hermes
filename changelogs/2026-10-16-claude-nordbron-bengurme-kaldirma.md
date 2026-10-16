# 2026-10-16 — Claude — Nordbron ve Ben Gurme sitelerinin kaldırılması (4.16.0)

## Sorumlu ve yetki

- Agent: Claude. Çalışma kopyası: hermes-claude-site-removal (dal claude-site-removal, origin/main 7ba824c üzerine).
- İnsan talimatı: Nuri "nordbron ve bengurme sitelerini tanınan sitelerden kaldır, arama kartını kullanmak istemiyorum artık" dedi (16 Ekim 2026); kod, test ve kurulum için onay.
- Sürüm: 4.14.0 → 4.16.0 (bin/ha-version --change minor ile ayrıldı).

## Amaç ve davranış

- Önce: Nordbron ve Ben Gurme linkleri otomatik algılanıp okunuyordu (4.10.0 U8 site listesi).
- Sonra: iki site desteklenen listeden çıktı; bu sitelerin linkleri `Desteklenmeyen site alan adı` ile reddedilir. Aynı kartın diğer linkleri ve diğer kartlar etkilenmez (mevcut davranış: desteklenmeyen link atlanır, günlüğe yazılır).
- "Arama kartı": Hermes'te ayrı bir arama kartı özelliği yok; arama sayfası takibi Amazon ve Hepsiburada'nın çekirdek özelliği ve Nordbron/Ben Gurme arama sayfası okumuyordu. Bu iki site kalktığı için onların arama linkleri de artık kabul edilmiyor; Amazon/Hepsiburada arama takibine dokunulmadı (Nuri'ye bu yorum bildirildi, aksini isterse ayrı iş).
- Canlıda (4.14.0, 13 kart) bu sitelere ait kart yoktu; ana sayfada bu sitelere ait satır görünmüyordu.
- Hepsiburada başlık temizliğindeki "Nordbron Stark" özel adı Hepsiburada ürünleri içindir ve değişmedi.
- Silinen dosyalar: providers/nordbron.py, providers/bengurme.py, tests/fixtures/ (yalnız bu iki siteye aitti), tests/test_fixtures.py ve tools/capture_fixture.py (fixture çerçevesi yalnız bu iki siteyi kullanıyordu).
- Değişen dosyalar: constants.py (site sabitleri, etiketler, host işaretleri, sürüm), providers/registry.py, web/dashboard.py ve web/static/overview.css (site renk sınıfı), README.md, ha-addon/README.md, docs/ARCHITECTURE.md, ha-addon/CHANGELOG.md, config.yaml.
- Testler: Nordbron'u genel örnek site olarak kullanan testler Trendyol/Network'e taşındı; yalnız bu sitelere ait testler silindi; yeni testler: kaldırılan site linkleri reddedilir ve diğer linkleri engellemez (test_parsers), kalan eski satırlar (durum + fiyat geçmişi) saklanır ve çevrimi bozmaz (test_monitor).

## Test ve doğrulama

| Kontrol | Komut/yöntem ve kapsam | Sonuç / kanıt |
|---|---|---|
| Yerel test | sh tools/check.sh (pip check, compileall, ruff, 605 test) | OK |
| Canlı davranış | aşağıda | aşağıda |

## Veri, yedek ve geri alma

- Kullanıcı verisi silinmedi: hermes.db, state.json, fiyat geçmişi ve ayarlar dokunulmadı. Kalan eski satırlar (varsa) site etiketi yedeği (`Nordbron`/`Bengurme` başlık biçimi) ile gösterilir, okunmaz.
- Kartı olan karışık karta (Nordbron + başka site) dikkat: kimliksiz eski kartlarda kart kimliği desteklenen linklerden türetildiği için kimlik değişebilir; canlıda böyle kart yoktu.
- Yedek ve geri alma: aşağıda.

## Canlı kanıt (17 Ekim 2026)

- Not: bu kayıttaki "4.15.0" geçişleri sürüm çakışması yüzünden 4.16.0'a çevrildi (4.15.1 başka iş parçacığınca yayımlandı); yayın commiti 8ad03ab "Release 4.16.0".
- Kurulum: HA_AGENT=claude, bin/ha-exclusive altında tools/install_rpi.sh; check.sh geçti; kullanılan tam yedek 42c055d8; çıktı "Hermes 4.16.0 kuruldu", sağlık healthy:true, 13 aktif kart, price_points 682 (artış normal tarama), veritabanı korunmuş.
- Yedek (düzeltme): kurulumda kullanılan 42c055d8, 16 Ekim 2026 18:50 UTC'de alınmış "Home Assistant manuel tam yedek" (tam, 466,79 MB); bu iş için ayrı alınmadı, kurulumdan (~21:20 UTC) önceki en yeni tam yedekti. Ayrıca b5d1ccbd (15 Ekim 22:06 UTC, tam) mevcut. Bu iş için ek tam yedek alınmadı (politika: yalnız Nuri'nin açık isteğiyle).
- Geri alma (kod): `sh tools/prepare_code_rollback.sh 47d8687` (4.15.1 koduna döner, 4.16.0 öncesi canlı durum). Daha eskiye (4.14.0) dönmek için 7ba824c. rollback_rpi.sh (veri geri yükleme) yalnız Nuri'nin açık onayıyla.
- Sınırlama: Pi'deki hermes.db'de bu sitelerin eski satır sayısı uzaktan okunamadı; satırlar silinmedi.
