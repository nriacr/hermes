(() => {
  const region = document.getElementById('live-region');
  if (!region || !window.fetch) return;
  const url = region.dataset.liveUrl;
  let busy = false;
  let version = '';
  const refresh = async () => {
    if (busy || document.hidden) return;
    busy = true;
    try {
      const query = `ts=${Date.now()}&v=${encodeURIComponent(version)}`;
      const response = await fetch(`${url}${url.includes('?') ? '&' : '?'}${query}`, { cache: 'no-store' });
      if (!response.ok) return;
      const data = await response.json();
      if (data.same || typeof data.html !== 'string') return;
      const open = new Set([...region.querySelectorAll('details[data-key][open]')].map((item) => item.dataset.key));
      const scroll = window.scrollY;
      const phases = region.getAnimations({ subtree: true })
        .filter((item) => item.effect.getComputedTiming().iterations === Infinity)
        .map((item) => [item.animationName, item.currentTime]);
      region.innerHTML = data.html;
      // Sonsuz animasyonlar (fırsat glow'u, nabız) yenilemede baştan başlamasın.
      region.getAnimations({ subtree: true })
        .filter((item) => item.effect.getComputedTiming().iterations === Infinity)
        .forEach((item, index) => {
          const old = phases[index];
          if (old && old[0] === item.animationName) item.currentTime = old[1];
        });
      document.dispatchEvent(new Event('hermes-live'));
      region.querySelectorAll('details[data-key]').forEach((item) => { if (open.has(item.dataset.key)) item.open = true; });
      window.scrollTo(0, scroll);
      version = data.v || '';
    } catch (_error) {
      // Hermes may be restarting; the next attempt retries.
    } finally {
      busy = false;
    }
  };
  window.setInterval(refresh, (Number(region.dataset.liveInterval) || 15) * 1000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
})();