// A same-document handoff keeps the question in memory only: never in a URL,
// history.state, window.name, browser storage, or a new server-side draft store.
import { showStartupFailure } from './startup.js?v=20261005-boot2';
const form = document.getElementById('landing-question-panel');
const input = document.getElementById('landing-question');
const send = document.getElementById('landing-send');
const errorBox = document.getElementById('entry-error');
const tabs = [...document.querySelectorAll('.entry-tabs [role="tab"]')];
let entering = false, composing = false;

function mode(index, focus = false) {
  if (entering) return;
  tabs.forEach((tab, i) => {
    tab.setAttribute('aria-selected', String(i === index)); tab.tabIndex = i === index ? 0 : -1;
    document.getElementById(tab.getAttribute('aria-controls')).hidden = i !== index;
  });
  if (focus) tabs[index].focus();
}
tabs.forEach((tab, index) => {
  tab.addEventListener('click', () => mode(index));
  tab.addEventListener('keydown', event => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault(); mode(event.key === 'Home' ? 0 : event.key === 'End' ? 1 : 1 - index, true);
  });
});
function refresh() {
  send.disabled = entering || !input.value.trim();
  document.getElementById('landing-question-count').textContent = `${input.value.length.toLocaleString()} / 10,000`;
}
input.addEventListener('input', refresh);
input.addEventListener('compositionstart', () => { composing = true; });
input.addEventListener('compositionend', () => { composing = false; });
input.addEventListener('keydown', event => {
  if (event.key === 'Enter' && !event.shiftKey && !composing && !event.isComposing && event.keyCode !== 229) {
    event.preventDefault(); form.requestSubmit();
  }
});

function loadAsset(node) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => { node.remove(); reject(new Error('资源加载超时')); }, 15000);
    node.onload = () => { clearTimeout(timer); resolve(node); };
    node.onerror = () => { clearTimeout(timer); node.remove(); reject(new Error('资源加载失败')); };
    document.head.append(node);
  });
}

async function openWorkspace(message) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 15000);
  const styles = [];
  let mounted = false, started = false;
  let releaseNavigation = () => {};
  try {
    // Fetch the existing local shell, not patient data; execute only known scripts.
    const response = await fetch('/workspace/', { signal: controller.signal, cache: 'no-store' });
    if (!response.ok) throw new Error('工作台页面不可用');
    const html = new DOMParser().parseFromString(await response.text(), 'text/html');
    if (!html.querySelector('#app-shell') || !html.querySelector('#message-input')) throw new Error('工作台页面不完整');
    const allowedStyles = new Set(['/css/theme.css', '/css/workspace.css', '/css/workspace-shell.css', '/css/patients.css', '/css/response.css']);
    const links = [...html.querySelectorAll('link[rel="stylesheet"]')];
    if (links.length !== allowedStyles.size || links.some(link => !allowedStyles.has(new URL(link.href).pathname) || new URL(link.href).origin !== location.origin)) throw new Error('工作台资源不匹配');
    const assets = links.map(link => {
      const copy = document.createElement('link'); copy.rel = 'stylesheet'; copy.href = link.getAttribute('href');
      copy.media = 'print'; styles.push(copy); return loadAsset(copy);
    });
    for (const [global, src] of [['markdownit', '/vendor/markdown-it.min.js'], ['DOMPurify', '/vendor/purify.min.js']]) {
      if (!window[global]) { const script = document.createElement('script'); script.src = src; assets.push(loadAsset(script)); }
    }
    await Promise.all(assets);
    // No question in navigation state. Back/Forward reload a normal document and
    // cannot replay this one-shot action. Regular workspace entry still restores history.
    window.dispatchEvent(new Event('agentforge:leave-landing'));
    const oldStyles = [...document.querySelectorAll('link[rel="stylesheet"]')].filter(link => !styles.includes(link));
    const body = document.importNode(html.body, true);
    body.dataset.theme = window.AgentForgeTheme?.get() || 'a';
    body.querySelector('#message-input').readOnly = true;
    body.querySelector('#composer').setAttribute('aria-busy', 'true');
    body.querySelectorAll('[data-view], [data-new-chat]').forEach(node => { node.disabled = true; });
    document.body.replaceWith(body); mounted = true;
    for (const style of styles) style.media = 'all';
    oldStyles.forEach(link => link.remove());
    const apiMeta = html.querySelector('meta[name="api-key"]');
    if (apiMeta) document.head.append(document.importNode(apiMeta, true));
    document.title = html.title;
    // Guard the module-loading gap too: the app's own navigation handlers do
    // not exist until import resolves. Keep one canonical route while pending.
    const keepWorkspaceRoute = () => {
      if (location.pathname !== '/workspace/') history.pushState(null, '', '/workspace/#message-input');
    };
    window.addEventListener('popstate', keepWorkspaceRoute);
    releaseNavigation = () => window.removeEventListener('popstate', keepWorkspaceRoute);
    history.pushState(null, '', '/workspace/#message-input');
    window.scrollTo(0, 0);
    const loadingNotice = document.getElementById('notice');
    loadingNotice.hidden = false; loadingNotice.textContent = '正在打开工作台，请稍候…';
    const { start } = await import('./app.js?v=20261005-boot2');
    input.value = ''; // Clear only after scripts load; failures retain the draft in memory.
    releaseNavigation(); // The app has installed its own busy/history guards.
    const initialMessage = message; message = '';
    started = true;
    await start({ initialMessage });
  } catch (error) {
    if (!mounted) {
      styles.forEach(link => link.remove());
      throw new Error('工作台加载失败，问题尚未发送。已保留输入，请检查服务后再试。');
    }
    // Never rerun start or retry chat: execution status might be unknown.
    showStartupFailure({ started, draft: message });
  } finally { clearTimeout(timer); releaseNavigation(); }
}

form.addEventListener('submit', async event => {
  event.preventDefault();
  if (entering || composing || !input.value.trim()) return;
  entering = true; input.readOnly = true; tabs.forEach(tab => { tab.disabled = true; }); refresh();
  form.setAttribute('aria-busy', 'true'); errorBox.hidden = true;
  document.getElementById('landing-status').textContent = '正在打开工作台并提交问题';
  try { await openWorkspace(input.value.trim()); }
  catch (error) {
    errorBox.textContent = error.message; errorBox.hidden = false;
    entering = false; input.readOnly = false; tabs.forEach(tab => { tab.disabled = false; }); refresh();
    form.setAttribute('aria-busy', 'false'); input.focus();
  }
});
// Prevent abandoning a pending handoff via another landing entry.
document.addEventListener('click', event => {
  if (entering && form.isConnected && event.target.closest('a[href]')) event.preventDefault();
}, { capture: true });
document.getElementById('landing-composer').hidden = false;
// A reloaded landing document can inherit forward same-document history entries
// from a previous handoff. Render their real route, never replay a question.
window.addEventListener('popstate', () => {
  if (form.isConnected && location.pathname !== '/') location.reload();
});
window.addEventListener('pageshow', () => {
  if (form.isConnected && location.pathname !== '/') location.reload();
});
refresh();
