(function () {
  const STORAGE_KEY = 'jd_theme';
  const doc = document.documentElement;

  function getStoredTheme() {
    let value = null;
    try {
      value = localStorage.getItem(STORAGE_KEY);
    } catch (err) {
      return null;
    }
    if (value === 'light' || value === 'dark') {
      return value;
    }
    return null;
  }

  function preferredTheme() {
    const stored = getStoredTheme();
    if (stored) {
      return stored;
    }
    if (window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches) {
      return 'light';
    }
    return 'dark';
  }

  function setColorScheme(theme) {
    doc.setAttribute('data-theme', theme);
    doc.style.colorScheme = theme === 'light' ? 'light' : 'dark';
  }

  function apply(theme) {
    const normalized = theme === 'light' ? 'light' : 'dark';
    setColorScheme(normalized);
    try {
      localStorage.setItem(STORAGE_KEY, normalized);
    } catch (err) {
      /* no-op */
    }
    const detail = { theme: normalized };
    document.dispatchEvent(new CustomEvent('jd-theme-change', { detail }));
  }

  function get() {
    return doc.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
  }

  function updateToggle(toggle, theme) {
    if (!toggle) return;
    const icon = theme === 'light' ? '🌙' : '🌞';
    toggle.innerHTML = `<span aria-hidden="true">${icon}</span>`;
    toggle.setAttribute('aria-label', theme === 'light' ? 'Switch to dark theme' : 'Switch to light theme');
  }

  function initToggle(toggle) {
    if (!toggle) return;
    updateToggle(toggle, get());
    toggle.addEventListener('click', () => {
      const next = get() === 'light' ? 'dark' : 'light';
      apply(next);
    });
    document.addEventListener('jd-theme-change', (event) => {
      updateToggle(toggle, event.detail.theme);
    });
  }

  function boot() {
    const initial = preferredTheme();
    setColorScheme(initial);
  }

  boot();
  document.addEventListener('DOMContentLoaded', () => {
    const toggle = document.getElementById('theme_toggle');
    if (toggle) {
      initToggle(toggle);
    }
  });

  window.JDTheme = {
    get,
    apply,
    preferredTheme,
    initToggle,
    STORAGE_KEY,
  };
})();
