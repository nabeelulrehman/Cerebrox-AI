(() => {
  const form = document.querySelector('[data-avatar-upload-url]');
  if (!form) return;

  const input = form.querySelector('[data-profile-avatar-input]');
  const submit = form.querySelector('button[type="submit"]');
  const progress = form.querySelector('[data-avatar-upload-progress]');
  const status = form.querySelector('[data-avatar-upload-status]');
  const csrf = form.querySelector('[name="csrfmiddlewaretoken"]')?.value;
  let busy = false;

  const showStatus = (message, isError = false) => {
    status.textContent = message;
    status.hidden = false;
    status.style.color = isError ? 'var(--bad)' : 'var(--txt-faint)';
  };

  const startUpload = async (file) => {
    const body = new FormData();
    body.append('csrfmiddlewaretoken', csrf);
    body.append('file_name', file.name);
    body.append('file_size', String(file.size));
    const response = await fetch(form.dataset.avatarUploadUrl, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'X-CSRFToken': csrf },
      body,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || 'Could not authorize the profile image upload.');
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
      showStatus(`Uploading profile image… ${percent}%`);
    };
    request.onload = () => request.status >= 200 && request.status < 300
      ? resolve()
      : reject(new Error('Storage rejected the image. Check bucket CORS settings and try again.'));
    request.onerror = () => reject(new Error('Upload connection failed. Check your connection and bucket CORS settings.'));
    request.ontimeout = () => reject(new Error('Upload timed out. Please try again.'));
    request.timeout = 2 * 60 * 1000;
    request.send(file);
  });

  form.addEventListener('submit', async (event) => {
    if (busy || !input?.files?.[0]) return;
    event.preventDefault();
    busy = true;
    submit.disabled = true;
    progress.hidden = false;
    progress.value = 0;
    try {
      showStatus('Preparing secure image upload…');
      const authorization = await startUpload(input.files[0]);
      await putFile(authorization, input.files[0]);

      let uploadId = form.querySelector('[name="avatar_upload_id"]');
      if (!uploadId) {
        uploadId = document.createElement('input');
        uploadId.type = 'hidden';
        uploadId.name = 'avatar_upload_id';
        form.append(uploadId);
      }
      uploadId.value = authorization.upload_id;
      input.value = '';
      showStatus('Image uploaded. Saving your profile…');
      busy = false;
      submit.disabled = false;
      form.requestSubmit();
    } catch (error) {
      showStatus(error.message || 'Image upload failed. Please try again.', true);
      submit.disabled = false;
      busy = false;
    }
  });
})();
