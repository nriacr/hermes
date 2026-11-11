(() => {
  const state = { tab: 'watch', filter: 'all' };
  const modal = document.getElementById('ov-modal');
  const sheet = document.getElementById('ov-sheet');
  const region = document.getElementById('live-region');
  if (!modal || !sheet || !region) return;
  const apply = () => {
    region.querySelectorAll('[data-tab]').forEach((tab) => tab.setAttribute('aria-selected', String(tab.dataset.tab === state.tab)));
    region.querySelectorAll('.ov-tabs').forEach((bar) => bar.style.setProperty('--i', String([...bar.querySelectorAll('[data-tab]')].findIndex((tab) => tab.dataset.tab === state.tab))));
    region.querySelectorAll('[data-pane]').forEach((pane) => { pane.hidden = pane.dataset.pane !== state.tab; });
    region.querySelectorAll('[data-filter]').forEach((chip) => chip.setAttribute('aria-pressed', String(chip.dataset.filter === state.filter)));
    region.querySelectorAll('.ov-tile').forEach((tile) => { tile.hidden = state.filter !== 'all' && tile.dataset.site !== state.filter; });
    region.querySelectorAll('.ov-group').forEach((group) => { group.hidden = !group.querySelector('.ov-tile:not([hidden])'); });
  };
  const close = () => { modal.hidden = true; sheet.innerHTML = ''; document.documentElement.style.overflow = ''; };
  const open = (card) => {
    const detail = card.querySelector('.ov-detail');
    if (!detail) return;
    sheet.style.setProperty('--c', getComputedStyle(card).getPropertyValue('--c'));
    sheet.innerHTML = detail.innerHTML;
    modal.hidden = false;
    document.documentElement.style.overflow = 'hidden';
  };
  const tick = () => {
    const now = Date.now() / 1000;
    region.querySelectorAll('[data-ago]').forEach((el) => {
      const s = Math.max(0, Math.floor(now - Number(el.dataset.ago)));
      const m = Math.floor(s / 60);
      el.textContent = s < 60 ? `${s} sn önce` : m < 60 ? `${m} dk ${s % 60} sn önce` : m < 1440 ? `${Math.floor(m / 60)} sa ${m % 60} dk önce` : `${Math.floor(m / 1440)} gün önce`;
    });
  };
  window.setInterval(tick, 1000);
  document.addEventListener('click', (event) => {
    const target = event.target;
    if (target === modal || target.closest('[data-close]')) return close();
    const range = target.closest('#ov-sheet [data-range]');
    if (range) {
      sheet.querySelectorAll('[data-range]').forEach((button) => button.setAttribute('aria-pressed', String(button === range)));
      sheet.querySelectorAll('[data-range-pane]').forEach((pane) => { pane.hidden = pane.dataset.rangePane !== range.dataset.range; });
      return;
    }
    if (target.closest('#ov-sheet')) return;
    const tab = target.closest('[data-tab]');
    if (tab) {
      // While the row is pinned, a new tab starts at its own top instead of mid-list.
      const bar = tab.closest('.ov-tabs');
      const top = bar ? parseFloat(getComputedStyle(bar).top) || 0 : 0;
      const pinned = bar && bar.getBoundingClientRect().top <= top + 1 && window.scrollY > 0;
      state.tab = tab.dataset.tab;
      apply();
      const panes = bar && bar.nextElementSibling;
      if (pinned && panes) window.scrollTo({ top: Math.max(0, window.scrollY + panes.getBoundingClientRect().top - bar.offsetHeight - parseFloat(getComputedStyle(bar).marginBottom) - top) });
      return;
    }
    const chip = target.closest('[data-filter]');
    if (chip) { state.filter = chip.dataset.filter; return apply(); }
    const card = target.closest('[data-open]');
    if (card && !target.closest('a')) open(card);
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !modal.hidden) close();
    if ((event.key === 'Enter' || event.key === ' ') && event.target.matches && event.target.matches('[data-open]')) { event.preventDefault(); open(event.target); }
  });
  const fmtPrice = (value) => `${Math.round(value).toLocaleString('tr-TR')} TL`;
  const fmtDate = (seconds) => new Date(seconds * 1000).toLocaleString('tr-TR', { day: 'numeric', month: 'long', year: 'numeric', hour: '2-digit', minute: '2-digit' });
  // Hovering (or dragging a finger over) the chart shows the price that held at that moment.
  const probe = (event) => {
    const chart = event.target.closest && event.target.closest('.ov-chart');
    const box = sheet.querySelector('.ov-chartbox');
    const tip = box && box.querySelector('.ov-tip');
    if (!chart || !box) { if (tip) tip.hidden = true; sheet.querySelectorAll('.ov-cross,.ov-cross-dot').forEach((item) => item.setAttribute('opacity', '0')); return; }
    const points = JSON.parse(chart.dataset.points || '[]');
    if (!points.length) return;
    const rect = chart.getBoundingClientRect();
    const scale = chart.viewBox.baseVal.width / rect.width;
    const x = (event.clientX - rect.left) * scale;
    let hit = points[0];
    for (const point of points) { if (point[0] <= x) hit = point; }
    const cross = chart.querySelector('.ov-cross');
    const dot = chart.querySelector('.ov-cross-dot');
    const axis = JSON.parse(chart.dataset.span || 'null') || [points[0][0], points[0][2], points[points.length - 1][0], points[points.length - 1][2]];
    const moment = Math.max(axis[1], Math.min(axis[3], axis[1] + (axis[3] - axis[1]) * ((x - axis[0]) / ((axis[2] - axis[0]) || 1))));
    cross.setAttribute('x1', x); cross.setAttribute('x2', x);
    cross.setAttribute('y1', chart.dataset.top); cross.setAttribute('y2', chart.dataset.bottom);
    dot.setAttribute('cx', x); dot.setAttribute('cy', hit[1]);
    cross.setAttribute('opacity', '1'); dot.setAttribute('opacity', '1');
    let bubble = tip;
    if (!bubble) { bubble = document.createElement('div'); bubble.className = 'ov-tip'; box.appendChild(bubble); }
    bubble.hidden = false;
    const otherPoints = JSON.parse(chart.dataset.pointsB || '[]');
    let extra = '';
    if (otherPoints.length) {
      let found = null;
      for (const point of otherPoints) { if (point[0] <= x) found = point; }
      const names = [...sheet.querySelectorAll('.ov-legend span')].map((item) => item.textContent.trim());
      extra = `<small>${names[0] || ''}: ${fmtPrice(hit[3])}${found ? ` · ${names[1] || ''}: ${fmtPrice(found[3])}` : ''}</small>`;
    }
    bubble.innerHTML = otherPoints.length ? `${extra}<small>${fmtDate(moment)}</small>` : `${fmtPrice(hit[3])}<small>${fmtDate(moment)}</small>`;
    const boxRect = box.getBoundingClientRect();
    const left = Math.max(70, Math.min(boxRect.width - 70, event.clientX - boxRect.left));
    bubble.style.left = `${left}px`;
    bubble.style.top = `${hit[1] / scale + (rect.top - boxRect.top) - 12}px`;
  };
  sheet.addEventListener('pointermove', probe);
  sheet.addEventListener('pointerdown', probe);
  sheet.addEventListener('pointerleave', () => probe({ target: document.body }));
  // The tab row turns light while it is pinned at the top of the screen.
  const pin = () => region.querySelectorAll('.ov-tabs').forEach((bar) => {
    bar.classList.toggle('is-pinned', window.scrollY > 0 && bar.getBoundingClientRect().top <= (parseFloat(getComputedStyle(bar).top) || 0) + 0.5);
  });
  let pinFrame = 0;
  window.addEventListener('scroll', () => { if (!pinFrame) pinFrame = requestAnimationFrame(() => { pinFrame = 0; pin(); }); }, { passive: true });
  window.addEventListener('resize', pin);
  document.addEventListener('hermes-live', pin);
  document.addEventListener('hermes-live', apply);
  apply();
  pin();
  // The entrance animation plays once; later in-place refreshes must not replay it.
  window.setTimeout(() => document.body.classList.remove('ov-first'), 2500);
})();