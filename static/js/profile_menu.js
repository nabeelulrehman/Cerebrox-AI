(() => {
  const container = document.querySelector('[data-profile-menu]');
  const trigger = container?.querySelector('[data-profile-menu-trigger]');
  const dropdown = container?.querySelector('[data-profile-menu-dropdown]');
  const dialog = document.querySelector('[data-profile-dialog]');
  const dialogContent = dialog?.querySelector('[data-profile-dialog-content]');
  const closeButton = dialog?.querySelector('[data-profile-dialog-close]');

  if (!container || !trigger || !dropdown || !dialog || !dialogContent || !closeButton) return;

  const loadedScripts = new Map();
  const setOpen = (open, restoreFocus = false) => {
    dropdown.hidden = !open;
    trigger.setAttribute('aria-expanded', String(open));
    if (restoreFocus) trigger.focus();
  };

  const showStatus = (message) => {
    let status = dialogContent.querySelector('[data-profile-dialog-status]');
    if (!status) {
      status = document.createElement('p');
      status.className = 'site-profile-dialog__status';
      status.setAttribute('role', 'status');
      status.dataset.profileDialogStatus = '';
      dialogContent.append(status);
    }
    status.textContent = message;
  };

  const loadScript = (src) => {
    if (loadedScripts.has(src)) return loadedScripts.get(src);
    const loading = new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.src = src;
      script.onload = resolve;
      script.onerror = () => {
        script.remove();
        reject(new Error('A profile form script could not be loaded.'));
      };
      document.body.append(script);
    });
    loadedScripts.set(src, loading);
    loading.catch(() => loadedScripts.delete(src));
    return loading;
  };

  const loadStylesheet = (href) => {
    if ([...document.querySelectorAll('link[rel="stylesheet"]')].some((link) => link.href === href)) return;
    const stylesheet = document.createElement('link');
    stylesheet.rel = 'stylesheet';
    stylesheet.href = href;
    document.head.append(stylesheet);
  };

  const initializeForm = async (page) => {
    const formStyles = [...page.querySelectorAll('link[rel="stylesheet"]')]
      .filter((link) => /cropper\.min\.css|profile_avatar_crop\.css/.test(link.href));
    formStyles.forEach((link) => loadStylesheet(link.href));

    const cropperScript = [...page.querySelectorAll('script[src]')]
      .find((script) => /cropper(?:\.min)?\.js/.test(script.src));
    const cropScript = [...page.querySelectorAll('script[src]')]
      .find((script) => /profile_avatar_crop\.js/.test(script.src));
    const paymentFieldsScript = [...page.querySelectorAll('script[src]')]
      .find((script) => /teacher_payment_fields\.js/.test(script.src));

    if (cropperScript && typeof window.Cropper !== 'function') await loadScript(cropperScript.src);
    if (cropScript) {
      await loadScript(cropScript.src);
      window.initializeProfileAvatarCrop?.(dialogContent);
    }
    if (paymentFieldsScript) {
      await loadScript(paymentFieldsScript.src);
      window.initializeTeacherPaymentFields?.(dialogContent);
    }
  };

  const renderPage = async (response) => {
    const page = new DOMParser().parseFromString(await response.text(), 'text/html');
    const content = page.querySelector('.page-content');
    if (!content) throw new Error('The requested form could not be displayed.');
    dialogContent.replaceChildren(content);
    dialogContent.querySelector('[data-profile-dialog-status]')?.remove();
    await initializeForm(page);
    dialogContent.querySelectorAll('form:not([action])').forEach((form) => {
      form.setAttribute('action', response.url);
    });
    dialogContent.querySelector('input:not([type="hidden"]), select, textarea, button')?.focus();
    dialogContent.querySelectorAll('form').forEach((form) => {
      form.addEventListener('submit', submitForm);
    });
  };

  const fetchPage = async (url, options) => {
    const response = await fetch(url, {
      ...options,
      credentials: 'same-origin',
      headers: { 'X-Requested-With': 'XMLHttpRequest', ...options?.headers },
    });
    if (!response.ok && response.status >= 500) {
      throw new Error('The form could not be loaded. Please try again.');
    }
    if (options?.method === 'POST' && response.redirected) {
      window.location.reload();
      return;
    }
    await renderPage(response);
  };

  const submitForm = async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const submitButton = form.querySelector('button[type="submit"], input[type="submit"]');
    if (submitButton) submitButton.disabled = true;
    try {
      await fetchPage(form.action || window.location.href, {
        method: form.method.toUpperCase(),
        body: new FormData(form),
      });
    } catch (error) {
      showStatus(error.message);
      if (submitButton?.isConnected) submitButton.disabled = false;
    }
  };

  const openForm = async (url) => {
    dialog.showModal();
    closeButton.focus();
    dialogContent.replaceChildren();
    showStatus('Loading form…');
    try {
      await fetchPage(url);
    } catch (error) {
      showStatus(error.message);
    }
  };

  trigger.addEventListener('click', () => {
    setOpen(dropdown.hidden);
  });

  dropdown.querySelectorAll('[data-profile-form-link]').forEach((link) => {
    link.addEventListener('click', (event) => {
      event.preventDefault();
      setOpen(false);
      openForm(link.href);
    });
  });

  closeButton.addEventListener('click', () => dialog.close());
  dialog.addEventListener('close', () => setOpen(false, true));
  dialog.addEventListener('click', (event) => {
    if (event.target === dialog) dialog.close();
  });

  document.addEventListener('pointerdown', (event) => {
    if (!container.contains(event.target)) setOpen(false);
  });

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !dropdown.hidden) {
      setOpen(false, true);
    }
  });
})();
