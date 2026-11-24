(() => {
  const toggle = document.querySelector('[data-mobile-sidebar-toggle]');
  const sidebar = document.querySelector('#sidebar');
  const backdrop = document.querySelector('[data-mobile-sidebar-backdrop]');
  const menuIcon = toggle?.querySelector('[data-menu-icon]');
  const closeIcon = toggle?.querySelector('[data-close-icon]');

  if (!toggle || !backdrop) return;
  if (!sidebar) {
    toggle.hidden = true;
    return;
  }

  const setOpen = (open, restoreFocus = false) => {
    sidebar.classList.toggle('mobile-sidebar-open', open && window.matchMedia('(max-width: 767px)').matches);
    const isOpen = sidebar.classList.contains('mobile-sidebar-open');
    backdrop.hidden = !isOpen;
    toggle.setAttribute('aria-expanded', String(isOpen));
    toggle.setAttribute('aria-label', isOpen ? 'Close navigation menu' : 'Open navigation menu');
    if (menuIcon) menuIcon.hidden = isOpen;
    if (closeIcon) closeIcon.hidden = !isOpen;
    if (restoreFocus) toggle.focus();
  };

  toggle.addEventListener('click', () => {
    setOpen(!sidebar.classList.contains('mobile-sidebar-open'));
  });
  backdrop.addEventListener('click', () => setOpen(false, true));
  sidebar.querySelectorAll('a').forEach(link => link.addEventListener('click', () => setOpen(false)));
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && sidebar.classList.contains('mobile-sidebar-open')) {
      setOpen(false, true);
    }
  });
  window.matchMedia('(min-width: 768px)').addEventListener('change', event => {
    if (event.matches) setOpen(false);
  });
})();
