# Yayın erişimi

GitHub kod yayını ve otomatik kontrol dosyası düzenlemesi farklı izinlerdir.
2026-10-09'da mevcut `codex` anahtarının `repo` iznine, sahibinin ayrıca onayladığı
`workflow` izni eklendi. Anahtar değiştirilmedi; macOS Anahtar Zinciri'nde kaldı.
Bu klasik anahtar tek depoyla sınırlı değildir. Depo silme, hesap, organizasyon
ve paket yönetimi gibi başka izinler eklenmedi.

## Her yayından önce

```sh
python3 tools/check_access.py --require-workflow
```

Kontrol yalnız okur: doğru GitHub hesabı, Hermes'e kod yayımlama, otomatik
kontrollerin etkinliği/okuma erişimi ve Home Assistant yönetim/yedek erişimi.
Anahtar veya ham yönetim yanıtı yazdırmaz. Ağ hatası izin varmış gibi kabul edilmez.
Sınırlı depo anahtarlarının izinleri OAuth başlığından kanıtlanamaz; böyle bir
anahtara geçiş ayrıca Workflows/Contents yazma yetkisi doğrulaması gerektirir.

Her yeni çalışma klasöründe şu ayar bir kez uygulanır:

```sh
git config --local core.hooksPath .githooks
```

Sonraki Git yayınlarında kontrol kendiliğinden çalışır. `.github/workflows`
dosyaları değişiyorsa eksik `workflow` izni, yayın sunucusuna gönderilmeden
yakalanır. Kurulum aracı da yönetim/yedek erişimini canlı işlemden önce kontrol eder.
Bu ön kontrol, GitHub'ın son yazma denetimi ve kurulum sonrası sağlık kontrolünün
yerine geçmez. Erişim reddedildiğinde başka kimliğe/taşıma yöntemine geçerek aşılmaz.

## Yetki katmanları

- Yerel Git anahtarı: mevcut `repo, workflow`. Yayımlama ve kontrol dosyası bakımı.
- GitHub Actions iş anahtarı: mevcut `contents: read`; test/derleme için yeterli.
  Kod yayımlama anahtarının izniyle karıştırılmaz ve gereksiz yere genişletilmez.
- Raspberry Pi bağlantısı: mevcut SSH yardımcısı; kurulum ve yedek yönetimi.
- Home Assistant REST/WS: mevcut ayrı bağlantı araçları. SSH eklentisinin
  Supervisor anahtarı, Home Assistant Core erişim anahtarı yerine kullanılmaz.
- Hermes: mevcut Supervisor manager ve Home Assistant API bildirim izinleri.
- Çalışma alanı ve otomatik onay: uygulama güvenlik politikasıdır; bu işlemde
  kapatılmaz veya genel sınırsız erişime çevrilmez.

Mevcut erişimler doğrulanabilir; ilerideki anahtar iptali, ağ kesintisi veya
hesap politikası değişikliklerini hiçbir izin ayarı sonsuza kadar önleyemez.
Kontrol bunları yayından önce görünür kılar.

Kaynaklar: [GitHub OAuth kapsamları](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps),
[sınırlı anahtar izinleri](https://docs.github.com/en/rest/authentication/permissions-required-for-fine-grained-personal-access-tokens).
