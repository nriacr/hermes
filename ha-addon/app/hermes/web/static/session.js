(() => {
  const script = document.getElementById('hermes-session-script');
  if (!script) return;
  let deadline = Date.now() + Number(script.dataset.remaining) * 1000;
  let lastTouch = 0;
  let busy = false;
  let locking = false;
  const lock = () => {
    if (locking) return;
    locking = true;
    document.dispatchEvent(new Event('hermes-session-locked'));
    // Remove sensitive form values before navigating, including on connection loss.
    document.querySelector('main')?.replaceChildren();
    window.location.replace(script.dataset.pinPath);
  };
  const activity = async () => {
    if (locking || busy || Date.now() - lastTouch < 30000) return;
    if (Date.now() >= deadline) { lock(); return; }
    busy = true;
    lastTouch = Date.now();
    try {
      const response = await fetch(script.dataset.touchPath, {
        method: 'POST', cache: 'no-store', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: new URLSearchParams({ csrf_token: script.dataset.csrf }),
      });
      if (response.status === 401 || response.status === 403) { lock(); return; }
      if (response.ok) deadline = Date.now() + (await response.json()).remaining * 1000;
    } catch (_error) { /* Offline activity cannot extend the server session. */ }
    finally { busy = false; }
  };
  ['pointerdown', 'keydown', 'touchstart'].forEach(name => {
    document.addEventListener(name, event => { if (event.isTrusted) activity(); }, { passive: true });
  });
  window.setInterval(async () => {
    if (Date.now() >= deadline) { lock(); return; }
    if (document.hidden || locking) return;
    try {
      const response = await fetch(script.dataset.statusPath, { cache: 'no-store', credentials: 'same-origin' });
      if (!response.ok) { lock(); return; }
      deadline = Date.now() + (await response.json()).remaining * 1000;
    } catch (_error) { /* The local deadline still clears the page when offline. */ }
  }, 15000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden && Date.now() >= deadline) lock(); });
  window.addEventListener('pageshow', event => { if (event.persisted) window.location.reload(); });
})();
