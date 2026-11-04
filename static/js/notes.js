document.querySelectorAll('[data-notes-stream-url]').forEach((form) => {
  const progress = form.parentElement.querySelector('[data-notes-progress]');
  const spinner = progress.querySelector('[data-notes-spinner]');
  const status = progress.querySelector('[data-notes-status]');
  const preview = progress.querySelector('[data-notes-preview]');
  const error = progress.querySelector('[data-notes-error]');
  const retry = progress.querySelector('[data-notes-retry]');
  const submitButtons = form.querySelectorAll('button[type="submit"]');
  let running = false;

  const reset = () => {
    status.textContent = 'Analyzing topic';
    preview.textContent = '';
    preview.hidden = true;
    error.textContent = '';
    error.hidden = true;
    retry.hidden = true;
  };

  const setBusy = (busy) => {
    running = busy;
    spinner.classList.toggle('hidden', !busy);
    submitButtons.forEach((button) => {
      button.disabled = busy;
    });
  };

  const displayFailure = (message) => {
    setBusy(false);
    status.textContent = 'Generation stopped';
    error.textContent = message;
    error.hidden = false;
    retry.hidden = false;
  };

  const handleEvent = (event) => {
    if (event.type === 'status') {
      status.textContent = event.message;
    } else if (event.type === 'chunk') {
      preview.hidden = false;
      preview.textContent += event.text;
    } else if (event.type === 'error') {
      throw new Error(event.message);
    } else if (event.type === 'complete') {
      setBusy(false);
      status.textContent = 'Notes ready — opening your study material';
      window.location.assign(event.url);
      return true;
    }
    return false;
  };

  const readStream = async (response) => {
    if (!response.body) {
      throw new Error('Streaming is unavailable in this browser. Please reload and try again.');
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let completed = false;

    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
      let boundary = buffer.indexOf('\n\n');
      while (boundary !== -1) {
        const frame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const data = frame.split('\n').find((line) => line.startsWith('data: '));
        if (data) {
          completed = handleEvent(JSON.parse(data.slice(6))) || completed;
        }
        boundary = buffer.indexOf('\n\n');
      }
      if (done) break;
    }

    if (buffer.trim()) {
      const data = buffer.split('\n').find((line) => line.startsWith('data: '));
      if (data) {
        completed = handleEvent(JSON.parse(data.slice(6))) || completed;
      }
    }
    if (!completed) {
      throw new Error('The connection ended before notes were complete. Please retry.');
    }
  };

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (running) return;

    reset();
    progress.hidden = false;
    setBusy(true);
    try {
      const response = await fetch(form.dataset.notesStreamUrl, {
        method: 'POST',
        body: new FormData(form),
        headers: {
          Accept: 'text/event-stream',
          'X-Requested-With': 'XMLHttpRequest',
        },
        credentials: 'same-origin',
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.error || 'Notes could not be generated. Please retry.');
      }
      if (!response.headers.get('content-type')?.includes('text/event-stream')) {
        throw new Error('Your session may have expired. Reload the page and try again.');
      }
      await readStream(response);
    } catch (generationError) {
      displayFailure(generationError.message || 'Notes could not be generated. Please retry.');
    }
  });

  retry.addEventListener('click', () => {
    form.requestSubmit();
  });
});
