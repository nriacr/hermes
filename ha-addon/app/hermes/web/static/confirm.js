document.querySelectorAll('form[data-confirm]').forEach((form) => {
  form.addEventListener('submit', (event) => {
    if (!window.confirm(form.getAttribute('data-confirm') || 'Bu işlemi yapmak istediğine emin misin?')) event.preventDefault();
  });
});
