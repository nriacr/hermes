(() => {
  const storageKey = 'hermes-hidden-watch-groups';
  const hiddenGroups = new Set(JSON.parse(localStorage.getItem(storageKey) || '[]'));
  const normalize = (value) => String(value || '').toLocaleLowerCase('tr-TR');
  const watchSearch = document.getElementById('watch-search');
  const settingsForm = document.getElementById('settings-form');
  const watchesCount = document.getElementById('watches-count');
  const newWatchList = document.getElementById('new-watch-list');
  const newWatchTemplate = document.getElementById('new-watch-template');
  let dirty = false;

  const refreshWatchList = () => {
    const searchText = normalize(watchSearch?.value.trim());
    document.querySelectorAll('[data-watch-group]').forEach((item) => {
      const groupHidden = hiddenGroups.has(normalize(item.dataset.watchGroup || 'Diğer'));
      const productName = normalize(item.dataset.watchSearch);
      const searchHidden = Boolean(searchText) && !productName.includes(searchText);
      item.hidden = groupHidden || searchHidden;
    });
    document.querySelectorAll('[data-watch-group-filter]').forEach((button) => {
      const hidden = hiddenGroups.has(normalize(button.dataset.watchGroupFilter || 'Diğer'));
      button.setAttribute('aria-pressed', String(!hidden));
      button.title = hidden ? 'Grubu göster' : 'Grubu gizle';
    });
  };

  document.querySelectorAll('[data-watch-group-filter]').forEach((button) => {
    button.addEventListener('click', () => {
      const group = normalize(button.dataset.watchGroupFilter || 'Diğer');
      if (hiddenGroups.has(group)) hiddenGroups.delete(group); else hiddenGroups.add(group);
      localStorage.setItem(storageKey, JSON.stringify([...hiddenGroups]));
      refreshWatchList();
    });
  });
  watchSearch?.addEventListener('input', refreshWatchList);
  refreshWatchList();

  const nextWatchIndex = () => {
    const current = Number(watchesCount?.value || 0);
    const next = current;
    if (watchesCount) watchesCount.value = String(current + 1);
    return next;
  };

  document.getElementById('add-watch-card')?.addEventListener('click', () => {
    if (!newWatchTemplate || !newWatchList) return;
    const index = nextWatchIndex();
    newWatchList.insertAdjacentHTML(
      'beforeend',
      newWatchTemplate.innerHTML.replaceAll('__INDEX__', String(index)),
    );
    dirty = true;
  });

  document.addEventListener('click', (event) => {
    const removeButton = event.target.closest('[data-remove-new-watch]');
    if (removeButton) {
      const card = removeButton.closest('[data-watch-card]');
      card?.remove();
      dirty = true;
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
      if (!window.confirm('Bu takip, değişiklikleri uygulayınca silinecek. Devam etmek istiyor musun?')) return;
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
    savingMessage.textContent = 'Tüm değişiklikler tek seferde Home Assistant’a yazılıyor. Hermes bir kez yeniden başlayacak; hazır olduğunda ayarlara otomatik dönülecek.';
    savingOverlay.hidden = false;
  });
})();