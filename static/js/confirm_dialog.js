(() => {
  const dialog = document.createElement('dialog');
  dialog.className = 'action-confirm-dialog';
  dialog.setAttribute('aria-labelledby', 'action-confirm-title');
  dialog.innerHTML = `
    <section class="action-confirm-panel">
      <h2 id="action-confirm-title">Confirm action</h2>
      <p data-action-confirm-message></p>
      <div class="action-confirm-actions">
        <button class="btn btn-ghost" type="button" data-action-confirm-cancel>Cancel</button>
        <button class="btn action-confirm-submit" type="button" data-action-confirm-submit>Confirm</button>
      </div>
    </section>`;
  document.body.append(dialog);

  const message = dialog.querySelector('[data-action-confirm-message]');
  const title = dialog.querySelector('#action-confirm-title');
  const cancelButton = dialog.querySelector('[data-action-confirm-cancel]');
  const confirmButton = dialog.querySelector('[data-action-confirm-submit]');
  const confirmedForms = new WeakSet();
  let pendingForm = null;
  let pendingSubmitter = null;

  document.addEventListener('submit', (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || !form.hasAttribute('data-confirm')) return;
    if (confirmedForms.has(form)) {
      confirmedForms.delete(form);
      return;
    }
    event.preventDefault();
    pendingForm = form;
    pendingSubmitter = event.submitter;
    message.textContent = form.dataset.confirm || 'Are you sure you want to continue?';
    title.textContent = form.dataset.confirmTitle || 'Confirm action';
    confirmButton.textContent = form.dataset.confirmSubmit || 'Confirm';
    dialog.showModal();
    cancelButton.focus();
  });

  cancelButton.addEventListener('click', () => dialog.close());
  confirmButton.addEventListener('click', () => {
    const form = pendingForm;
    const submitter = pendingSubmitter;
    pendingForm = null;
    pendingSubmitter = null;
    dialog.close();
    if (!form?.isConnected) return;
    confirmedForms.add(form);
    if (submitter?.form === form) form.requestSubmit(submitter);
    else form.requestSubmit();
  });
  dialog.addEventListener('close', () => {
    pendingForm = null;
    pendingSubmitter = null;
  });
})();
