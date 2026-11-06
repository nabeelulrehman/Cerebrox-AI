(() => {
  const dialog = document.querySelector('[data-guidance-compose-dialog]');
  const openButton = document.querySelector('[data-guidance-compose-open]');
  const closeButtons = dialog?.querySelectorAll('[data-guidance-compose-close]');

  if (!dialog || !openButton || !closeButtons) return;

  let returnFocusTarget = openButton;

  const openDialog = () => {
    if (dialog.open) return;
    returnFocusTarget = document.activeElement instanceof HTMLElement ? document.activeElement : openButton;
    dialog.showModal();
    dialog.querySelector('select, input, textarea')?.focus();
  };

  const closeDialog = () => {
    if (dialog.open) dialog.close();
  };

  openButton.addEventListener('click', openDialog);
  closeButtons.forEach((button) => button.addEventListener('click', closeDialog));

  dialog.addEventListener('click', (event) => {
    if (event.target === dialog) closeDialog();
  });

  dialog.addEventListener('close', () => {
    if (returnFocusTarget.isConnected) returnFocusTarget.focus();
  });

  if (dialog.hasAttribute('data-guidance-compose-invalid')) {
    dialog.showModal();
    dialog.querySelector('[aria-invalid="true"], select, input, textarea')?.focus();
  }
})();
