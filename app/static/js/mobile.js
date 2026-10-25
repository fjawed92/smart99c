/* Phone helpers shared by the store and the admin panel. */
'use strict';

// Tables with .table-stack turn into one card per row on phones (see style.css).
// Each cell gets its column name as a label so the card still reads clearly.
document.querySelectorAll('table.table-stack').forEach(table => {
  const heads = [...table.querySelectorAll('thead th')].map(th => th.textContent.trim());
  table.querySelectorAll('tbody tr').forEach(tr => {
    [...tr.children].forEach((td, i) => {
      if (td.hasAttribute('colspan')) { td.classList.add('stack-full'); return; }
      if (!td.hasAttribute('data-label')) td.setAttribute('data-label', heads[i] || '');
    });
  });
});

// "Filters" buttons that open a panel on phones.
document.querySelectorAll('.filters-toggle').forEach(btn => {
  const panel = document.querySelector(btn.dataset.target);
  if (!panel) return;
  btn.addEventListener('click', () => {
    const open = panel.classList.toggle('open');
    btn.setAttribute('aria-expanded', open ? 'true' : 'false');
  });
});

// Pause the scrolling news banner while the page scrolls, so phones only
// repaint what moved (keeps the bottom bar smooth on iPhone).
(function () {
  let timer = null;
  window.addEventListener('scroll', () => {
    if (!timer) document.body.classList.add('is-scrolling');
    clearTimeout(timer);
    timer = setTimeout(() => { document.body.classList.remove('is-scrolling'); timer = null; }, 150);
  }, { passive: true });
})();
