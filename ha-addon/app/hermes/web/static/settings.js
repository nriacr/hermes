(() => {
  const normalize = (value) => String(value || '').toLocaleLowerCase('tr-TR');
  const watchSearch = document.getElementById('watch-search');
  const settingsForm = document.getElementById('settings-form');
  let dirty = false;
  document.addEventListener('hermes-session-locked', () => { dirty = false; });

  const refreshWatchList = () => {
    const searchText = normalize(watchSearch?.value.trim());
    document.querySelectorAll('[data-site-group]').forEach((group) => {
      let shown = 0;
      group.querySelectorAll('[data-watch-card]').forEach((card) => {
        card.hidden = Boolean(searchText) && !normalize(card.dataset.watchSearch).includes(searchText);
        if (!card.hidden) shown += 1;
      });
      group.hidden = shown === 0;
      if (searchText) group.open = shown > 0;
    });
  };
  watchSearch?.addEventListener('input', refreshWatchList);

  const newWatch = document.querySelector('[data-new-watch]');
  const element = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const writeVariantFilter = () => {
    const chosen = [...newWatch.querySelectorAll('[data-variant-dim]')].map((group) => {
      const values = [...group.querySelectorAll('input:checked')].map((box) => box.value);
      return values.length ? `${group.dataset.variantDim}=${values.join(', ')}` : '';
    }).filter(Boolean);
    newWatch.querySelector('[data-variant-filter]').value = chosen.join('; ');
  };
  const showProduct = (data) => {
    const body = newWatch.querySelector('[data-new-body]');
    newWatch.querySelector('[data-product-title]').textContent = data.title || '';
    const variants = data.variants || [];
    const priced = variants.filter((item) => item.price);
    newWatch.querySelector('[data-product-sub]').textContent = variants.length > 1
      ? `${variants.length} ${'Mağaza' in (data.dimensions || {}) ? 'mağaza' : 'varyant'}` : (priced[0]?.price || '');
    const table = newWatch.querySelector('[data-variant-table]');
    table.replaceChildren();
    if (variants.length > 1) {
      const grid = element('table');
      const head = grid.createTHead().insertRow();
      [['Varyant', ''], ['Fiyat', 'num'], ['Durum', '']].forEach(([text, cls]) => { const th = element('th', cls, text); head.appendChild(th); });
      const rows = grid.createTBody();
      variants.forEach((item) => {
        const row = rows.insertRow();
        row.appendChild(element('td', '', item.label));
        row.appendChild(element('td', 'num', item.price || '–'));
        row.appendChild(element('td', item.in_stock ? 'in-stock' : 'no-stock', item.in_stock ? 'Stokta' : 'Tükendi'));
      });
      table.appendChild(grid);
    }
    const options = new Set(data.options || []);
    const dimensions = data.dimensions || {};
    newWatch.querySelectorAll('[data-site-option]').forEach((node) => {
      node.hidden = !options.has(node.dataset.siteOption) || ('searchOnly' in node.dataset && !data.search);
    });
    newWatch.querySelector('[data-seller-label]').textContent = data.seller_label || '';
    const sizeSelect = newWatch.querySelector('[data-size-select]');
    sizeSelect.replaceChildren(new Option('Hepsi', ''));
    (dimensions.Beden || []).forEach((size) => sizeSelect.appendChild(new Option(size, size)));
    const dims = newWatch.querySelector('[data-variant-dims]');
    dims.replaceChildren();
    if (options.has('variant_filter')) {
      Object.entries(dimensions).forEach(([name, values]) => {
        const group = element('div', 'variant-dim');
        group.dataset.variantDim = name;
        group.appendChild(element('span', '', name));
        const chips = element('div', 'variant-chips');
        values.forEach((value) => {
          const chip = element('label', 'variant-chip');
          const box = element('input');
          box.type = 'checkbox';
          box.value = value;
          box.checked = (data.defaults?.[name] || []).includes(value);
          box.addEventListener('change', writeVariantFilter);
          chip.append(box, value);
          chips.appendChild(chip);
        });
        group.appendChild(chips);
        dims.appendChild(group);
      });
      dims.hidden = !Object.keys(dimensions).length;
    }
    table.hidden = !variants.length;
    writeVariantFilter();
    const nameField = newWatch.querySelector('[data-product-name]');
    if (nameField && (!nameField.value || !data.search)) nameField.value = data.search ? nameField.value : (data.title || '');
    body.hidden = false;
  };
  newWatch?.querySelector('[data-check-link]')?.addEventListener('click', async (event) => {
    const button = event.currentTarget;
    const url = newWatch.querySelector('[data-check-url]').value.trim();
    const error = newWatch.querySelector('[data-check-error]');
    const body = newWatch.querySelector('[data-new-body]');
    error.hidden = true;
    if (!url) return;
    button.disabled = true;
    button.textContent = 'Okunuyor…';
    try {
      const form = new URLSearchParams({ url, csrf_token: settingsForm.querySelector("[name='csrf_token']")?.value || '' });
      const response = await fetch(newWatch.dataset.checkPath, { method: 'POST', body: form, credentials: 'same-origin' });
      const data = await response.json();
      if (!data.ok) throw new Error(data.error || 'Sayfa okunamadı.');
      showProduct(data);
    } catch (problem) {
      body.hidden = true;
      error.textContent = problem.message || 'Sayfa okunamadı.';
      error.hidden = false;
    } finally {
      button.disabled = false;
      button.textContent = 'Kontrol et';
    }
  });
  newWatch?.querySelector('[data-check-url]')?.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter') return;
    event.preventDefault();
    newWatch.querySelector('[data-check-link]').click();
  });

  document.addEventListener('click', (event) => {
    const activeSwitch = event.target.closest('[data-active-switch]');
    if (activeSwitch) {
      // Inside a card's summary a click would also open the card; the switch only flips Aktif.
      event.preventDefault();
      const box = activeSwitch.querySelector('input');
      box.checked = !box.checked;
      box.dispatchEvent(new Event('change', { bubbles: true }));
      return;
    }
    const deleteButton = event.target.closest('[data-delete-watch]');
    if (!deleteButton) return;
    const card = deleteButton.closest('[data-watch-card]');
    const flag = card?.querySelector('[data-delete-flag]');
    if (!card || !flag) return;
    const marked = flag.value === '1';
    if (marked) {
      flag.value = '0';
      card.classList.remove('is-deleted');
      deleteButton.textContent = 'Sil';
    } else {
      if (!window.confirm('Bu takip silinecek. Devam?')) return;
      flag.value = '1';
      card.classList.add('is-deleted');
      deleteButton.textContent = 'Vazgeç';
    }
    dirty = true;
  });

  settingsForm?.addEventListener('input', () => { dirty = true; });
  settingsForm?.addEventListener('change', () => { dirty = true; });
  window.addEventListener('beforeunload', (event) => {
    if (!dirty) return;
    event.preventDefault();
    event.returnValue = '';
  });

  const picks = () => [...document.querySelectorAll('[data-watch-card]')]
    .filter((card) => !card.hidden && card.querySelector('[data-bulk-pick]')?.checked);
  const countPicks = () => {
    const label = document.querySelector('[data-bulk-count]');
    if (label) label.textContent = `${picks().length} seçili`;
  };
  const touch = (field) => { field.dispatchEvent(new Event('input', { bubbles: true })); field.dispatchEvent(new Event('change', { bubbles: true })); };
  document.addEventListener('change', (event) => { if (event.target.matches('[data-bulk-pick]')) countPicks(); });
  const bulk = (selector, value) => {
    const cards = picks();
    if (!cards.length) { window.alert('Önce en az bir kart seç.'); return; }
    cards.forEach((card) => {
      const field = card.querySelector(selector);
      if (!field) return;
      if (field.type === 'checkbox') field.checked = value; else field.value = value;
      touch(field);
    });
  };
  document.querySelectorAll('[data-bulk-active]').forEach((button) => button.addEventListener('click', () => bulk("input[type='checkbox'][name$='_active']", button.dataset.bulkActive === '1')));
  document.querySelector('[data-bulk-set-priority]')?.addEventListener('click', () => bulk('[data-watch-priority]', document.querySelector('[data-bulk-priority]').value));

  const savingOverlay = document.getElementById('saving-overlay');
  const savingTitle = document.getElementById('saving-title');
  const savingMessage = document.getElementById('saving-message');
  settingsForm?.addEventListener('submit', (event) => {
    const button = event.submitter;
    if (button) {
      button.classList.add('is-submitting');
      button.setAttribute('aria-disabled', 'true');
      button.textContent = 'Kaydediliyor...';
    }
    dirty = false;
    savingTitle.textContent = 'Ayarlar kaydediliyor';
    savingMessage.textContent = 'Kaydediliyor, Hermes yeniden başlıyor…';
    savingOverlay.hidden = false;
  });
})();
