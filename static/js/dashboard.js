// Animates bar-fill widths in from 0 on load (purely cosmetic).
document.addEventListener('DOMContentLoaded', ()=>{
  document.querySelectorAll('.bar-fill[data-w]').forEach(b=>{
    const score = Number.parseFloat(b.dataset.w);
    if (!Number.isFinite(score)) return;

    const dashboardBar = b.closest('.student-dashboard');
    const width = Math.min(100, Math.max(0, score));
    if (dashboardBar) {
      const scoreColor = score < 40
        ? 'var(--score-low)'
        : score < 70
          ? 'var(--score-mid)'
          : score < 85
            ? 'var(--score-good)'
            : 'var(--score-high)';
      b.style.setProperty('--score-color', scoreColor);
    }

    requestAnimationFrame(()=> b.style.width = width + '%');
  });
});
