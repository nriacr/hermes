(() => {
  const state = { tab: 'watch', filter: 'all' };
  const modal = document.getElementById('ov-modal');
  const sheet = document.getElementById('ov-sheet');
  const region = document.getElementById('live-region');
  if (!modal || !sheet || !region) return;
  const apply = () => {
    region.querySelectorAll('[data-tab]').forEach((tab) => tab.setAttribute('aria-selected', String(tab.dataset.tab === state.tab)));
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
  document.addEventListener('click', (event) => {
    const target = event.target;
    if (target === modal || target.closest('[data-close]')) return close();
    if (target.closest('#ov-sheet')) return;
    const tab = target.closest('[data-tab]');
    if (tab) { state.tab = tab.dataset.tab; return apply(); }
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
    const moment = Math.max(points[0][2], Math.min(points[points.length - 1][2], points[0][2] + (points[points.length - 1][2] - points[0][2]) * ((x - points[0][0]) / ((points[points.length - 1][0] - points[0][0]) || 1))));
    cross.setAttribute('x1', x); cross.setAttribute('x2', x);
    cross.setAttribute('y1', chart.dataset.top); cross.setAttribute('y2', chart.dataset.bottom);
    dot.setAttribute('cx', x); dot.setAttribute('cy', hit[1]);
    cross.setAttribute('opacity', '1'); dot.setAttribute('opacity', '1');
    let bubble = tip;
    if (!bubble) { bubble = document.createElement('div'); bubble.className = 'ov-tip'; box.appendChild(bubble); }
    bubble.hidden = false;
    bubble.innerHTML = `${fmtPrice(hit[3])}<small>${fmtDate(moment)}</small>`;
    const boxRect = box.getBoundingClientRect();
    const left = Math.max(70, Math.min(boxRect.width - 70, event.clientX - boxRect.left));
    bubble.style.left = `${left}px`;
    bubble.style.top = `${hit[1] / scale + (rect.top - boxRect.top) - 12}px`;
  };
  sheet.addEventListener('pointermove', probe);
  sheet.addEventListener('pointerdown', probe);
  sheet.addEventListener('pointerleave', () => probe({ target: document.body }));
  document.addEventListener('hermes-live', apply);
  apply();
  // The entrance animation plays once; later in-place refreshes must not replay it.
  window.setTimeout(() => document.body.classList.remove('ov-first'), 2500);
})();