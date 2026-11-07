(() => {
  const normalize = (value) => String(value || '').toLocaleLowerCase('tr-TR');
  const watchSearch = document.getElementById('watch-search');
  const settingsForm = document.getElementById('settings-form');
  let dirty = false;
  // Apply and Vazgeç wait until something changed.
  const setDirty = (value) => {
    dirty = value;
    document.querySelectorAll('[data-apply], [data-discard]').forEach((button) => { button.disabled = !value; });
  };
  setDirty(false);
  document.addEventListener('hermes-session-locked', () => setDirty(false));
  document.querySelector('[data-discard]')?.addEventListener('click', () => {
    setDirty(false);
    window.location.reload();
  });

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

  // Prices show thousands separators while typing: 100000 -> 100.000.
  document.addEventListener('input', (event) => {
    const field = event.target;
    if (!field.matches?.("input[name$='_target_price'], input[name$='_minimum_price']")) return;
    const digitsAfterCaret = field.value.slice(field.selectionStart).replace(/\D/g, '').length;
    const digits = field.value.replace(/\D/g, '').replace(/^0+(?=\d)/, '');
    field.value = digits.replace(/\B(?=(\d{3})+(?!\d))/g, '.');
    let caret = field.value.length;
    for (let seen = 0; caret > 0 && seen < digitsAfterCaret; caret -= 1) if (/\d/.test(field.value[caret - 1])) seen += 1;
    field.setSelectionRange(caret, caret);
  });

  let newWatch = document.querySelector('[data-new-watch]');
  // The empty form, reused for the next link after "Ekle".
  const pristineWatch = newWatch?.outerHTML || '';
  const watchesCount = document.getElementById('watches-count');
  const pristineIndex = Number(watchesCount?.value || 1) - 1;
  const queuedList = document.querySelector('[data-queued-list]');
  const element = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  // The new card and an opened saved card share the variant table, choice boxes and size boxes.
  const writeVariantFilter = (root) => {
    const chosen = [...root.querySelectorAll('[data-variant-dim]')].map((group) => {
      const values = [...group.querySelectorAll('input:checked')].map((box) => box.value);
      return values.length ? `${group.dataset.variantDim}=${values.join(', ')}` : '';
    }).filter(Boolean);
    const field = root.querySelector('[data-variant-filter]');
    if (field) field.value = chosen.join('; ');
  };
  document.addEventListener('change', (event) => {
    if (!event.target.closest('[data-variant-dim]')) return;
    writeVariantFilter(event.target.closest('[data-new-watch], [data-watch-card]'));
  });
  const parseChoices = (text) => {
    const chosen = {};
    String(text || '').split(';').forEach((part) => {
      const [dimension, list] = part.split('=');
      if (dimension && list) chosen[dimension.trim()] = list.split(',').map((value) => value.trim()).filter(Boolean);
    });
    return chosen;
  };
  // One size box per chosen size; choosing one opens the next. Saved as "M; L".
  const renderSizes = (root, chosen) => {
    const boxes = root.querySelector('[data-size-boxes]');
    if (!boxes) return;
    const sizeChoices = root.sizeChoices || [];
    boxes.replaceChildren();
    [...chosen, ''].forEach((value, position) => {
      const remaining = sizeChoices.filter(([size]) => size === value || !chosen.includes(size));
      if (position > 0 && !remaining.length) return;
      const label = element('label', '', position ? `Beden ${position + 1}` : 'Beden');
      const select = element('select');
      select.appendChild(new Option(position ? 'Seç' : 'Hepsi', ''));
      remaining.forEach(([size, text]) => select.appendChild(new Option(text, size)));
      select.value = value;
      select.addEventListener('change', () => {
        const next = [...boxes.querySelectorAll('select')].map((box) => box.value).filter(Boolean);
        renderSizes(root, [...new Set(next)]);
      });
      label.appendChild(select);
      boxes.appendChild(label);
    });
    root.querySelector('[data-sizes]').value = chosen.join('; ');
  };
  // A value is out of stock only when no variant with it is in stock.
  const outOfStockValues = (variants) => {
    const inStock = new Set(variants.filter((item) => item.in_stock).flatMap((item) => Object.values(item.attributes || {})));
    return new Set(variants.flatMap((item) => Object.values(item.attributes || {})).filter((value) => !inStock.has(value)));
  };
  const renderVariantTable = (root, variants) => {
    const table = root.querySelector('[data-variant-table]');
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
    table.hidden = !variants.length;
  };
  // Out-of-stock values stay choosable: the card waits for them to come back. A saved value the page
  // no longer lists stays too, so a choice is never dropped silently.
  const renderChips = (root, data, chosen, saved = {}) => {
    const options = new Set(data.options || []);
    const outOfStock = outOfStockValues(data.variants || []);
    const dims = root.querySelector('[data-variant-dims]');
    dims.replaceChildren();
    if (options.has('variant_filter')) {
      const dimensions = { ...(data.dimensions || {}) };
      Object.entries(saved).forEach(([name, values]) => { dimensions[name] = [...new Set([...(dimensions[name] || []), ...values])]; });
      Object.entries(dimensions).forEach(([name, values]) => {
        if (values.length < 2 && !saved[name]) return;  // nothing to choose
        if (name === 'Beden' && options.has('size') && !saved[name]) return;  // sizes have their own boxes
        const listed = new Set(data.dimensions?.[name] || []);
        const group = element('div', 'variant-dim');
        group.dataset.variantDim = name;
        group.appendChild(element('span', '', name));
        const chips = element('div', 'variant-chips');
        values.forEach((value) => {
          const chip = element('label', outOfStock.has(value) || !listed.has(value) ? 'variant-chip is-out' : 'variant-chip');
          const box = element('input');
          box.type = 'checkbox';
          box.value = value;
          box.checked = (chosen[name] || []).includes(value);
          chip.append(box, value);
          chips.appendChild(chip);
        });
        group.appendChild(chips);
        dims.appendChild(group);
      });
    }
    dims.hidden = !dims.children.length;
    writeVariantFilter(root);
    const sizes = options.has('size') ? data.dimensions?.Beden || [] : [];
    root.sizeChoices = sizes.map((size) => [size, outOfStock.has(size) ? `${size} (tükendi)` : size]);
  };

  let lastCheck = null;
  const showProduct = (data) => {
    lastCheck = data;
    const body = newWatch.querySelector('[data-new-body]');
    newWatch.querySelector('[data-product-title]').textContent = data.title || '';
    const variants = data.variants || [];
    const priced = variants.filter((item) => item.price);
    // A single product shows its live price large; a family shows its variant count above the table.
    newWatch.querySelector('[data-product-sub]').classList.toggle('is-price', variants.length <= 1);
    newWatch.querySelector('[data-product-sub]').textContent = variants.length > 1
      ? `${variants.length} ${'Mağaza' in (data.dimensions || {}) ? 'mağaza' : 'Model' in (data.dimensions || {}) ? 'model' : 'varyant'}` : (priced[0]?.price || '');
    renderVariantTable(newWatch, variants);
    const options = new Set(data.options || []);
    const dimensions = data.dimensions || {};
    newWatch.querySelectorAll('[data-site-option]').forEach((node) => {
      node.hidden = !options.has(node.dataset.siteOption) || ('searchOnly' in node.dataset && !data.search);
    });
    newWatch.querySelector('[data-seller-label]').textContent = data.seller_label || '';
    // A model site whose list is empty right now asks for the model name to wait for.
    const typedModel = options.has('model') && !(data.dimensions?.Model || []).length;
    newWatch.querySelector('[data-site-option="model"]').hidden = !options.has('model') || typedModel;
    if (typedModel) newWatch.querySelector('[data-site-option="name"]').hidden = false;
    newWatch.querySelector('[data-name-label]').textContent = options.has('model') ? 'Model' : 'Ürün adı';
    // A model site (Togg) names the card after the chosen model.
    const modelSelect = newWatch.querySelector('[data-model-select]');
    modelSelect.replaceChildren(new Option('Seç', ''), ...(dimensions.Model || []).map((model) => new Option(model, model)));
    modelSelect.onchange = () => { newWatch.querySelector('[data-product-name]').value = modelSelect.value; };
    renderChips(newWatch, data, data.defaults || {});
    renderSizes(newWatch, []);
    const nameField = newWatch.querySelector('[data-product-name]');
    if (nameField && (!nameField.value || !data.search)) nameField.value = data.search ? nameField.value : (options.has('model') ? '' : data.title || '');
    body.hidden = false;
  };
  // The check runs on Hermes in the background; ask for its result until it is ready.
  const readLink = async (url) => {
    const csrf = settingsForm.querySelector("[name='csrf_token']")?.value || '';
    const ask = async (fields) => {
      const response = await fetch(newWatch.dataset.checkPath, { method: 'POST', body: new URLSearchParams({ ...fields, csrf_token: csrf }),
                                                                 credentials: 'same-origin' });
      try { return await response.json(); } catch { throw new Error('Hermes yanıt vermedi; tekrar dene.'); }
    };
    const { job, error: startError } = await ask({ url });
    if (!job) throw new Error(startError || 'Sayfa okunamadı.');
    let data;
    do {
      await new Promise((resolve) => { setTimeout(resolve, 1500); });
      data = await ask({ job });
    } while (data.pending);
    if (!data.ok) throw new Error(data.error || 'Sayfa okunamadı.');
    return data;
  };
  // A saved card reads its page the first time it opens; a failed read tries again on the next open.
  document.addEventListener('toggle', async (event) => {
    const card = event.target;
    const box = card.matches?.('[data-watch-card]') && card.open ? card.querySelector('[data-edit-variants]') : null;
    if (!box?.dataset.url || box.dataset.state) return;
    box.dataset.state = 'reading';
    const status = box.querySelector('[data-edit-status]');
    status.className = 'edit-status';
    status.replaceChildren(element('span', 'spinner'), 'Okunuyor');
    status.hidden = false;
    try {
      const data = await readLink(box.dataset.url);
      renderVariantTable(box, data.variants || []);
      const saved = parseChoices(card.querySelector('[data-variant-filter]')?.value);
      renderChips(card, data, saved, saved);
      const sizeText = card.querySelector('[data-size-text]');
      if (sizeText) {
        sizeText.hidden = true;
        card.querySelector('[data-size-boxes]').hidden = false;
        const sizes = sizeText.querySelector('input').value.split(';').map((size) => size.trim()).filter(Boolean);
        card.sizeChoices.push(...sizes.filter((size) => !card.sizeChoices.some(([known]) => known === size)).map((size) => [size, size]));
        renderSizes(card, sizes);
      }
      status.hidden = true;
      box.dataset.state = 'done';
    } catch (problem) {
      status.className = 'check-error';
      status.textContent = problem.message || 'Sayfa okunamadı.';
      delete box.dataset.state;
    }
  }, true);
  const checkLink = async (button) => {
    const url = newWatch.querySelector('[data-check-url]').value.trim();
    const error = newWatch.querySelector('[data-check-error]');
    const body = newWatch.querySelector('[data-new-body]');
    error.hidden = true;
    if (!url) return;
    button.disabled = true;
    button.replaceChildren(element('span', 'spinner'), 'Okunuyor');
    try {
      showProduct(await readLink(url));
    } catch (problem) {
      body.hidden = true;
      error.textContent = problem.message || 'Sayfa okunamadı.';
      error.hidden = false;
    } finally {
      button.disabled = false;
      button.textContent = 'Kontrol et';
    }
  };
  document.addEventListener('click', (event) => {
    const button = event.target.closest('[data-check-link]');
    if (button) checkLink(button);
  });
  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter' || !event.target.matches('[data-check-url]')) return;
    event.preventDefault();
    newWatch.querySelector('[data-check-link]').click();
  });

  // "Ekle" keeps the checked card as hidden fields and opens an empty form; one apply saves them all.
  document.addEventListener('click', (event) => {
    if (!event.target.closest('[data-queue-watch]')) return;
    const error = newWatch.querySelector('[data-check-error]');
    const target = newWatch.querySelector("input[name$='_target_price']");
    const name = newWatch.querySelector('[data-product-name]');
    const model = newWatch.querySelector('[data-model-select]');
    const missing = !target.value.trim() ? target
      : (!model.closest('[hidden]') && !model.value ? model : (!name.closest('[hidden]') && !name.value.trim() ? name : null));
    if (missing) {
      error.textContent = missing === target ? 'Hedef fiyatı yaz.' : missing === model ? 'Modeli seç.' : `${newWatch.querySelector('[data-name-label]').textContent === 'Model' ? 'Modeli' : 'Ürün adını'} yaz.`;
      error.hidden = false;
      missing.focus();
      return;
    }
    const row = element('div', 'queued-watch');
    row.dataset.index = newWatch.querySelector("input[name$='_url_1']").name.match(/^watches_(\d+)_/)[1];
    row.checkData = lastCheck;
    newWatch.querySelectorAll('input[name], select[name]').forEach((field) => {
      if (field.type === 'checkbox' && !field.checked) return;
      const copy = element('input');
      copy.type = 'hidden';
      copy.name = field.name;
      copy.value = field.value;
      row.appendChild(copy);
    });
    const modelCard = newWatch.querySelector('[data-name-label]').textContent === 'Model';
    const title = (modelCard && name.value) || newWatch.querySelector('[data-product-title]').textContent || name.value
      || newWatch.querySelector('[data-check-url]').value;
    const remove = element('button', 'queued-remove', 'Kaldır');
    remove.type = 'button';
    remove.addEventListener('click', () => { row.remove(); setDirty(true); });
    const edit = element('button', 'queued-name', title);
    edit.type = 'button';
    edit.title = 'Düzelt';
    edit.addEventListener('click', () => reopenQueued(row));
    row.append(edit, element('span', 'queued-price', `${target.value} TL`), remove);
    const placeholder = queuedList.querySelector(`[data-reopened='${row.dataset.index}']`);
    if (placeholder) placeholder.replaceWith(row); else queuedList.appendChild(row);
    const index = Number(watchesCount.value);
    watchesCount.value = String(index + 1);
    openForm(index);
    newWatch.querySelector('[data-check-url]').focus();
    setDirty(true);
  });

  // "Temizle" empties the form back to its default state; nothing is saved.
  document.addEventListener('click', (event) => {
    if (!event.target.closest('[data-clear-watch]')) return;
    const index = newWatch.querySelector("input[name$='_url_1']").name.match(/^watches_(\d+)_/)[1];
    queuedList.querySelector(`[data-reopened='${index}']`)?.remove();
    openForm(index);
    newWatch.querySelector('[data-check-url]').focus();
  });

  const openForm = (index) => {
    const holder = document.createElement('div');
    holder.innerHTML = pristineWatch.replaceAll(`watches_${pristineIndex}_`, `watches_${index}_`);
    const fresh = holder.firstElementChild;
    newWatch.replaceWith(fresh);
    newWatch = fresh;
    lastCheck = null;
  };
  // A queued card opens again in the form with its own values; "Ekle" puts it back in its place.
  const reopenQueued = (row) => {
    const urlField = newWatch.querySelector('[data-check-url]');
    const error = newWatch.querySelector('[data-check-error]');
    if (urlField.value.trim()) {
      error.textContent = 'Önce bu linki ekle ya da linki sil.';
      error.hidden = false;
      return;
    }
    openForm(row.dataset.index);
    const values = {};
    row.querySelectorAll('input[type=hidden]').forEach((copy) => { values[copy.name] = copy.value; });
    if (row.checkData) showProduct(row.checkData);
    newWatch.querySelectorAll('input[name], select[name]').forEach((field) => {
      if (field.type === 'checkbox') field.checked = field.name in values;
      else if (field.name in values) field.value = values[field.name];
    });
    const chosen = parseChoices(values[`watches_${row.dataset.index}_variant_filter`]);
    newWatch.querySelectorAll('[data-variant-dim]').forEach((group) => {
      group.querySelectorAll('input').forEach((box) => { box.checked = (chosen[group.dataset.variantDim] || []).includes(box.value); });
    });
    writeVariantFilter(newWatch);
    newWatch.querySelector('[data-model-select]').value = values[`watches_${row.dataset.index}_name`] || '';
    newWatch.querySelectorAll('[data-time-pick]').forEach(loadTimePick);
    renderSizes(newWatch, (values[`watches_${row.dataset.index}_size`] || '').split(';').map((size) => size.trim()).filter(Boolean));
    const marker = element('div', 'queued-watch is-open');
    marker.dataset.reopened = row.dataset.index;
    marker.append(element('span', 'queued-name', `${row.querySelector('.queued-name').textContent} (düzeltiliyor)`));
    row.replaceWith(marker);
    newWatch.querySelector("input[name$='_target_price']").focus();
  };

  // A time pick (hour + minute selects) writes "HH:MM" into its hidden field.
  const syncTimePick = (pick) => {
    pick.querySelector('input[type=hidden]').value = `${pick.querySelector('[data-time-hour]').value}:${pick.querySelector('[data-time-minute]').value}`;
  };
  const loadTimePick = (pick) => {
    const [hour = '00', minute = '00'] = pick.querySelector('input[type=hidden]').value.split(':');
    const minuteSelect = pick.querySelector('[data-time-minute]');
    if (![...minuteSelect.options].some((option) => option.value === minute)) minuteSelect.add(new Option(minute, minute));
    pick.querySelector('[data-time-hour]').value = hour;
    minuteSelect.value = minute;
  };
  document.addEventListener('change', (event) => {
    const pick = event.target.closest?.('[data-time-pick]');
    if (pick) syncTimePick(pick);
  });

  // "Gece modu" opens its two times.
  document.addEventListener('change', (event) => {
    if (!event.target.matches('[data-night-toggle]')) return;
    event.target.closest('[data-night-mode]').querySelector('[data-night-times]').hidden = !event.target.checked;
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
      flag.value = '1';
      card.classList.add('is-deleted');
      deleteButton.textContent = 'Vazgeç';
    }
    setDirty(true);
  });

  // Searching the list or ticking cards for bulk actions changes nothing by itself.
  const changes = (event) => !event.target.matches('#watch-search, [data-bulk-pick], [data-check-url]');
  settingsForm?.addEventListener('input', (event) => { if (changes(event)) setDirty(true); });
  settingsForm?.addEventListener('change', (event) => { if (changes(event)) setDirty(true); });
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

  // A card is dragged by its handle, up and down only, inside its own site; the others slide out of
  // its way and it settles into its place on release. The shown order is saved with the page.
  const orderField = document.querySelector('[data-watch-order]');
  const writeOrder = () => {
    orderField.value = [...document.querySelectorAll('[data-watch-card]')]
      .map((card) => card.querySelector("input[name$='_id']")?.value || '').join(',');
    setDirty(true);
  };
  const slide = (cards, move) => {
    // Move the DOM, then animate each sibling from where it was to where it now is.
    const before = new Map(cards.map((card) => [card, card.getBoundingClientRect().top]));
    move();
    cards.forEach((card) => {
      const shift = before.get(card) - card.getBoundingClientRect().top;
      if (!shift) return;
      card.style.transition = 'none';
      card.style.transform = `translateY(${shift}px)`;
      requestAnimationFrame(() => {
        card.style.transition = 'transform .18s ease';
        card.style.transform = '';
      });
    });
  };
  let drag = null;
  document.addEventListener('click', (event) => { if (event.target.closest('[data-drag-handle]')) event.preventDefault(); });
  document.addEventListener('pointerdown', (event) => {
    const handle = event.target.closest('[data-drag-handle]');
    if (!handle || event.button !== 0) return;
    event.preventDefault();
    const card = handle.closest('[data-watch-card]');
    drag = { card, list: card.parentElement, startY: event.clientY, top: card.getBoundingClientRect().top, moved: false };
    try { handle.setPointerCapture(event.pointerId); } catch { /* the document listeners still follow the pointer */ }
    card.classList.add('is-dragging');
  });
  document.addEventListener('pointermove', (event) => {
    if (!drag) return;
    const { card, list } = drag;
    const others = [...list.children].filter((item) => item !== card);
    const bounds = list.getBoundingClientRect();
    const height = card.getBoundingClientRect().height;
    // Only vertical, and never outside the site's own list.
    const wanted = Math.min(Math.max(drag.top + event.clientY - drag.startY, bounds.top), bounds.bottom - height);
    const middle = wanted + height / 2;
    const next = others.find((item) => middle < item.getBoundingClientRect().top + item.getBoundingClientRect().height / 2) || null;
    if (next !== card.nextElementSibling) {
      slide(others, () => list.insertBefore(card, next));
      drag.moved = true;
    }
    card.style.transition = 'none';
    card.style.transform = `translateY(${wanted - (card.getBoundingClientRect().top - (parseFloat(card.style.transform.slice(11)) || 0))}px)`;
  });
  const drop = () => {
    if (!drag) return;
    const { card, moved } = drag;
    drag = null;
    card.style.transition = 'transform .18s ease';
    card.style.transform = '';
    card.addEventListener('transitionend', () => { card.classList.remove('is-dragging'); card.style.transition = ''; }, { once: true });
    setTimeout(() => card.classList.remove('is-dragging'), 250);
    if (moved) writeOrder();
  };
  document.addEventListener('pointerup', drop);
  document.addEventListener('pointercancel', drop);

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
    setDirty(false);
    savingTitle.textContent = 'Ayarlar kaydediliyor';
    savingMessage.textContent = 'Kaydediliyor, Hermes yeniden başlıyor…';
    savingOverlay.hidden = false;
  });
})();
