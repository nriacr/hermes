(() => {
  const script = document.getElementById('hermes-restart-script');
  const settingsPath = script?.dataset.settingsPath || '../settings';
  const returnPath = script?.dataset.returnPath || settingsPath;
  const healthPath = script?.dataset.healthPath || '../health';
  const statusBox = document.getElementById('restart-status');
  let attempts = 0;

  const waitForHermes = async () => {
    attempts += 1;
    statusBox.textContent = `Hermes kontrol ediliyor... Deneme ${attempts}`;
    try {
      const response = await fetch(`${healthPath}?ts=${Date.now()}`, { cache: 'no-store' });
      if (response.ok) {
        statusBox.textContent = 'Hermes hazır. Sayfa yenileniyor...';
        const separator = returnPath.includes('?') ? '&' : '?';
        window.location.href = `${returnPath}${separator}saved=ok&msg=${encodeURIComponent('Hermes hazır. Ayarlar güncellendi.')}`;
        return;
      }
    } catch (_error) {
      statusBox.textContent = 'Hermes yeniden başlıyor, bağlantı bekleniyor...';
    }
    window.setTimeout(waitForHermes, 2000);
  };

  window.setTimeout(waitForHermes, 6000);
})();