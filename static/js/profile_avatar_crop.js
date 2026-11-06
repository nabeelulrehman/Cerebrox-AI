(() => {
  const form = document.querySelector('.profile-avatar-field')?.closest('form');
  const input = form?.querySelector('[data-profile-avatar-input]');
  const dialog = document.querySelector('[data-profile-crop-dialog]');
  const source = dialog?.querySelector('[data-profile-crop-source]');
  const preview = dialog?.querySelector('[data-profile-crop-preview]');
  const previewCanvas = preview?.querySelector('[data-profile-crop-preview-canvas]');
  const error = document.querySelector('[data-profile-crop-error]');
  const confirmButton = dialog?.querySelector('[data-profile-crop-confirm]');
  const cancelButtons = dialog?.querySelectorAll('[data-profile-crop-cancel]');
  const zoomIn = dialog?.querySelector('[data-profile-crop-zoom-in]');
  const zoomOut = dialog?.querySelector('[data-profile-crop-zoom-out]');

  if (!form || !input || !dialog || !source || !preview || !previewCanvas || !error || !confirmButton || !cancelButtons || !zoomIn || !zoomOut) {
    return;
  }

  const previewContext = previewCanvas.getContext('2d');
  const maxBytes = 5 * 1024 * 1024;
  const allowedTypes = new Set(['image/jpeg', 'image/png', 'image/webp']);
  const allowedExtensions = /\.(jpe?g|png|webp)$/i;
  let cropper = null;
  let sourceUrl = null;
  let cropFileBase = 'profile-picture';
  let confirmedFile = input.files[0] ?? null;
  let cropConfirmed = true;
  let returnFocusTarget = input;
  let previewFrame = null;

  const showError = (message) => {
    error.textContent = message;
    error.hidden = false;
  };

  const clearError = () => {
    error.textContent = '';
    error.hidden = true;
  };

  const updatePreview = () => {
    if (!cropper || !previewContext || previewFrame !== null) return;
    previewFrame = requestAnimationFrame(() => {
      previewFrame = null;
      const croppedCanvas = cropper?.getCroppedCanvas({
        width: 128,
        height: 128,
        imageSmoothingEnabled: true,
        imageSmoothingQuality: 'high',
        fillColor: '#ffffff',
      });
      if (croppedCanvas) {
        previewContext.clearRect(0, 0, previewCanvas.width, previewCanvas.height);
        previewContext.drawImage(croppedCanvas, 0, 0, previewCanvas.width, previewCanvas.height);
      }
    });
  };

  const setInputFile = (file) => {
    const transfer = new DataTransfer();
    if (file) transfer.items.add(file);
    input.files = transfer.files;
  };

  const releaseCropper = () => {
    if (cropper) {
      cropper.destroy();
      cropper = null;
    }
    if (previewFrame !== null) {
      cancelAnimationFrame(previewFrame);
      previewFrame = null;
    }
    source.onload = null;
    source.onerror = null;
    if (sourceUrl) {
      URL.revokeObjectURL(sourceUrl);
      sourceUrl = null;
    }
    source.removeAttribute('src');
    previewContext?.clearRect(0, 0, previewCanvas.width, previewCanvas.height);
    confirmButton.disabled = false;
    confirmButton.textContent = 'Crop & Save';
  };

  const cancelCrop = () => {
    releaseCropper();
    setInputFile(confirmedFile);
    cropConfirmed = true;
    if (dialog.open) dialog.close();
  };

  const openCrop = (file) => {
    clearError();
    if (file.size > maxBytes) {
      setInputFile(confirmedFile);
      showError('Choose an image that is 5 MB or smaller.');
      return;
    }
    if (!allowedTypes.has(file.type) && !allowedExtensions.test(file.name)) {
      setInputFile(confirmedFile);
      showError('Choose a JPEG, PNG, or WebP image.');
      return;
    }
    if (!previewContext) {
      showError('Your browser could not preview the cropped image. Please try a different browser.');
      return;
    }
    if (typeof window.Cropper !== 'function') {
      cropConfirmed = false;
      input.value = '';
      showError('The crop tool could not be loaded. Refresh the page and try again.');
      return;
    }

    cropConfirmed = false;
    input.value = '';
    cropFileBase = file.name.replace(/\.[^.]+$/, '').replace(/[^a-zA-Z0-9_-]+/g, '-').slice(0, 60) || 'profile-picture';
    returnFocusTarget = input;
    sourceUrl = URL.createObjectURL(file);
    source.onload = () => {
      try {
        cropper = new window.Cropper(source, {
          aspectRatio: 1,
          viewMode: 1,
          dragMode: 'move',
          autoCropArea: 0.82,
          cropBoxMovable: true,
          cropBoxResizable: true,
          responsive: true,
          restore: false,
          background: false,
          toggleDragModeOnDblclick: false,
          crop: updatePreview,
        });
        updatePreview();
      } catch {
        showError('The selected image could not be opened for cropping. Choose another image.');
        cancelCrop();
        return;
      }
    };
    source.onerror = () => {
      showError('The selected image could not be loaded. Choose another image.');
      cancelCrop();
    };
    source.src = sourceUrl;

    try {
      dialog.showModal();
      cancelButtons[0].focus();
    } catch {
      releaseCropper();
      setInputFile(confirmedFile);
      cropConfirmed = true;
      showError('The crop window could not be opened. Please try again.');
    }
  };

  input.addEventListener('change', () => {
    const file = input.files[0];
    if (file) openCrop(file);
  });

  confirmButton.addEventListener('click', () => {
    if (!cropper) {
      showError('Wait for the image to finish loading before cropping.');
      return;
    }

    confirmButton.disabled = true;
    confirmButton.textContent = 'Preparing crop…';
    let canvas;
    try {
      canvas = cropper.getCroppedCanvas({
        width: 512,
        height: 512,
        imageSmoothingEnabled: true,
        imageSmoothingQuality: 'high',
        fillColor: '#ffffff',
      });
    } catch {
      confirmButton.disabled = false;
      confirmButton.textContent = 'Crop & Save';
      showError('The image could not be cropped. Adjust the image and try again.');
      return;
    }

    if (!canvas) {
      confirmButton.disabled = false;
      confirmButton.textContent = 'Crop & Save';
      showError('The image could not be cropped. Adjust the image and try again.');
      return;
    }

    canvas.toBlob((blob) => {
      if (!blob) {
        confirmButton.disabled = false;
        confirmButton.textContent = 'Crop & Save';
        showError('The cropped image could not be prepared. Please try again.');
        return;
      }

      const croppedFile = new File([blob], `${cropFileBase}-cropped.jpg`, {
        type: 'image/jpeg',
        lastModified: Date.now(),
      });

      try {
        setInputFile(croppedFile);
      } catch {
        confirmButton.disabled = false;
        confirmButton.textContent = 'Crop & Save';
        showError('Your browser could not attach the cropped image. Please try a different browser.');
        return;
      }

      confirmedFile = croppedFile;
      cropConfirmed = true;
      clearError();
      releaseCropper();
      if (dialog.open) dialog.close();
    }, 'image/jpeg', 0.92);
  });

  zoomIn.addEventListener('click', () => cropper?.zoom(0.1));
  zoomOut.addEventListener('click', () => cropper?.zoom(-0.1));

  cancelButtons.forEach((button) => button.addEventListener('click', cancelCrop));
  dialog.addEventListener('click', (event) => {
    if (event.target === dialog) cancelCrop();
  });
  dialog.addEventListener('cancel', (event) => {
    event.preventDefault();
    cancelCrop();
  });
  dialog.addEventListener('close', () => {
    releaseCropper();
    if (returnFocusTarget.isConnected) returnFocusTarget.focus();
  });

  form.addEventListener('submit', (event) => {
    if (!cropConfirmed) {
      event.preventDefault();
      showError('Crop the selected image or cancel before saving your profile.');
    }
  });
})();
