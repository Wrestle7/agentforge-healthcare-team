// Workspace-only views. Uses existing data and callbacks; never calls a model or
// executes a tool. Patient data and search terms stay in memory, not storage.
import { icon } from './icons.js?v=20261005-boot2';
import { textElement as el } from './render.js?v=20261005-boot2';
import { tools, toolTemplates } from './catalog.js?v=20261005-boot2';

const $ = id => document.getElementById(id);
const categories = [
  ['all', '全部能力'], ['patient', '患者资料'], ['medication', '用药安全'],
  ['care', '筛查与记录'], ['services', '就医与保障'],
];
const presentation = {
  patient_summary: ['patient', 'clipboard', '整理已有病史、用药和过敏记录，形成患者摘要。'],
  symptom_lookup: ['patient', 'search', '查询症状相关参考信息，了解信息范围与局限。'],
  lab_results_analysis: ['patient', 'chart', '查看已有检验结果、参考范围及指标变化趋势。'],
  drug_interaction_check: ['medication', 'pill', '基于当前登记用药，检查潜在药物相互作用。'],
  allergy_check: ['medication', 'shield', '核对用药与已有过敏记录，提示潜在冲突。'],
  fda_drug_safety: ['medication', 'shield', '查询 FDA 药品警告、禁忌及不良反应等参考信息。'],
  drug_recall_check: ['medication', 'search', '查询药品召回信息，并核对结果的适用范围。'],
  record_vitals: ['care', 'chart', '将你提供的实测生命体征记录到指定患者病历。'],
  care_gap_analysis: ['care', 'calendar', '分析预防筛查记录，整理缺失或待核实的项目。'],
  update_care_gap: ['care', 'clipboard', '更新指定患者的筛查状态，操作前需要核对。'],
  provider_search: ['services', 'search', '按姓名或专科查找已有医务人员信息。'],
  appointment_availability: ['services', 'calendar', '查询医生的可预约时间，不创建预约。'],
  clinical_trials_search: ['services', 'layers', '查找相关临床试验，获取可核对的登记信息。'],
  insurance_coverage_check: ['services', 'clipboard', '查询登记用药的保险覆盖情况及缺失信息。'],
};

function action(label, symbol, handler, className) {
  const node = el('button', className); node.type = 'button';
  node.innerHTML = icon(symbol); node.append(el('span', '', label));
  node.addEventListener('click', handler); return node;
}
function emptyState(title, description, symbol = 'search') {
  const box = el('div', 'workspace-empty');
  const mark = el('span', 'empty-symbol'); mark.innerHTML = icon(symbol);
  box.append(mark, el('h2', '', title), el('p', '', description)); return box;
}
function timestamp(value) {
  if (typeof value === 'number' && Number.isFinite(value) && value > 0) return value * 1000;
  return null;
}
function dateLabel(value) {
  const ms = timestamp(value);
  if (ms === null || Number.isNaN(new Date(ms).getTime())) return '时间未提供';
  return new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' }).format(ms);
}

export function createWorkspace({ getState, openConversation, renameConversation, deleteConversation, chooseTemplate, reloadHistory }) {
  let category = 'all';

  function renderConversations() {
    const state = getState(), box = $('conversation-list'); box.replaceChildren();
    const query = $('conversation-search').value.trim().toLocaleLowerCase();
    const order = $('conversation-sort').value;
    const filtered = state.conversations.filter(item => (item.title || '未命名会话').toLocaleLowerCase().includes(query));
    filtered.sort((a, b) => order === 'title'
      ? (a.title || '未命名会话').localeCompare(b.title || '未命名会话', 'zh-CN')
      : order === 'oldest' ? (timestamp(a.updated_at) ?? Infinity) - (timestamp(b.updated_at) ?? Infinity)
        : (timestamp(b.updated_at) ?? 0) - (timestamp(a.updated_at) ?? 0));
    box.setAttribute('aria-busy', String(state.historyLoading));
    $('refresh-conversations').disabled = state.busy || state.historyLoading;
    $('conversation-count').textContent = state.historyLoading ? '正在读取会话…' : query ? `找到 ${filtered.length} 条 · 已加载 ${state.conversations.length} 条` : `已加载 ${state.conversations.length} 条会话`;
    if (state.historyError) {
      const warning = el('div', 'list-error'); warning.setAttribute('role', 'alert');
      warning.append(el('span', '', `${state.historyError}${state.conversations.length ? ' 当前显示上次加载的记录。' : ''}`));
      const retry = action('重新加载', 'refresh', reloadHistory, 'button button-quiet button-small manager-action'); retry.disabled = state.busy || state.historyLoading;
      warning.append(retry); box.append(warning);
    }
    if (!filtered.length) {
      if (state.historyLoading) {
        const loading = el('div', 'list-loading', '正在读取会话记录，请稍候…'); loading.setAttribute('role', 'status'); box.append(loading);
      } else if (!state.historyError) {
        const empty = emptyState(query ? '没有找到匹配的会话' : '让第一次讨论成为工作记录', query ? '试试更短的关键词，或清除搜索查看已加载的会话。' : '在医疗助手中发送问题后，会话会出现在这里。');
        if (query) empty.append(action('清除搜索', 'close', () => { $('conversation-search').value = ''; renderConversations(); $('conversation-search').focus(); }, 'button button-quiet button-small'));
        box.append(empty);
      }
      return;
    }
    const tableHead = el('div', 'conversation-table-head'); tableHead.setAttribute('aria-hidden', 'true');
    tableHead.append(el('span', '', '会话名称'), el('span', '', '最近更新'), el('span', '', '操作')); box.append(tableHead);
    filtered.forEach(item => {
      const title = item.title || '未命名会话';
      const row = el('article', `conversation-row${state.id === item.id ? ' is-current' : ''}`);
      const open = action(title, 'chat', () => openConversation(item.id), 'conversation-open manager-action');
      open.disabled = state.busy; open.title = title; open.setAttribute('aria-label', `打开会话：${title}`);
      if (state.id === item.id) open.append(el('small', 'current-label', '当前会话'));
      const time = el('time', 'conversation-time', dateLabel(item.updated_at));
      const ms = timestamp(item.updated_at); if (ms !== null && !Number.isNaN(new Date(ms).getTime())) time.dateTime = new Date(ms).toISOString();
      const actions = el('div', 'conversation-row-actions');
      const rename = action('重命名', 'edit', () => renameConversation(item), 'conversation-rename action manager-action');
      const remove = action('删除', 'trash', () => deleteConversation(item), 'conversation-delete action manager-action');
      rename.setAttribute('aria-label', `重命名会话：${title}`); remove.setAttribute('aria-label', `删除会话：${title}`);
      rename.disabled = state.busy; remove.disabled = state.busy; actions.append(rename, remove);
      row.append(open, time, actions); box.append(row);
    });
  }

  function renderCapabilities() {
    const state = getState(), query = $('capability-search').value.trim().toLocaleLowerCase();
    const box = $('capability-list'); box.replaceChildren();
    const entries = Object.entries(tools).filter(([name, label]) => {
      const [group, , description] = presentation[name] || ['patient', 'grid', '选择后补全问题信息，再手动发送。'];
      return (category === 'all' || category === group) && `${name} ${label} ${description}`.toLocaleLowerCase().includes(query);
    });
    $('tool-count').textContent = String(Object.keys(tools).length);
    $('capability-count').textContent = `显示 ${entries.length} / ${Object.keys(tools).length} 项能力`;
    $('clear-capability-filters').hidden = category === 'all' && !query;
    $('capability-filters').querySelectorAll('button').forEach(node => { node.setAttribute('aria-pressed', String(node.dataset.category === category)); });
    if (!entries.length) box.append(emptyState('没有找到匹配的能力', '尝试其他关键词，或清除当前分类与搜索条件。'));
    entries.forEach(([name, label]) => {
      const template = toolTemplates[name];
      const [group, symbol, description] = presentation[name] || ['patient', 'grid', '选择后补全问题信息，再手动发送。'];
      const card = el('button', 'capability-row capability-card'); card.type = 'button'; card.dataset.tool = name;
      card.disabled = state.busy; card.setAttribute('aria-label', `填入问题：${label}${template.writeLabel ? `（${template.writeLabel}）` : ''}`);
      const top = el('span', 'capability-card-top');
      const mark = el('span', 'capability-symbol'); mark.innerHTML = icon(symbol);
      top.append(mark, el('span', 'capability-category', categories.find(([key]) => key === group)?.[1] || '其他'));
      const heading = el('span', 'capability-heading', label);
      if (template.writeLabel) heading.append(el('span', 'capability-badge', template.writeLabel));
      const footer = el('span', 'capability-card-footer');
      const arrow = el('span', 'capability-action', '填入问题'); arrow.innerHTML += icon('arrow-up-right');
      footer.append(el('code', '', name), arrow);
      card.append(top, heading, el('span', 'capability-description', description), footer);
      card.addEventListener('click', () => chooseTemplate(template)); box.append(card);
    });
  }

  categories.forEach(([value, label]) => {
    const filter = el('button', 'filter-tab', label); filter.type = 'button'; filter.dataset.category = value;
    filter.addEventListener('click', () => { category = value; renderCapabilities(); }); $('capability-filters').append(filter);
  });
  $('conversation-search').addEventListener('input', renderConversations);
  $('conversation-sort').addEventListener('change', renderConversations);
  $('refresh-conversations').addEventListener('click', reloadHistory);
  $('capability-search').addEventListener('input', renderCapabilities);
  $('clear-capability-filters').addEventListener('click', () => { category = 'all'; $('capability-search').value = ''; renderCapabilities(); $('capability-search').focus(); });
  renderCapabilities();
  return { renderConversations, renderCapabilities };
}
