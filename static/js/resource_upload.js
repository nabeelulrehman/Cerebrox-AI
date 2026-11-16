(() => {
  const form = document.querySelector('[data-resource-upload]');
  if (!form) return;

  const fileInput = form.querySelector('input[type="file"]');
  const submit = form.querySelector('[data-upload-submit]');
  const progress = form.querySelector('[data-upload-progress]');
  const status = form.querySelector('[data-upload-status]');
  const csrf = form.querySelector('[name="csrfmiddlewaretoken"]')?.value;
  let busy = false;

  const showStatus = (message, isError = false) => {
    status.textContent = message;
    status.hidden = false;
    status.setAttribute('aria-live', isError ? 'assertive' : 'polite');
    status.style.color = isError ? 'var(--bad)' : 'var(--txt-faint)';
  };

  const jsonRequest = async (url, body) => {
    const response = await fetch(url, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'X-CSRFToken': csrf },
      body,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = payload.error || Object.values(payload.errors || {})
        .flat().map((item) => item.message).join(' ');
      throw new Error(detail || 'The server could not authorize this upload.');
    }
    return payload;
  };

  const putFile = (authorization, file) => new Promise((resolve, reject) => {
    let destination;
    try {
      destination = new URL(authorization.url);
      if (destination.protocol !== 'https:') throw new Error('Storage URL must use HTTPS.');
    } catch {
      reject(new Error('The secure storage URL was invalid.'));
      return;
    }
    const request = new XMLHttpRequest();
    request.open('PUT', destination.href);
    Object.entries(authorization.headers).forEach(([name, value]) => request.setRequestHeader(name, value));
    request.upload.onprogress = (event) => {
      if (!event.lengthComputable) return;
      const percent = Math.round((event.loaded / event.total) * 100);
      progress.value = percent;
      showStatus(`Uploading… ${percent}%`);
    };
    request.onload = () => {
      if (request.status >= 200 && request.status < 300) resolve();
      else reject(new Error('Storage rejected the upload. Check the bucket CORS settings and try again.'));
    };
    request.onerror = () => reject(new Error('Upload connection failed. Check your connection and bucket CORS settings.'));
    request.ontimeout = () => reject(new Error('Upload timed out. Try again on a stable connection.'));
    request.timeout = 15 * 60 * 1000;
    request.send(file);
  });

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (busy) return;
    const file = fileInput.files?.[0];
    if (!file) {
      showStatus('Choose a file before uploading.', true);
      return;
    }

    busy = true;
    submit.disabled = true;
    progress.hidden = false;
    progress.value = 0;
    showStatus('Preparing secure upload…');
    try {
      const startData = new FormData(form);
      startData.delete('file');
      startData.append('file_name', file.name);
      startData.append('file_size', String(file.size));
      const authorization = await jsonRequest(form.dataset.startUrl, startData);
      await putFile(authorization, file);

      showStatus('Verifying upload and saving resource…');
      const finishData = new FormData();
      finishData.append('csrfmiddlewaretoken', csrf);
      finishData.append('upload_id', authorization.upload_id);
      const result = await jsonRequest(form.dataset.finishUrl, finishData);
      showStatus(result.message || 'Upload received and is awaiting its security scan.');
      window.location.reload();
    } catch (error) {
      showStatus(error.message || 'Upload failed. Please try again.', true);
      submit.disabled = false;
      busy = false;
    }
  });
})();
