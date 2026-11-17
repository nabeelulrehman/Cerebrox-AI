(() => {
  const root = document.querySelector('[data-notes-tutor]');
  if (!root) return;
  const csrf = root.querySelector('[name=csrfmiddlewaretoken]')?.value || '';
  const output = root.querySelector('[data-explanation-output]');
  const form = root.querySelector('[data-tutor-form]');
  const messages = root.querySelector('[data-tutor-messages]');
  const status = root.querySelector('[data-tutor-status]');
  const post = async (url, data) => {
    const body = new URLSearchParams({...data, csrfmiddlewaretoken: csrf});
    const response = await fetch(url, {method:'POST', headers:{'X-CSRFToken':csrf, 'Content-Type':'application/x-www-form-urlencoded'}, body});
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || 'The request failed.');
    return payload;
  };
  root.querySelectorAll('[data-explain]').forEach(button => button.addEventListener('click', async () => {
    button.disabled = true; button.textContent = 'Generating…'; output.classList.remove('hidden'); output.textContent = 'Gemini is preparing a deeper explanation…';
    try { const result = await post(root.dataset.explainUrl, {section:button.dataset.explain, level:root.querySelector('[data-explain-level]').value}); output.textContent = result.explanation; }
    catch (error) { output.textContent = error.message; }
    finally { button.disabled = false; button.textContent = 'More Explanation'; }
  }));
  form.addEventListener('submit', async event => {
    event.preventDefault(); const input = form.elements.question; const question = input.value.trim(); if (!question) return;
    status.textContent = 'Thinking…'; form.querySelector('button').disabled = true;
    try {
      const result = await post(root.dataset.askUrl, {question});
      messages.replaceChildren(...result.messages.map(message => { const li = document.createElement('li'); const strong = document.createElement('strong'); strong.textContent = `${message.role === 'user' ? 'You' : 'Tutor'}: `; li.append(strong, document.createTextNode(message.content)); return li; }));
      input.value = ''; status.textContent = '';
    } catch (error) { status.textContent = error.message; }
    finally { form.querySelector('button').disabled = false; }
  });
})();
