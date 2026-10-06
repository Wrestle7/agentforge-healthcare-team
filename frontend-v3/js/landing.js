// Progressive decoration only. The separate entry module handles explicit submit.
// The shared theme helper persists only a/b in this tab's sessionStorage.
(() => {
  const root = document.documentElement;
  const body = document.body;
  const toggle = document.getElementById('motion-toggle');
  const status = document.getElementById('landing-status');
  const preference = window.matchMedia('(prefers-reduced-motion: reduce)');
  const finePointer = window.matchMedia('(hover: hover) and (pointer: fine) and (min-width: 768px)');
  const scene = document.querySelector('.landing-scene');
  const entry = document.getElementById('landing-composer');
  const surface = document.querySelector('.landing-entry-surface');
  const themeButtons = [...document.querySelectorAll('[data-theme-choice]')];
  let paused = false;
  let leaving = false;
  let navigationTimer = null;
  let recoveryTimer = null;
  let pointerFrame = null;
  let pointerPosition = null;
  const lifecycle = new AbortController();
  const on = (target, type, handler, options = {}) => target.addEventListener(type, handler, { ...options, signal: lifecycle.signal });
  on(window, 'agentforge:leave-landing', () => {
    resetNavigation(); lifecycle.abort();
    root.classList.remove('effects-ready', 'sheen-supported');
  });

  function resetPointer() {
    cancelAnimationFrame(pointerFrame); pointerFrame = null; pointerPosition = null;
    body.classList.remove('pointer-active');
    body.style.removeProperty('--scene-x'); body.style.removeProperty('--scene-y');
    scene.style.removeProperty('--pointer-x'); scene.style.removeProperty('--pointer-y');
    surface.style.removeProperty('--tilt-x'); surface.style.removeProperty('--tilt-y');
  }

  function pointerEnabled() { return !paused && !preference.matches && !document.hidden && !leaving && finePointer.matches; }

  function renderPointer() {
    pointerFrame = null;
    if (!pointerEnabled() || !pointerPosition) return;
    const {x, y} = pointerPosition;
    const clamp = value => Math.max(-1, Math.min(1, value));
    body.classList.add('pointer-active');
    body.style.setProperty('--scene-x', `${(clamp(x / innerWidth * 2 - 1) * 18).toFixed(2)}px`);
    body.style.setProperty('--scene-y', `${(clamp(y / innerHeight * 2 - 1) * 12).toFixed(2)}px`);
    const sceneRect = scene.getBoundingClientRect();
    scene.style.setProperty('--pointer-x', `${(x - sceneRect.left).toFixed(2)}px`);
    scene.style.setProperty('--pointer-y', `${(y - sceneRect.top).toFixed(2)}px`);
    const rect = entry.getBoundingClientRect();
    const inside = x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom;
    surface.style.setProperty('--tilt-x', `${inside ? (-clamp((y - rect.top) / rect.height * 2 - 1) * 2.5).toFixed(2) : 0}deg`);
    surface.style.setProperty('--tilt-y', `${inside ? (clamp((x - rect.left) / rect.width * 2 - 1) * 3).toFixed(2) : 0}deg`);
  }

  function syncMotion() {
    body.classList.toggle('effects-paused', paused || preference.matches || document.hidden);
    toggle.hidden = preference.matches;
    toggle.textContent = paused ? '恢复动效' : '暂停动效';
    if (!pointerEnabled()) resetPointer();
  }

  function resetNavigation() {
    clearTimeout(navigationTimer);
    clearTimeout(recoveryTimer);
    leaving = false;
    body.classList.remove('landing-leaving');
    document.getElementById('overview').removeAttribute('aria-busy');
    status.textContent = '';
    resetPointer();
  }

  root.classList.add('effects-ready');
  // Without typed custom properties, keep the decorative border static.
  if (typeof window.CSS?.registerProperty === 'function') root.classList.add('sheen-supported');
  syncMotion();
  function syncThemeButtons() {
    const theme = window.AgentForgeTheme?.get() || 'a';
    themeButtons.forEach(item => item.setAttribute('aria-pressed', String(item.dataset.themeChoice === theme)));
  }
  syncThemeButtons();
  on(window, 'agentforge:themechange', syncThemeButtons);
  document.getElementById('visual-switch').hidden = false;
  themeButtons.forEach(button => on(button, 'click', () => {
    if (leaving) return;
    window.AgentForgeTheme?.set(button.dataset.themeChoice);
    resetPointer();
    status.textContent = body.dataset.theme === 'b' ? '已切换到 B 深色沉浸风格，工作台入口不变' : '已切换到 A 浅色空间风格，工作台入口不变';
  }));

  on(toggle, 'click', () => { paused = !paused; syncMotion(); });
  on(preference, 'change', syncMotion);
  on(document, 'visibilitychange', syncMotion);
  on(finePointer, 'change', syncMotion);
  on(document, 'pointermove', event => {
    if (!pointerEnabled() || event.pointerType !== 'mouse') return;
    pointerPosition = {x: event.clientX, y: event.clientY};
    // At most one pending frame, scheduled only by input; no idle rendering loop.
    if (pointerFrame === null) pointerFrame = requestAnimationFrame(renderPointer);
  }, {passive: true});
  on(document, 'pointerleave', resetPointer);
  on(window, 'blur', resetPointer);
  on(window, 'resize', resetPointer);

  document.querySelectorAll('a[href^="/workspace/"]').forEach(link => {
    on(link, 'click', event => {
      // Preserve native modified-click, new-tab, download and keyboard behavior.
      if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || link.hasAttribute('download') || (link.target && link.target !== '_self')) return;
      if (leaving) { event.preventDefault(); return; }
      if (preference.matches || paused || document.hidden) return;

      event.preventDefault();
      leaving = true;
      resetPointer();
      body.classList.add('landing-leaving');
      document.getElementById('overview').setAttribute('aria-busy', 'true');
      status.textContent = '正在进入工作台';
      navigationTimer = setTimeout(() => {
        try { window.location.assign(link.href); }
        catch { resetNavigation(); }
      }, 180);
      // Restore a usable page if navigation is cancelled or takes unusually long.
      // This never retries navigation or submits a task.
      recoveryTimer = setTimeout(resetNavigation, 1500);
    });
  });

  // bfcache must not restore the dimmed/leaving state on Back.
  on(window, 'pagehide', resetNavigation);
  on(window, 'pageshow', event => {
    // Initial pageshow may arrive after an early click while module dependencies
    // are still loading. Do not cancel that click's pending navigation timer.
    if (event.persisted) resetNavigation();
    syncMotion();
  });
})();
