document.querySelectorAll('[data-quiz-generation-form]').forEach((form) => {
  const button = form.querySelector('[data-quiz-submit]');
  const status = form.querySelector('[data-quiz-status]');
  let submitting = false;

  form.addEventListener('submit', (event) => {
    if (submitting) {
      event.preventDefault();
      return;
    }

    submitting = true;
    button.disabled = true;
    button.textContent = 'Generating quiz…';
    status.classList.remove('hidden');
  });
});
