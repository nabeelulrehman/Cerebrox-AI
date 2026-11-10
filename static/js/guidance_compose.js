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
    dialog.querySelector('[data-recipient-search], input:not([type="hidden"]), textarea')?.focus();
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
    dialog.querySelector('[data-recipient-search], [aria-invalid="true"]:not([type="hidden"]), input:not([type="hidden"]), textarea')?.focus();
  }

  const picker = dialog.querySelector('[data-recipient-picker]');
  if (!picker) return;
  const search = picker.querySelector('[data-recipient-search]');
  const hiddenRecipient = dialog.querySelector('input[name="recipient"]');
  const results = picker.querySelector('[data-recipient-results]');
  const status = picker.querySelector('[data-recipient-status]');
  const selected = picker.querySelector('[data-recipient-selected]');
  const selectedName = picker.querySelector('[data-recipient-selected-name]');
  const clearSelection = picker.querySelector('[data-recipient-clear]');
  const form = picker.closest('form');
  let debounceTimer;
  let activeIndex = -1;
  let requestController;
  let requestNumber = 0;

  const hideResults = () => {
    results.hidden = true;
    search.setAttribute('aria-expanded', 'false');
    search.removeAttribute('aria-activedescendant');
    activeIndex = -1;
  };

  const setSelection = (person) => {
    hiddenRecipient.value = person.id;
    search.value = person.label;
    selectedName.textContent = person.label;
    selected.hidden = false;
    status.textContent = `Selected recipient: ${person.label}`;
    hideResults();
  };

  const showMatches = (people) => {
    results.replaceChildren();
    if (!people.length) {
      hideResults();
      status.textContent = 'No users found.';
      return;
    }
    people.forEach((person, index) => {
      const option = document.createElement('button');
      option.type = 'button';
      option.className = 'guidance-recipient-option';
      option.id = `guidance-recipient-option-${person.id}`;
      option.setAttribute('role', 'option');
      option.setAttribute('aria-selected', 'false');
      option.dataset.index = String(index);
      const label = document.createElement('span');
      label.className = 'guidance-recipient-option__name';
      label.textContent = person.label;
      const detail = document.createElement('span');
      detail.className = 'guidance-recipient-option__detail';
      detail.textContent = person.detail;
      option.append(label, detail);
      option.addEventListener('click', () => setSelection(person));
      results.append(option);
    });
    results.hidden = false;
    search.setAttribute('aria-expanded', 'true');
    status.textContent = `${people.length} ${people.length === 1 ? 'user' : 'users'} found. Use the arrow keys to choose.`;
  };

  const searchRecipients = async (query) => {
    requestController?.abort();
    requestController = new AbortController();
    const currentRequest = ++requestNumber;
    status.textContent = 'Searching…';
    try {
      const url = new URL(picker.dataset.searchUrl, window.location.origin);
      url.searchParams.set('q', query);
      const response = await fetch(url, {
        credentials: 'same-origin',
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
        signal: requestController.signal,
      });
      if (!response.ok) throw new Error('Recipient search failed.');
      const body = await response.json();
      if (currentRequest === requestNumber) showMatches(body.results || []);
    } catch (error) {
      if (error.name !== 'AbortError' && currentRequest === requestNumber) {
        hideResults();
        status.textContent = 'Could not search users. Try again.';
      }
    }
  };

  search.addEventListener('input', () => {
    clearTimeout(debounceTimer);
    requestController?.abort();
    requestNumber += 1;
    hideResults();
    if (hiddenRecipient.value) {
      hiddenRecipient.value = '';
      selected.hidden = true;
      selectedName.textContent = '';
    }
    const query = search.value.trim();
    if (query.length < 2) {
      requestController?.abort();
      hideResults();
      status.textContent = 'Type at least two characters to search.';
      return;
    }
    status.textContent = 'Searching…';
    debounceTimer = setTimeout(() => searchRecipients(query), 250);
  });

  search.addEventListener('keydown', (event) => {
    const options = [...results.querySelectorAll('[role="option"]')];
    if (event.key === 'ArrowDown' && options.length) {
      event.preventDefault();
      activeIndex = (activeIndex + 1) % options.length;
    } else if (event.key === 'ArrowUp' && options.length) {
      event.preventDefault();
      activeIndex = activeIndex <= 0 ? options.length - 1 : activeIndex - 1;
    } else if (event.key === 'Enter' && !results.hidden && activeIndex >= 0) {
      event.preventDefault();
      options[activeIndex].click();
    } else if (event.key === 'Escape' && !results.hidden) {
      event.preventDefault();
      hideResults();
    } else {
      return;
    }
    options.forEach((option, index) => option.setAttribute('aria-selected', String(index === activeIndex)));
    if (activeIndex >= 0) search.setAttribute('aria-activedescendant', options[activeIndex].id);
  });

  clearSelection.addEventListener('click', () => {
    hiddenRecipient.value = '';
    selected.hidden = true;
    selectedName.textContent = '';
    search.value = '';
    status.textContent = 'Type at least two characters to search.';
    search.focus();
  });

  form.addEventListener('submit', (event) => {
    if (hiddenRecipient.value) return;
    event.preventDefault();
    status.textContent = 'Select a recipient from the search results.';
    search.focus();
  });
})();
