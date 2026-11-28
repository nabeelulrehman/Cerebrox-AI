(() => {
  const root = document.querySelector('[data-notification-menu]');
  if (!root) return;

  const trigger = root.querySelector('[data-notification-trigger]');
  const dropdown = root.querySelector('[data-notification-dropdown]');
  const badge = root.querySelector('[data-notification-count]');
  const items = root.querySelector('[data-notification-items]');
  const readAllForm = root.querySelector('[data-notification-read-all]');
  const summaryUrl = '/notifications/api/summary/';
  let rows = [];

  function updateBadge(count) {
    badge.textContent = count > 99 ? '99+' : String(count);
    badge.hidden = count < 1;
  }

  function render() {
    items.replaceChildren();
    if (!rows.length) {
      const empty = document.createElement('p');
      empty.className = 'notification-empty';
      empty.textContent = 'You’re all caught up.';
      items.append(empty);
      return;
    }
    rows.forEach(item => {
      const link = document.createElement('a');
      link.className = `notification-item${item.is_read ? '' : ' is-unread'}`;
      link.href = `/notifications/${item.id}/open/`;
      const copy = document.createElement('span');
      copy.className = 'notification-item__copy';
      const title = document.createElement('strong');
      title.textContent = item.title;
      const message = document.createElement('span');
      message.textContent = item.message;
      const date = document.createElement('time');
      date.dateTime = item.created_at;
      date.textContent = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(item.created_at));
      copy.append(title, message, date);
      const state = document.createElement('span');
      state.className = `notification-item__state${item.is_read ? '' : ' is-unread'}`;
      state.textContent = item.is_read ? 'Read' : 'New';
      link.append(copy, state);
      items.append(link);
    });
  }

  async function refresh() {
    try {
      const response = await fetch(summaryUrl, { credentials: 'same-origin', headers: { 'X-Requested-With': 'XMLHttpRequest' } });
      if (!response.ok) return;
      const data = await response.json();
      rows = data.notifications;
      updateBadge(data.unread_count);
      render();
    } catch (_) { /* Keep the last rendered notification state during brief network errors. */ }
  }

  trigger.addEventListener('click', () => {
    const open = dropdown.hidden;
    dropdown.hidden = !open;
    trigger.setAttribute('aria-expanded', String(open));
    if (open) refresh();
  });
  document.addEventListener('click', event => {
    if (!root.contains(event.target)) {
      dropdown.hidden = true;
      trigger.setAttribute('aria-expanded', 'false');
    }
  });
  readAllForm.addEventListener('submit', async event => {
    event.preventDefault();
    const button = readAllForm.querySelector('button[type="submit"]');
    button.disabled = true;
    try {
      const response = await fetch(readAllForm.action, {
        method: 'POST', credentials: 'same-origin', body: new FormData(readAllForm),
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
      });
      if (response.ok) await refresh();
    } finally {
      button.disabled = false;
    }
  });
  refresh();
  window.setInterval(refresh, 25000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
})();
