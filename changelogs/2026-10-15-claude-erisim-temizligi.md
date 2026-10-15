# 2026-10-15 — Claude — Erişim ve temizlik işleri

## Sorumlu ve yetki

- Agent: Claude (`hermes-work` main). Nuri 15 Ekim 2026: "ajan başına SSH anahtarı, tek depoya GitHub anahtarı, .claude Edit izni, iCloud/yedek kontrolü, eski worktree silme — bunları da yapalım".
- Sürümsüz araç/kayıt değişikliği (4.2.5 canlı, değişmedi).

## Yapılanlar

- **Eski worktree'ler:** 9 temiz (kaydedilmemiş değişiklik yok) worktree `git worktree remove` ile kaldırıldı: hermes-{contract,deleted-watch,diagnostics,frequency,pin,purchase-design,purchase-trial,startup,statistics}-work. Dallar silinmedi. Önce 10 `codex/*` dalının tamamı `backups/worktree-yedek-20261015.bundle` dosyasına yedeklendi (`git bundle verify` geçti; gitignore'daki backups/ içinde, GitHub'a gitmedi). Birleşmemiş commit sayıları: purchase-design 3, deleted-watch(codex) 4, diagnostics 2, frequency 2, pin 2, diğerleri 1. Geri alma: `git worktree add <klasör> <dal>`; bozulursa bundle'dan `git fetch <bundle> 'refs/heads/*:refs/heads/*'`.
- **Korunanlar:** hermes-work, hermes-stock-work (kayıtlı worktree, kalan), hermes-pi-purchase-test-work, hermes-pin-release-work, hermes-statistics-operation (bağımsız klon/klasör, kayıtlı worktree değil; silinmedi), `.release-state/` (kuyruk arşivi, kilit ve rezervasyon dosyaları), backups/ içeriği. /private/tmp altındaki 2 ölü worktree kaydı `git worktree prune` ile temizlendi.
- **GitHub ince ayarlı anahtar desteği:** `tools/check_access.py` `github_pat_` anahtarını tanır; OAuth scope başlığına bağlı olmadan şunları doğrular: hesap nriacr, anahtar yalnız `nriacr/hermes` deposuna erişiyor (`/user/repos`), Contents yazma (push izni), son kullanma tarihi var. Workflows yazma izni API ile okunamaz; ilk workflow'lu push'ta GitHub sınar (çıktıda belirtilir). Klasik anahtar yolu aynen çalışır. 4 yeni test (`tests/test_release_access.py`).
- **Ajan başına SSH anahtarı (kod tarafı):** HA deposunda `bin/ha-ssh` `HA_AGENT=<ad>` verilirse ve `~/.ssh/homeassistant_<ad>` varsa onu kullanır; yoksa mevcut anahtar. Denendi: dosya yokken eski anahtarla bağlanıyor. docs/YAYIN_AKISI.md'ye yazıldı.

## Yapılmayanlar (güvenlik ayarı; Nuri'nin kendi adımı)

Agent güvenlik ayarı, kimlik bilgisi veya yetki değiştirmediği için şunlar yapılmadı:
1. SSH anahtarı üretme ve Pi'de `authorized_keys`'e ekleme.
2. GitHub ince ayarlı token oluşturma ve anahtarlığa koyma; eski klasik anahtarı iptal.
3. `.claude/settings.json` Edit izinlerini daraltma.
4. backups/ klasörünü iCloud dışına taşıma (karar Nuri'nin).

## iCloud / yedek bulgusu (yalnız okuma)

- Finder "Masaüstü ve Belgeler" iCloud eşitlemesi açık (`FXICloudDriveDesktop=1`); `~/Documents`, iCloud Drive içindeki `Documents` ile aynı klasör. Yani proje klasörü ve **backups/ iCloud'a eşitleniyor**.
- backups/ içinde `homeassistant-28523a61.tar` (692 MB), `homeassistant-f6d99221.tar` (703 MB) ve `baseline-20261010/` (715 MB: homeassistant-ea4d96c1.tar, downloads/workspace/ha/hermes yedekleri). HA tar'ları iç arşiv adlarını (homeassistant.tar.gz, core_mosquitto…) gösteriyor; şifreli olup olmadığı içerik okunmadan kesin bilinemez. HA yedekleri anahtar/parola ve oturum verisi içerebilir.
- Seçenekler: (a) backups/ klasörünü iCloud dışına taşı (örn. ~/HA-yedek), (b) klasör adını `backups.nosync` yapıp sembolik bağ bırak, (c) olduğu gibi bırak. Karar Nuri'nin.

## Test

`sh tools/check.sh` (ruff + tüm testler) geçti; `zsh -n bin/ha-ssh` geçti.
