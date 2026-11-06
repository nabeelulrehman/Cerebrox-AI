(() => {
  const trigger = document.querySelector('[data-avatar-preview]');
  const dialog = document.querySelector('[data-avatar-dialog]');
  const closeButton = dialog?.querySelector('[data-avatar-close]');

  if (!trigger || !dialog || !closeButton) return;

  let closeTimer;

  const closePreview = () => {
    if (!dialog.open || dialog.classList.contains('is-closing')) return;

    dialog.classList.add('is-closing');
    closeTimer = window.setTimeout(() => {
      dialog.close();
      dialog.classList.remove('is-closing');
      trigger.focus();
    }, 160);
  };

  trigger.addEventListener('click', () => {
    window.clearTimeout(closeTimer);
    dialog.showModal();
    closeButton.focus();
  });

  closeButton.addEventListener('click', closePreview);
  dialog.addEventListener('click', (event) => {
    if (event.target === dialog) closePreview();
  });
  window.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && dialog.open) {
      event.preventDefault();
      closePreview();
    }
  });
  dialog.addEventListener('cancel', (event) => {
    event.preventDefault();
    closePreview();
  });
})();
