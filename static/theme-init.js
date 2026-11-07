/* Apply the saved/system theme before CSS renders, preventing a PWA theme flash. */
(() => {
  'use strict';
  const key = 'cabinet-theme';
  const root = document.documentElement;
  const scheme = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;
  let preference;
  try { preference = localStorage.getItem(key); } catch (_) { preference = null; }
  function apply(value) {
    const theme = value === 'dark' ? 'dark' : 'light';
    root.dataset.theme = theme;
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.content = theme === 'dark' ? '#191719' : '#F8F4EF';
    document.querySelectorAll('[data-theme-toggle]').forEach(button => {
      button.setAttribute('aria-label', theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode');
      button.setAttribute('title', theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode');
    });
  }
  const system = () => scheme && scheme.matches ? 'dark' : 'light';
  apply(preference === 'dark' || preference === 'light' ? preference : system());
  const select = theme => {
    preference = theme;
    try { localStorage.setItem(key, theme); } catch (_) { /* still apply visually */ }
    apply(theme);
  };
  if (scheme) {
    const onSystemChange = () => { if (preference !== 'dark' && preference !== 'light') apply(system()); };
    if (scheme.addEventListener) scheme.addEventListener('change', onSystemChange);
    else if (scheme.addListener) scheme.addListener(onSystemChange);
  }
  window.CabinetTheme = { select, refresh: () => apply(root.dataset.theme) };
})();
