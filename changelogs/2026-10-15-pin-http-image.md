# 2026-10-15 — Claude — PIN kilidi ayrımı, açık HTTP uyarısı, sabit Docker imajı

## Sorumlu ve yetki

- Agent ve konu/çalışma kopyası: Claude, `hermes-work` (main).
- İnsan talimatı ve kapsamı: Nuri 2026-10-15 17:03Z "olur" dedi; yalnız bu üç madde (kod, test, kurulum). Container root kalır (Nuri kararı).
- Tarih, commitler, sürüm: 2026-10-15; başlangıç `0b9c9c2`; sürüm 4.2.3 → 4.2.4 (`bin/ha-version` ile ayrıldı). Sonuç commiti kurulum kaydıyla birlikte aşağıda güncellenir.

## Amaç ve davranış

- Sorun: tek küresel PIN deneme sayacı vardı; public adresten yapılan yanlış denemeler Home Assistant içinden (ingress) girişi de 60 sn kilitliyordu. Public HTTP açıkken hiçbir uyarı yoktu. Docker temel imajı `python:3.12-slim` hareketli etiketti.
- Önceki: MAX_FAILURES=5, LOCK_SECONDS=60, tek `failures`/`locked_until`.
- Sonraki:
  1. Sayaçlar yüzeye göre ayrı (`ingress`, `public`; IP kullanılmıyor). Ingress: her 5 yanlışta sabit 60 sn. Public: her yeni kilitte 60 sn → 5 dk → 30 dk (tavan 30 dk); o yüzeyde doğru PIN sayacı ve kademeyi sıfırlar. Public kilidi ingress'i kilitlemez. PIN ekranı bekleme süresini doğru yazar ("60 saniye", "5 dakika", "30 dakika").
  2. Public yüzeyde ve HTTPS değilken (`X-Forwarded-Proto` ≠ https) ayarlar ekranında sarı uyarı: "Bu adres şifresiz (HTTP). Yalnız güvendiğin ağda ya da HTTPS tünel arkasında kullan." Ingress'te ve HTTPS'te gösterilmez. README'ye eklendi.
  3. Dockerfile: `python:3.12.15-slim@sha256:a6e34c59…` (aarch64/armv7/amd64 destekleyen çok mimarili indeks; Docker Hub'da doğrulandı). Container root kalır.
- Dosya biçimi: `/data/web_access.json` `version: 1` aynı; yeni `locks` alanı eklendi. Eski dosya (yalnız `failures`/`locked_until`) yüklenir; eski sayaç public kovasına taşınır, PBKDF2 doğrulayıcı değişmez. Kayıtta eski anahtarlar `[]`/`0` yazılır, böylece 4.2.3'e dönüş dosyayı okuyabilir. Bozuk `locks` kapalı başarısız olur (fail-closed); kayıt hatası yine erişim vermez.
- Değişen dosyalar: `ha-addon/app/hermes/web/access.py` (kovalar, kademe, taşıma), `web/server.py` (kapsam geçişi, uyarı bayrağı), `web/pin.py` (süre metni), `web/settings.py` + `web/pages.py` + `static/base.css` + `static/settings.css` (uyarı), `ha-addon/Dockerfile`, `ha-addon/config.yaml` + `constants.py` (4.2.4), `ha-addon/CHANGELOG.md`, `README.md`, `tests/test_web_access.py`.
- Bağımlılıklar/başka agent: ChatGPT da düzenleyebilir; başlangıçta `git pull` güncel, ek değişiklik yoktu.

## Test ve doğrulama

| Kontrol | Komut/yöntem ve kapsam | Sonuç / kanıt dosyası |
|---|---|---|
| Yerel test | `sh tools/check.sh` (pip check, compileall, ruff, 575 unittest; yeni: yüzey ayrımı, kademe/tavan/sıfırlama, eski dosya taşıma, bozuk durum, router süre metni, HTTP uyarısı) | Geçti |
| Canlı sürüm/sağlık | `install_rpi.sh` + `tools/read_runtime.py` | 4.2.4 started, healthy, 14 aktif kart, 637 fiyat noktası (kurulum öncesi geçmiş korundu), open_incidents 1 (önceden var), tamamlanan iş okuması sürüyor |
| Public panel çerçeve koruması | Pi'den `curl` ile public ana sayfa başlıkları | HTTP 200, `X-Frame-Options: DENY`, CSP `frame-ancestors 'none'` |
| Public ayarlar kilit ekranı | Pi'den public `/settings` | "Ayarlar kilitli" PIN ekranı geliyor; HTTP uyarısı PIN girilmeden görünmez (ayarlar açılınca görünür) |
| PIN kilit davranışı | Yalnız birim testleri | Canlıda yanlış PIN denenmedi: canlı güvenlik durumunu (kademe sayacı) değiştirirdi; ayrı kovalar ve kademe testlerle doğrulandı |
| Ek yük imajı | `ha apps update` Pi'de yeni sabit imajı derledi | Başarılı, uygulama started |

## Yayın ve kurulum

- Yayın: commit `4538c75`, sürüm 4.2.4 (GitHub main).
- Kuruldu: 2026-10-15, `sh tools/install_rpi.sh` (Nuri'nin yazılı onayıyla; HTTP uyarısı dahil üç madde de kalsın dedi). Sonuç: Hermes 4.2.4 kuruldu, sağlıklı.

## Veri, yedek ve geri alma

- Kullanıcı verisi: fiyat geçmişi, takipler, bildirim hafızası, SQLite değişmez; yalnız `web_access.json` içine `locks` eklenir (PIN korunur).
- Yedek: install_rpi.sh'in doğruladığı günlük tam yedek `b160df65` (yeni yedek alınmadı).
- Geri alma: `sh tools/prepare_code_rollback.sh 0b9c9c2` (yalnız kod); kurulum aracının verdiği yol: `sh tools/rollback_rpi.sh b160df65` (yedekten geri yükleme, veri etkiler, ayrı insan yetkisi ister). Veri geri yükleme ayrı insan yetkisi ister.

## Kalan durum

- Public kilit sayacı yüzey başınadır, kişi/IP başına değil: bir saldırgan public girişi kilitleyebilir (en çok 30 dk), ingress etkilenmez.
