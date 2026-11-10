(() => {
  document.querySelectorAll('[data-copy-meeting-link]').forEach((button) => {
    button.addEventListener('click', async () => {
      const status = document.querySelector('[data-copy-status]');
      try {
        await navigator.clipboard.writeText(button.dataset.copyMeetingLink);
        status.textContent = 'Meeting link copied.';
      } catch {
        status.textContent = 'Could not copy the meeting link. Select and copy the link above.';
      }
    });
  });
})();
