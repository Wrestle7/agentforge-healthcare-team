// Recovery navigation never carries/replays a question or invokes business APIs.
export function showStartupFailure({ started = false, draft = '' } = {}) {
  const notice = document.getElementById('notice');
  if (!notice) return;
  document.querySelectorAll('#app-shell button').forEach(node => { node.disabled = true; });
  const input = document.getElementById('message-input');
  if (input) { input.disabled = false; input.readOnly = true; if (!started && draft) input.value = draft; }
  document.getElementById('composer')?.addEventListener('submit', event => {
    event.preventDefault(); event.stopImmediatePropagation();
  }, { capture: true });
  const connection = document.getElementById('connection');
  if (connection) { connection.className = 'connection offline'; connection.lastElementChild.textContent = '工作台启动失败'; }
  notice.replaceChildren(); notice.hidden = false; notice.setAttribute('role', 'alert');
  const text = document.createElement('p');
  text.textContent = started
    ? '工作台初始化中断，任务状态尚未确认。不会自动重发问题，请先重新打开工作台核对历史记录。'
    : '工作台脚本加载失败，本次问题尚未发送。请重新打开工作台；下方如有输入，可先选中复制，重新打开后不会自动提交。';
  const actions = document.createElement('div'); actions.className = 'startup-actions';
  for (const [label, href] of [['重新打开工作台', '/workspace/#history'], ['返回首页', '/']]) {
    const link = document.createElement('a'); link.className = 'button button-small button-quiet';
    link.href = href; link.textContent = label;
    if (href.startsWith('/workspace/')) link.addEventListener('click', event => {
      // A fragment-only link would leave failed imports in the current module
      // map. Explicit user recovery must start a fresh document, without replay.
      event.preventDefault(); history.replaceState(null, '', href); location.reload();
    });
    actions.append(link);
  }
  notice.append(text, actions); actions.firstElementChild.focus();
}
