# 2026-10-14 — Codex — Koordinatörsüz Hermes yayın araçları

## İnsan yetkisi ve kapsam

Nuri yayın koordinatörünü kaldırmayı, ChatGPT kilidi olmadan Claude ile geliştirmeyi
ve her agentın ayrıntılı changelog yazmasını açıkça istedi. Bu kayıt araç/çalışma
sözleşmesi değişikliğidir; runtime kodu veya uygulama sürümü değiştirilmedi.
Başlangıç: origin/main 926ad1f. Yerel kanonik kopya önceden e8f59d1 idi; önce zaten
yayımlanmış kayda fast-forward edildi. Başka agentın .claude ve .cursor dosyaları
korundu.

## Önce / sonra ve dosyalar

- tools/install_rpi.sh ve tools/rollback_rpi.sh: homeassistant.coordinator Git ayarı ve ChatGPT sohbet/iş kimliği guard çağrısı kaldırıldı. Git common-dir üzerinden kanonik HA araçları bulunur; bin/ha-exclusive süreç kilidi otomatik alınır. İşlemin test/erişim/yedek/config/beklenen sürüm/sağlık kontrolleri korunur.
- AGENTS.md: her agent mevcut insan yetkisiyle kendi yayın, kurulum ve özel davranış doğrulamasını yapar; kuyruk ve koordinatör onayı beklemez. İşlemler aynı kanonik kilitte yapılır, runtime sürümü ha-version ile ayrılır. Veri geri yükleme için ayrı açık insan yetkisi devam eder.
- changelogs/SABLON.md: her agentın amacı, yetkisi, önce/sonra davranışı, dosyaları, testleri, yayın/kurulum ve canlı kanıtı, veri/yedek/geri alma ve sınırlamaları kaydetmesi için şablon.
- Yerel Git ayarı homeassistant.coordinator kaldırıldı; core.hooksPath=.githooks. Mevcut repository pre-push kancası gerçek GitHub/workflow erişim kontrolünü korur; agent kimliğine bakmaz.

## Test ve canlı doğrulama

- sh tools/check.sh: bağımlılık/Ruff denetimi ve 558 test geçti. İlk sandbox denemesinde beş yerel HTTP testinin port açma izni yoktu; aynı kontrol izin verilen ortamda geçti. Kanıt: /private/tmp/ha-retirement-hermes-check.log.
- sh -n tools/install_rpi.sh tools/rollback_rpi.sh .githooks/pre-push: geçti.
- HA projesindeki 12 doğrudan yayın aracı testi geçti. Gerçek installer'ın sahte önkontrol girişi, canlı komut göndermeden ChatGPT guard engelinin kaldırıldığını kanıtladı. Alt Python süreçlerinin aynı işlem kilidini kullanması ve iki agentın birbirine çakışmaması da doğrulandı.
- bin/ha-project status yalnız okuma ile API running ve HA 2026.10.1 doğrulandı. Bu araç bakımında uygulama install/restart/restore veya sipariş/TV komutu gönderilmedi.

## Yayın, veri, geri alma

Bu dosya araç/doc commitinin parçasıdır; GitHub yayın sonucu kanonik HA
reports/coordinator-retired-20261014.json dosyasında kayıtlıdır. Runtime sürümü
artırılmadı; ha-addon/CHANGELOG.md uygulama sürüm geçmişi aynı kaldı. Fiyat,
bildirim, takip, PIN, kullanıcı oturumu ve SQLite verilerine dokunulmadı. Ek tam
yedek alınmadı. Yerel araç kaynağı ve eski Git ayarları kanonik HA
backups/coordinator-retired-20261014/ arşivindedir. Araç commitini geri almak
kod geri alımıdır; canlı veri restore ayrı açık insan yetkisi gerektirir.

Eski konu kopyaları bu araç değişikliğini birleştirmeli veya güncel kanonik
araçları kullanmalıdır. Diğer makineler Mac'in yerel işlem kilidini paylaşmaz.
Başarılı iş için kullanıcıya veya başka sohbete rutin rapor mesajı gönderilmez.

## Güncel yayınla birleşim

İlk push güncel main ilerlediği için normal fast-forward korumasıyla reddedildi;
force push yapılmadı. origin/main yeniden okundu ve özel changelog commitimiz
6c4179f (Claude'un ayrı 4.2.2 işi) üzerine birleştirildi. Bu güncel yayında aynı
AGENTS, installer/rollback ve şablon değişiklikleri zaten bulunuyordu. Son
origin/main→bu commit farkı yalnız bu ayrıntılı Codex changelogudur; Claude'un
runtime/veri davranışı değişiklikleri üzerine yazılmadı. 558 test bu bakımın
başlangıçtaki 4.2.1 kaynağında geçti; 4.2.2 uygulama davranışının yayın/test kaydı
Claude'un ayrı changelogundadır. Bu belge yayını yeni runtime kurulumu yapmaz.
