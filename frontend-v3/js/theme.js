// Loaded before styles to avoid flashing a light workspace after a dark landing.
// Store only an allowlisted visual preference; never content, parameters or PHI.
(() => {
  const key = 'af_v3_theme';
  const root = document.documentElement;
  let current = 'a';
  const normalize = value => value === 'b' ? 'b' : 'a';
  function read() {
    try { return normalize(sessionStorage.getItem(key)); }
    catch { return current; } // Storage disabled: styling remains usable in memory.
  }
  function apply(theme) {
    current = normalize(theme);
    root.dataset.theme = current;
    if (document.body) document.body.dataset.theme = current;
    window.dispatchEvent(new CustomEvent('agentforge:themechange', {detail: current}));
  }
  window.AgentForgeTheme = Object.freeze({
    get: () => current,
    set(theme) {
      apply(theme);
      try { sessionStorage.setItem(key, current); } catch { /* Optional preference. */ }
    },
  });
  apply(read());
  document.addEventListener('DOMContentLoaded', () => apply(current), {once: true});
  // Reconcile a restored page with this tab's most recent explicit selection.
  window.addEventListener('pageshow', () => apply(read()));
})();
