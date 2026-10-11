(() => {
  const form = document.getElementById('pin-form');
  const input = document.getElementById('pin');
  const button = document.getElementById('unlock');
  if (!form || !input || !button) return;
  let submitting = false;
  const refresh = () => {
    input.value = input.value.replace(/[^0-9]/g, '').slice(0, 4);
    document.querySelectorAll('.pin-slots span').forEach((slot, index) => {
      slot.textContent = input.value.length > index ? '●' : '';
    });
    button.disabled = submitting || input.disabled || input.value.length !== 4;
  };
  input.addEventListener('input', () => {
    document.getElementById('pin-message').textContent = '';
    refresh();
    if (input.value.length === 4 && !submitting && !input.disabled) form.requestSubmit();
  });
  form.addEventListener('submit', (event) => {
    if (submitting || input.disabled || input.value.length !== 4) {
      event.preventDefault();
      return;
    }
    submitting = true;
    button.textContent = 'Kontrol ediliyor…';
    refresh();
  });
  refresh();
  const retryAfter = Number(form.dataset.retryAfter) || 0;
  if (retryAfter > 0) window.setTimeout(() => window.location.replace(form.dataset.pinPath), retryAfter * 1000);
  if (!input.disabled) input.focus();
})();
