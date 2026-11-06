(() => {
  const dialog = document.querySelector('.course-dialog');
  const openButton = document.querySelector('[data-open-course-dialog]');

  if (!(dialog instanceof HTMLDialogElement) || !(openButton instanceof HTMLButtonElement)) {
    return;
  }

  let returnFocusTarget = openButton;

  const openDialog = () => {
    returnFocusTarget = document.activeElement instanceof HTMLElement ? document.activeElement : openButton;
    if (!dialog.open) {
      dialog.showModal();
    }
    dialog.querySelector('input:not([type="hidden"]), textarea, select')?.focus();
  };

  openButton.addEventListener('click', () => {
    if (openButton.dataset.editMode === 'true') {
      window.location.assign(`${openButton.dataset.addUrl}?add=1`);
      return;
    }
    openDialog();
  });

  dialog.querySelectorAll('[data-close-course-dialog]').forEach((button) => {
    button.addEventListener('click', () => dialog.close());
  });

  dialog.addEventListener('click', (event) => {
    if (event.target === dialog) {
      dialog.close();
    }
  });

  dialog.addEventListener('close', () => {
    if (returnFocusTarget.isConnected) {
      returnFocusTarget.focus();
    }
  });

  if (dialog.dataset.openOnLoad === 'true') {
    openDialog();
  }
})();
