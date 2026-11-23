(() => {
  const form = document.querySelector('[data-explain-more]');
  if (!form) return;

  const button = form.querySelector('button[type="submit"]');
  const status = form.querySelector('[data-explain-status]');
  const history = document.querySelector('[data-additional-explanations]');

  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (button.disabled) return;

    const csrf = form.querySelector('[name=csrfmiddlewaretoken]')?.value || '';
    const requestId = window.crypto?.randomUUID?.()
      || `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    button.disabled = true;
    button.textContent = 'Generating…';
    status.textContent = 'Preparing a deeper explanation of the topic…';

    try {
      const body = new URLSearchParams({csrfmiddlewaretoken: csrf, request_id: requestId});
      const response = await fetch(form.dataset.url, {
        method: 'POST',
        headers: {'X-CSRFToken': csrf, 'Content-Type': 'application/x-www-form-urlencoded'},
        body,
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'The explanation could not be generated.');

      const section = document.createElement('section');
      section.className = 'border-t pt-5';
      const heading = document.createElement('h2');
      heading.className = 'font-display font-semibold mb-2';
      heading.textContent = 'More detail';
      const paragraph = document.createElement('p');
      paragraph.style.color = 'var(--txt-dim)';
      paragraph.style.whiteSpace = 'pre-line';
      paragraph.textContent = result.explanation;
      section.append(heading, paragraph);
      history.append(section);
      status.textContent = 'Added to your notes.';
    } catch (error) {
      status.textContent = error.message || 'The explanation could not be generated. Please try again.';
    } finally {
      button.disabled = false;
      button.textContent = 'Explain More';
    }
  });
})();
