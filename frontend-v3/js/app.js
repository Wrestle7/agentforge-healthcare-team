import { request, streamMessage } from './api.js?v=20261005-boot2';
import { icon, hydrateIcons } from './icons.js?v=20261005-boot2';
import { renderMarkdown, textElement as el, contentText } from './render.js?v=20261005-boot2';
import { tools, toolTemplates, suggestions } from './catalog.js?v=20261005-boot2';
import { renderResponseDetails, responseState } from './response-details.js?v=20261005-boot2';
import { createWorkspace } from './workspace.js?v=20261005-boot2';
import { createPatients } from './patients.js?v=20261005-boot2';

const $ = id => document.getElementById(id);
const workspacePath = location.pathname;
const state = { id: null, title: '', messages: [], conversations: [], busy: false, selected: null, deleting: null, renaming: null, view: 'assistant', chat: false, historyLoading: false, historyError: '' };
const storageKey = 'af_v3_conversation_id';
let renderTimer = null, focusBeforeDetails = null, renderedDetailsMessage = null;
let workspace = null, patients = null, historySequence = 0;
const viewLabels = { assistant: '医疗助手', patients: '患者工作台', history: '会话管理', capabilities: '能力中心' };
const patientSectionFromHash = () => /^#patients-(overview|medications|labs|history)$/.exec(location.hash)?.[1] || 'overview';
const viewFromHash = () => /^#patients(?:-|$)/.test(location.hash) ? 'patients' : location.hash === '#history' ? 'history' : location.hash === '#capabilities' ? 'capabilities' : 'assistant';
const hashForView = view => view === 'assistant' ? '#message-input' : `#${view}`;
const safeStore = {
  get() { try { return sessionStorage.getItem(storageKey); } catch { return null; } },
  set(id) { try { id ? sessionStorage.setItem(storageKey, id) : sessionStorage.removeItem(storageKey); } catch { /* storage may be disabled */ } },
};
const toolName = call => call.tool || call.name || 'unknown';
const templatePlaceholders = new Set(Object.values(toolTemplates).flatMap(item => item.prompt.match(/【[^【】\r\n]+】/g) || []));
const firstPlaceholder = text => [...text.matchAll(/【[^【】\r\n]+】/g)].find(match => templatePlaceholders.has(match[0]));

function announce(text) { $('live-status').textContent = text; }
function notice(text = '') { $('notice').textContent = text; $('notice').hidden = !text; if (text) announce(text); }
function button(label, symbol, handler, className = 'action') {
  const node = el('button', className); node.type = 'button';
  node.innerHTML = icon(symbol); node.append(el('span', '', label));
  node.addEventListener('click', handler); return node;
}
function refreshInput() {
  const input = $('message-input');
  if (!input.value.trim()) templateWarning();
  $('character-count').textContent = `${input.value.length.toLocaleString()} / 10,000`;
  $('send-button').disabled = state.busy || !input.value.trim();
}
function setBusy(value) {
  state.busy = value;
  document.querySelectorAll('[data-new-chat], [data-view], #refresh-history, .history-select, .history-delete, .history-rename, .suggestion, .capability-row, .manager-action').forEach(node => { node.disabled = value; });
  $('refresh-history').disabled = value || state.historyLoading;
  $('refresh-conversations').disabled = value || state.historyLoading;
  document.querySelectorAll('[data-navigation]').forEach(node => { node.setAttribute('aria-disabled', String(value)); });
  document.querySelectorAll('.capability-busy').forEach(node => { node.hidden = !value; });
  $('message-input').readOnly = value;
  // The HTML starts disabled to prevent native fragment focus before handlers
  // exist. Once initialized, keep normal busy conversations read-only, not
  // disabled, so sending a question does not unnecessarily discard input focus.
  if (!value) $('message-input').disabled = false;
  $('composer').setAttribute('aria-busy', String(value));
  patients?.setBusy(value);
  refreshInput();
}
function templateWarning(text = '') {
  $('template-warning').textContent = text; $('template-warning').hidden = !text;
}
function fillTemplate(template) {
  if (state.busy) return;
  if ($('info-dialog').open) $('info-dialog').close();
  closeSidebar(); closeDetails(); notice();
  changeView('assistant', { focus: false });
  const input = $('message-input'); input.value = template.prompt;
  refreshInput(); templateWarning(template.warning); input.focus();
  const placeholder = firstPlaceholder(template.prompt);
  if (placeholder) input.setSelectionRange(placeholder.index, placeholder.index + placeholder[0].length);
  announce(`已填入问题模板，请补全信息后发送。${template.warning || ''}`);
}
function setView(chat) {
  state.chat = chat;
  renderViews();
  (chat ? $('chat-composer-slot') : $('home-composer-slot')).append($('composer'));
  $('message-input').rows = chat ? 2 : 4;
  $('conversation-title').textContent = state.title || '新会话';
  $('conversation-title').title = state.title;
}
function renderViews() {
  $('welcome').hidden = state.view !== 'assistant' || state.chat;
  $('chat-view').hidden = state.view !== 'assistant' || !state.chat;
  $('history-view').hidden = state.view !== 'history';
  $('capabilities-view').hidden = state.view !== 'capabilities';
  $('patients-view').hidden = state.view !== 'patients';
  $('view-title').textContent = viewLabels[state.view];
  document.title = `${viewLabels[state.view]} · Medical Agent`;
  document.querySelectorAll('.main-nav [data-view]').forEach(node => {
    const active = node.dataset.view === state.view;
    node.classList.toggle('active', active);
    if (active) node.setAttribute('aria-current', 'page'); else node.removeAttribute('aria-current');
  });
}
function changeView(view, { focus = true, record = true, allowBusy = false } = {}) {
  if (!(view in viewLabels)) return;
  if (state.busy && !allowBusy) { announce('请等待当前任务结束后再切换界面。'); return; }
  closeSidebar(); closeDetails(); state.view = view; renderViews();
  if (record && location.hash !== hashForView(view)) history.pushState(null, '', `${location.pathname}${location.search}${hashForView(view)}`);
  if (view === 'patients') patients?.enter(patientSectionFromHash());
  if (focus) (view === 'assistant' ? $('message-input') : $(`${view === 'patients' ? 'patients' : view === 'history' ? 'history' : 'capabilities'}-title`))?.focus();
}
function atBottom() { const area = $('message-scroll'); return area.scrollHeight - area.scrollTop - area.clientHeight < 100; }
function scrollBottom() { const area = $('message-scroll'); area.scrollTop = area.scrollHeight; }
function renderText(message) {
  if (!message.contentNode) return;
  const follow = atBottom();
  renderMarkdown(message.contentNode, message.text);
  if (follow) scrollBottom();
}
function scheduleText(message) {
  if (renderTimer !== null) return;
  renderTimer = setTimeout(() => { renderTimer = null; renderText(message); }, 80);
}
function flushText(message) { clearTimeout(renderTimer); renderTimer = null; renderText(message); }

function showToolTrace(message) {
  const wasOpen = message.toolsNode.querySelector('details')?.open || false;
  const focused = message.toolsNode.contains(document.activeElement);
  message.toolsNode.replaceChildren();
  if (!message.calls.length) return;
  const trace = el('details', 'tool-trace'); trace.open = wasOpen;
  const summary = el('summary'); summary.innerHTML = icon('layers');
  summary.append(el('span', '', `执行过程 · ${message.calls.length} 次工具调用`)); trace.append(summary);
  const list = el('div', 'tool-items');
  message.calls.forEach(call => {
    const name = toolName(call), row = el('div', 'tool-item');
    row.append(el('strong', '', Object.hasOwn(tools, name) ? tools[name] : name));
    row.append(el('small', '', message.phase === 'done' ? '本轮已结束，未提供单工具结果状态' : message.phase === 'error' ? '本轮未确认完成，工具结果状态未知' : '已发起调用，等待本轮结束'));
    if (call.args == null) row.append(el('small', '', '调用参数未提供'));
    list.append(row);
  });
  list.append(el('p', 'trace-caption', '内部名称与调用参数可在回答详情中查看。'));
  trace.append(list); message.toolsNode.append(trace);
  if (focused) summary.focus({ preventScroll: true });
}

function showResponseState(message) {
  const phase = responseState(message);
  message.phaseNode.className = `response-state is-${phase.kind}`;
  message.phaseNode.textContent = phase.label;
  message.statusNode.hidden = message.phase !== 'running' || Boolean(message.text);
}

function appendMessage(message) {
  const article = el('article', `message ${message.role}`);
  if (message.role === 'user') {
    article.setAttribute('aria-label', '用户消息'); article.append(el('div', 'user-content', message.text));
  } else {
    article.setAttribute('aria-label', '助手回答');
    const label = el('div', 'assistant-label');
    label.innerHTML = '<img src="/assets/mark-landing.svg" alt=""><span>Medical Agent</span><span class="assistant-reference">辅助参考</span>';
    message.phaseNode = el('span'); label.append(message.phaseNode);
    message.toolsNode = el('div', 'message-process'); message.contentNode = el('div', 'markdown');
    message.statusNode = el('p', 'status-text', message.phase === 'running' ? '正在处理问题…' : '');
    message.statusNode.hidden = message.phase !== 'running';
    message.errorNode = el('div', 'message-error'); message.errorNode.hidden = true; message.errorNode.setAttribute('role', 'alert');
    message.actionsNode = el('div', 'message-actions');
    article.append(label, message.statusNode, message.contentNode, message.errorNode, message.toolsNode, message.actionsNode);
    renderText(message); showToolTrace(message); showResponseState(message);
    if (message.phase !== 'running') showActions(message);
  }
  $('messages').append(article); message.element = article;
}
function showActions(message) {
  message.actionsNode.replaceChildren();
  const copy = button('复制', 'copy', async () => {
    try { await navigator.clipboard.writeText(message.text); announce('回答已复制'); copy.lastChild.textContent = '已复制'; }
    catch { notice('复制失败，请选中回答手动复制。'); }
  });
  copy.disabled = !message.text;
  const details = button('查看详情', 'panel-right', () => openDetails(message, details));
  details.setAttribute('aria-controls', 'details-panel');
  details.setAttribute('aria-expanded', String(state.selected === message));
  message.detailsButton = details;
  message.actionsNode.append(copy, details);
  if (message.phase === 'error') return;
  const votes = ['up', 'down'].map((rating, index) => {
    const vote = button(index ? '无帮助' : '有帮助', rating, async () => {
      if (!message.conversationId) return;
      votes.forEach(node => { node.disabled = true; });
      try {
        const result = await request('/feedback', { method: 'POST', body: JSON.stringify({ conversation_id: message.conversationId, rating }) });
        if (result.status === 'error') throw new Error('反馈提交失败，请稍后再试。');
        vote.setAttribute('aria-pressed', 'true'); announce('感谢反馈');
      } catch (error) { notice(error.message); votes.forEach(node => { node.disabled = false; }); }
    });
    vote.setAttribute('aria-pressed', 'false'); return vote;
  });
  message.actionsNode.append(...votes);
}

function openDetails(message, trigger) {
  if (state.selected?.detailsButton) state.selected.detailsButton.setAttribute('aria-expanded', 'false');
  state.selected = message; focusBeforeDetails = trigger;
  message.detailsButton?.setAttribute('aria-expanded', 'true');
  renderDetails(); $('details-panel').hidden = false; $('app-shell').classList.add('details-open');
  syncOverlays(); $('close-details').focus();
}
function closeDetails() {
  state.selected?.detailsButton?.setAttribute('aria-expanded', 'false');
  $('details-panel').hidden = true; $('app-shell').classList.remove('details-open'); state.selected = null;
  renderedDetailsMessage = null;
  syncOverlays(); if (focusBeforeDetails?.isConnected) focusBeforeDetails.focus(); focusBeforeDetails = null;
}
function renderDetails() {
  const message = state.selected; if (!message) return;
  const box = $('details-content'), sameMessage = renderedDetailsMessage === message;
  const expanded = sameMessage ? new Set([...box.querySelectorAll('details[open][data-detail-key]')].map(node => node.dataset.detailKey)) : new Set();
  const focusKey = sameMessage && box.contains(document.activeElement) ? document.activeElement.dataset.detailFocus : null;
  const scroll = sameMessage ? box.scrollTop : 0;
  const index = state.messages.indexOf(message);
  const previous = state.messages.slice(0, index);
  const question = [...previous].reverse().find(item => item.role === 'user')?.text || '';
  const ordinal = previous.filter(item => item.role === 'assistant').length + 1;
  renderResponseDetails(box, message, { question, ordinal });
  box.querySelectorAll('details[data-detail-key]').forEach(node => { node.open = expanded.has(node.dataset.detailKey); });
  if (focusKey) [...box.querySelectorAll('[data-detail-focus]')].find(node => node.dataset.detailFocus === focusKey)?.focus({ preventScroll: true });
  box.scrollTop = scroll; renderedDetailsMessage = message;
}

function renderHistory() {
  workspace?.renderConversations();
  const box = $('history-list'); box.replaceChildren();
  if (state.historyLoading && !state.conversations.length) { box.append(el('p', 'sidebar-empty', '正在加载会话…')); return; }
  if (state.historyError) box.append(el('p', 'sidebar-empty', `${state.historyError}${state.conversations.length ? ' 下方为上次加载的记录。' : ''}`));
  const search = $('history-search').value.trim().toLowerCase();
  const filtered = state.conversations.filter(item => (item.title || '未命名会话').toLowerCase().includes(search));
  if (!filtered.length) { if (!state.historyError) box.append(el('p', 'sidebar-empty', search ? '没有匹配的最近会话' : '还没有会话，从一个问题开始。')); return; }
  filtered.slice(0, 8).forEach(item => {
    const row = el('div', `history-item${state.id === item.id ? ' selected' : ''}`);
    const select = el('button', 'history-select', item.title || '未命名会话'); select.title = item.title || '未命名会话';
    select.disabled = state.busy; select.addEventListener('click', () => loadConversation(item.id));
    if (state.id === item.id) select.setAttribute('aria-current', 'true');
    const rename = el('button', 'icon-button history-rename'); rename.innerHTML = icon('edit');
    rename.setAttribute('aria-label', `重命名会话：${item.title || '未命名会话'}`); rename.title = '重命名'; rename.disabled = state.busy;
    rename.addEventListener('click', () => openRename(item));
    const remove = el('button', 'icon-button history-delete'); remove.innerHTML = icon('trash');
    remove.setAttribute('aria-label', `删除会话：${item.title || '未命名会话'}`); remove.disabled = state.busy;
    remove.addEventListener('click', () => openDelete(item));
    row.append(select, rename, remove); box.append(row);
  });
}
function openDelete(item) {
  if (state.busy) return;
  state.deleting = item.id; $('delete-error').textContent = '';
  $('delete-dialog').showModal(); $('cancel-delete').focus();
}
function updateRenameInput() {
  const value = $('rename-input').value;
  $('rename-count').textContent = `${value.length} / 100`;
  $('save-rename').disabled = state.busy || !value.trim() || value.length > 100;
}
function openRename(item) {
  if (state.busy) return;
  state.renaming = item.id;
  $('rename-input').value = item.title || '';
  $('rename-error').textContent = ''; updateRenameInput();
  $('rename-dialog').showModal(); $('rename-input').focus(); $('rename-input').select();
}
async function saveRename(event) {
  event.preventDefault();
  if (state.busy || !state.renaming) return;
  const title = $('rename-input').value.trim();
  if (!title || title.length > 100 || /[\x00-\x1f\x7f]/.test(title)) {
    $('rename-error').textContent = '请输入 1～100 个字符的单行名称。'; return;
  }
  const id = state.renaming;
  setBusy(true); $('save-rename').disabled = true; $('cancel-rename').disabled = true; $('rename-input').readOnly = true;
  $('rename-error').textContent = '';
  try {
    const result = await request(`/conversations/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify({ title }) });
    if (result.id !== id || typeof result.title !== 'string') throw new Error('重命名未获确认，请刷新会话列表后核对。');
    // A GET started before this mutation must not roll the new title back.
    historySequence += 1; state.historyLoading = false; state.historyError = '';
    state.conversations = state.conversations.map(item => item.id === id ? { ...item, ...result } : item)
      .sort((a, b) => b.updated_at - a.updated_at);
    if (state.id === id) {
      state.title = result.title; $('conversation-title').textContent = result.title; $('conversation-title').title = result.title;
    }
    $('rename-dialog').close(); state.renaming = null; setBusy(false); renderHistory();
    const manager = state.view === 'history';
    const replacement = [...$(manager ? 'conversation-list' : 'history-list').querySelectorAll(manager ? '.conversation-rename' : '.history-rename')].find(node => node.getAttribute('aria-label') === `重命名会话：${result.title}`);
    (replacement || $(manager ? 'conversation-search' : 'history-search')).focus();
    announce('会话名称已保存');
  } catch (error) {
    $('rename-error').textContent = error.status === 422 ? '名称格式不正确，请使用 1～100 个字符的单行名称。' : error.message;
  } finally {
    setBusy(false); $('cancel-rename').disabled = false; $('rename-input').readOnly = false; updateRenameInput();
  }
}
async function loadHistory() {
  const sequence = ++historySequence;
  state.historyLoading = true; state.historyError = ''; renderHistory();
  $('refresh-history').disabled = true;
  try {
    const result = await request('/conversations');
    if (!Array.isArray(result)) throw new Error('会话列表格式异常。');
    if (sequence === historySequence) state.conversations = result;
  } catch (error) { if (sequence === historySequence) state.historyError = error.message; }
  finally {
    if (sequence === historySequence) { state.historyLoading = false; renderHistory(); $('refresh-history').disabled = state.busy; }
  }
}
async function loadConversation(id, { restore = false } = {}) {
  if (state.busy) return;
  setBusy(true); notice(); closeSidebar();
  try {
    const data = await request(`/conversations/${encodeURIComponent(id)}`);
    if (!Array.isArray(data.messages)) throw new Error('会话内容格式异常。');
    closeDetails(); state.id = data.id; state.title = data.title || '未命名会话'; safeStore.set(state.id);
    state.messages = []; $('messages').replaceChildren();
    for (const entry of data.messages) {
      const saved = entry.metadata;
      const meta = entry.role === 'assistant' && saved && typeof saved === 'object' && !Array.isArray(saved) && Object.keys(saved).length ? saved : null;
      const calls = Array.isArray(meta?.tool_calls) ? meta.tool_calls.filter(call => call && typeof call === 'object') : [];
      const message = { role: entry.role === 'user' ? 'user' : 'assistant', text: contentText(entry.content), history: true, phase: 'done', calls, meta, conversationId: state.id };
      state.messages.push(message); appendMessage(message);
    }
    if (!restore) changeView('assistant', { focus: false, allowBusy: true });
    setView(true); $('message-input').value = ''; scrollBottom(); renderHistory();
    $('latest-details').disabled = !state.messages.some(m => m.role === 'assistant');
  } catch (error) {
    notice(error.message);
    if (error.status === 404 && safeStore.get() === id) safeStore.set(null);
  } finally { setBusy(false); if (!restore && state.view === 'assistant') $('message-input').focus(); }
}
function newConversation({ stayInView = false, allowBusy = false } = {}) {
  if (state.busy && !allowBusy) return;
  closeDetails(); closeSidebar(); state.id = null; state.title = ''; state.messages = [];
  safeStore.set(null); $('messages').replaceChildren(); $('message-input').value = ''; notice();
  if (!stayInView) changeView('assistant', { focus: false, allowBusy });
  $('latest-details').disabled = true; setView(false); renderHistory(); refreshInput();
  if (state.view === 'assistant') $('message-input').focus();
}
async function send(event) {
  event.preventDefault(); if (state.busy) return;
  const input = $('message-input'), text = input.value.trim(); if (!text) return;
  const placeholder = firstPlaceholder(input.value);
  if (placeholder) {
    notice(`请先将问题模板中的${placeholder[0]}替换为实际信息，再发送。`);
    input.focus(); input.setSelectionRange(placeholder.index, placeholder.index + placeholder[0].length); return;
  }
  notice(); closeSidebar();
  if (!state.title) state.title = text.length > 45 ? `${text.slice(0, 45)}…` : text;
  setView(true); setBusy(true); $('message-input').value = ''; refreshInput();
  const user = { role: 'user', text }; state.messages.push(user); appendMessage(user);
  const assistant = { role: 'assistant', text: '', phase: 'running', history: false, calls: [], meta: null, conversationId: state.id };
  state.messages.push(assistant); appendMessage(assistant); scrollBottom();
  $('latest-details').disabled = false; announce('正在处理问题');
  try {
    await streamMessage(text, state.id, (eventName, data) => {
      const hadText = Boolean(assistant.text);
      if (eventName === 'thinking') {
        if (typeof data.conversation_id === 'string') { state.id = data.conversation_id; assistant.conversationId = state.id; safeStore.set(state.id); }
      } else if (eventName === 'tool_call') {
        assistant.calls.push(data); showToolTrace(assistant);
        assistant.statusNode.textContent = '正在查询和整理信息…'; announce(`正在${tools[toolName(data)] || '调用业务工具'}`);
      } else if (eventName === 'token') {
        if (typeof data.text === 'string') { assistant.text += data.text; assistant.statusNode.hidden = true; scheduleText(assistant); }
      } else if (eventName === 'done') {
        assistant.text = data.response; assistant.meta = data; assistant.phase = 'done';
        if (Array.isArray(data.tool_calls)) assistant.calls = data.tool_calls.filter(call => call && typeof call === 'object');
        if (typeof data.conversation_id === 'string') { state.id = data.conversation_id; assistant.conversationId = state.id; safeStore.set(state.id); }
        assistant.statusNode.hidden = true; flushText(assistant); showToolTrace(assistant); announce('回答已返回，请核对原始记录');
      }
      showResponseState(assistant);
      // Token updates only change the answer, not disclosure/focus/scroll state.
      if (state.selected === assistant && (eventName !== 'token' || (!hadText && assistant.text))) renderDetails();
    });
  } catch (error) {
    assistant.phase = 'error'; assistant.statusNode.hidden = true;
    assistant.errorNode.textContent = error.message; assistant.errorNode.hidden = false;
    flushText(assistant); showToolTrace(assistant); announce(error.message);
  } finally {
    setBusy(false); showResponseState(assistant); showActions(assistant); if (state.selected === assistant) renderDetails();
    await loadHistory();
  }
}

function openSidebar() { closeDetails(); $('app-shell').classList.add('sidebar-open'); $('menu-button').setAttribute('aria-expanded', 'true'); syncOverlays(); $('sidebar').querySelector('button:not(:disabled)').focus(); }
function closeSidebar() { const open = $('app-shell').classList.contains('sidebar-open'); $('app-shell').classList.remove('sidebar-open'); $('menu-button').setAttribute('aria-expanded', 'false'); syncOverlays(); if (open) $('menu-button').focus(); }
function syncOverlays() {
  const side = innerWidth < 768 && $('app-shell').classList.contains('sidebar-open');
  const detail = innerWidth < 1280 && !$('details-panel').hidden;
  $('sidebar-scrim').hidden = !side; $('details-scrim').hidden = !detail;
  $('main').inert = side || detail; $('sidebar').inert = detail;
  $('details-panel').setAttribute('role', detail ? 'dialog' : 'complementary');
  if (detail) $('details-panel').setAttribute('aria-modal', 'true'); else $('details-panel').removeAttribute('aria-modal');
}
function showHelp() {
  $('dialog-content').replaceChildren();
    $('dialog-title').textContent = '使用说明';
    [
      '医疗助手用于提问；会话管理用于搜索、排序、重命名和删除已加载会话；能力中心提供分类与搜索入口。切换栏目会保留当前会话与未发送草稿，只有新建会话才会清空。',
      '患者工作台先按姓名或 ID 查找并选择患者，再查看基础资料、用药过敏、检验和病史。此页面只读现有 FHIR 数据，不调用模型或写入工具；普通医疗助手仍可能执行带写入标记的任务。',
      '医疗助手的快捷任务和能力中心中的 14 个工具均可填入问题模板。替换所有【占位符】后再发送；这不会强制指定工具，实际调用仍由 Agent 判断。',
      '写入标记及输入框提示用于提醒业务副作用，不是后端权限或审批机制。请核对患者与操作，尤其是记录生命体征、预防筛查和保险查询。',
      'Enter 发送，Shift + Enter 换行。中文输入法选词时不会发送。任务执行期间，请等待本轮结束后再发送或切换会话。',
      '工具调用卡片显示已发起的调用，不代表执行成功。结果详情只展示本轮接口实际返回的数据。',
      '历史会话与经典版使用同一套后端数据；删除会话会同时影响两套界面。新版不将患者内容和回答保存到浏览器持久存储。',
      '接口可达仅表示基础健康检查通过，不代表模型、EHR、授权和全部工具均正常。',
      '本系统仍处于开发验证阶段，没有企业用户隔离和临床合规保证。AI 回答仅作辅助参考，必须核对原始记录。',
    ].forEach(text => $('dialog-content').append(el('p', '', text)));
  $('info-dialog').showModal(); $('close-dialog').focus();
}
async function health() {
  const node = $('connection');
  try {
    const result = await request('/health'); const ok = result.status === 'ok';
    node.className = `connection ${ok ? 'online' : 'offline'}`;
    node.lastElementChild.textContent = ok ? '接口可达' : '接口异常';
    node.title = '仅检查后端基础健康状态，不代表模型、FHIR 或所有工具正常。';
  } catch { node.className = 'connection offline'; node.lastElementChild.textContent = '接口不可达'; node.title = '未能完成基础健康检查，请检查服务和网络。'; }
}

hydrateIcons();
workspace = createWorkspace({ getState: () => state, openConversation: loadConversation, renameConversation: openRename, deleteConversation: openDelete, chooseTemplate: fillTemplate, reloadHistory: () => { if (!state.busy) loadHistory(); } });
patients = createPatients({ getBusy: () => state.busy, announce });
state.view = viewFromHash();
suggestions.forEach(item => {
  const card = el('button', 'suggestion'); card.type = 'button';
  const symbol = el('span', 'card-icon'); symbol.innerHTML = icon(item.icon);
  const title = el('div', 'card-title'); title.append(el('h3', '', item.title)); const arrow = el('span'); arrow.innerHTML = icon('arrow-up-right'); title.append(arrow);
  card.append(symbol, title, el('p', '', item.description));
  card.addEventListener('click', () => fillTemplate(item)); $('suggestions').append(card);
});
setView(false);
$('composer').addEventListener('submit', send);
$('message-input').addEventListener('input', refreshInput);
let composing = false;
$('message-input').addEventListener('compositionstart', () => { composing = true; });
$('message-input').addEventListener('compositionend', () => { composing = false; });
$('message-input').addEventListener('keydown', event => {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing && !composing && event.keyCode !== 229) { event.preventDefault(); $('composer').requestSubmit(); }
});
document.querySelectorAll('[data-new-chat]').forEach(node => node.addEventListener('click', newConversation));
document.querySelectorAll('[data-navigation]').forEach(node => node.addEventListener('click', event => { if (state.busy) { event.preventDefault(); announce('请等待当前任务结束后再切换界面。'); } }));
document.querySelectorAll('[data-view]').forEach(node => node.addEventListener('click', () => changeView(node.dataset.view)));
$('refresh-history').addEventListener('click', () => { if (!state.busy) loadHistory(); });
$('history-search').addEventListener('input', renderHistory);
$('help-button').addEventListener('click', showHelp);
$('close-dialog').addEventListener('click', () => $('info-dialog').close());
$('menu-button').addEventListener('click', openSidebar);
$('sidebar-scrim').addEventListener('click', closeSidebar);
$('details-scrim').addEventListener('click', closeDetails);
$('close-details').addEventListener('click', closeDetails);
$('latest-details').addEventListener('click', () => { const message = [...state.messages].reverse().find(m => m.role === 'assistant'); if (message) openDetails(message, $('latest-details')); });
$('cancel-delete').addEventListener('click', () => $('delete-dialog').close());
$('rename-form').addEventListener('submit', saveRename);
$('rename-input').addEventListener('input', () => { $('rename-error').textContent = ''; updateRenameInput(); });
$('rename-input').addEventListener('keydown', event => { if (event.key === 'Enter' && (event.isComposing || event.keyCode === 229)) event.preventDefault(); });
$('cancel-rename').addEventListener('click', () => $('rename-dialog').close());
$('rename-dialog').addEventListener('cancel', event => { if (state.busy) event.preventDefault(); });
$('delete-dialog').addEventListener('cancel', event => { if (state.busy) event.preventDefault(); });
$('confirm-delete').addEventListener('click', async () => {
  if (state.busy || !state.deleting) return;
  setBusy(true); $('confirm-delete').disabled = true; $('cancel-delete').disabled = true;
  try {
    const result = await request(`/conversations/${encodeURIComponent(state.deleting)}`, { method: 'DELETE' });
    if (result.status !== 'ok') throw new Error('删除未获确认，请刷新会话列表后核对。');
    $('delete-dialog').close();
    state.conversations = state.conversations.filter(item => item.id !== state.deleting);
    if (state.id === state.deleting) newConversation({ stayInView: state.view !== 'assistant', allowBusy: true });
    state.deleting = null;
    await loadHistory(); announce('会话已删除');
    if (state.view === 'history') $('conversation-search').focus();
  } catch (error) { $('delete-error').textContent = error.message; }
  finally { setBusy(false); $('confirm-delete').disabled = false; $('cancel-delete').disabled = false; }
});
document.addEventListener('keydown', event => {
  if (document.querySelector('dialog[open]')) return;
  const detailOverlay = innerWidth < 1280 && !$('details-panel').hidden;
  const sideOverlay = innerWidth < 768 && $('app-shell').classList.contains('sidebar-open');
  if (event.key === 'Escape') { if (!$('details-panel').hidden) closeDetails(); else closeSidebar(); }
  if (event.key === 'Tab' && (detailOverlay || sideOverlay)) {
    const panel = detailOverlay ? $('details-panel') : $('sidebar');
    const focusable = [...panel.querySelectorAll('button:not(:disabled), a[href], input, summary, [tabindex="0"]')].filter(node => node.getClientRects().length);
    const first = focusable[0], last = focusable.at(-1);
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
  }
});
window.addEventListener('resize', syncOverlays);
window.addEventListener('popstate', () => {
  // A submitted landing question mounts this shell in the same document.
  // Cross-page history must reload; while busy, restore the workspace URL without
  // invoking a reload whose beforeunload cancellation could strand it at '/'.
  if (location.pathname === workspacePath) return;
  if (state.busy) {
    history.pushState(null, '', `${workspacePath}${hashForView(state.view)}`);
    announce('请等待当前任务结束后再返回首页。');
  } else location.reload();
});
window.addEventListener('hashchange', () => {
  if (location.pathname !== workspacePath) return;
  if (location.hash === '#main-content') return;
  if (state.busy) {
    history.replaceState(null, '', `${location.pathname}${location.search}${hashForView(state.view)}`);
    announce('请等待当前任务结束后再切换界面。'); return;
  }
  changeView(viewFromHash(), { record: false });
});
window.addEventListener('beforeunload', event => { if (state.busy) { event.preventDefault(); event.returnValue = ''; } });
const healthInterval = setInterval(() => { if (!document.hidden) health(); }, 30000);
window.addEventListener('pagehide', () => clearInterval(healthInterval), { once: true });
let started = false;
export async function start({ initialMessage = null } = {}) {
  if (started) return;
  started = true;
  setBusy(true);
  const id = safeStore.get();
  const initialReads = Promise.all([health(), loadHistory()]);
  if (typeof initialMessage === 'string' && initialMessage.trim()) {
    // Explicit landing submit always starts a fresh context. No persisted draft,
    // auto replay on reload, or fallback request after stream failure.
    setBusy(false); newConversation();
    $('message-input').value = initialMessage.slice(0, 10000); initialMessage = null;
    refreshInput();
    await send({ preventDefault() {} });
  } else {
    await initialReads; setBusy(false);
    if (id) await loadConversation(id, { restore: true });
  }
  if (state.view === 'patients') patients.enter(patientSectionFromHash());
  if (location.hash === '#message-input') $('message-input').focus();
}
