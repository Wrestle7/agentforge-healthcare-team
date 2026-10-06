// Read-only patient browsing. No model calls, mutations, or persistent PHI storage.
import { request } from './api.js?v=20261005-boot2';
import { icon } from './icons.js?v=20261005-boot2';
import { textElement as el } from './render.js?v=20261005-boot2';

const sections = [
  ['overview', '患者概览'], ['medications', '用药与过敏'], ['labs', '检验记录'], ['history', '病史与就诊'],
];
const partNames = { medications: '登记用药', allergies: '过敏记录', observations: '检验记录', conditions: '病史记录', encounters: '就诊记录' };
const partKeys = { medications: ['medications', 'allergies'], labs: ['observations'], history: ['conditions', 'encounters'] };
const labels = {
  male: '男', female: '女', other: '其他', unknown: '未提供', active: '有效', inactive: '非活动',
  completed: '已完成', stopped: '已停止', cancelled: '已取消', 'entered-in-error': '录入错误',
  draft: '草稿', final: '最终', preliminary: '初步', amended: '已修订', corrected: '已更正',
  confirmed: '已确认', unconfirmed: '未确认', refuted: '已排除', resolved: '已缓解', remission: '缓解期', recurrence: '复发', relapse: '复发',
  low: '低', high: '高', 'unable-to-assess': '无法评估',
  proposal: '建议', plan: '计划', order: '医嘱', 'original-order': '原始医嘱', option: '备选方案',
  allergy: '过敏', intolerance: '不耐受', medication: '药物', food: '食物', environment: '环境', biologic: '生物制品',
  finished: '已结束', planned: '已计划', arrived: '已到达', triaged: '已分诊', 'in-progress': '进行中', onleave: '暂时离院',
};
const text = value => value === null || value === undefined || value === '' ? '未提供' : Array.isArray(value) ? (value.length ? value.map(text).join('、') : '未提供') : typeof value === 'object' ? '未提供' : String(value);
const translated = value => {
  if (Array.isArray(value)) return value.length ? value.map(translated).join('、') : '未提供';
  const key = typeof value === 'string' ? value.trim().toLowerCase() : '';
  return Object.hasOwn(labels, key) ? labels[key] : text(value);
};
const date = value => typeof value === 'string' && value ? value.replace('T', ' ').replace(/Z$/, ' UTC') : '未提供';
const sourceLabel = source => source === 'mock' ? '演示数据 · 非真实病历' : source === 'openemr' ? '来源：OpenEMR' : '来源未提供';

function mark(name) { const node = el('span', 'patient-symbol'); node.innerHTML = icon(name); return node; }
function action(label, fn, className = 'button button-quiet button-small') {
  const node = el('button', className, label); node.type = 'button'; node.dataset.patientAction = '';
  node.addEventListener('click', fn); return node;
}
function field(label, value) {
  const node = el('div', 'patient-field'); node.append(el('dt', '', label), el('dd', '', value)); return node;
}
function failure(error) {
  if (error.status === 404) return '未找到这位患者的资料，请重新核对患者 ID。';
  if (error.status === 422) return '查询条件格式不正确，请核对后重试。';
  return error.message || '资料暂时无法读取，请稍后重试。';
}

export function createPatients({ getBusy = () => false, announce = () => {} } = {}) {
  const root = document.getElementById('patients-view');
  if (!root) throw new Error('Patient workspace container is missing.');
  const state = { entered: false, section: 'overview', query: '', list: [], listStatus: 'idle', listError: '', listSource: null, hasMore: false, selected: null, profile: null, detail: new Map(), locked: false };
  let listSequence = 0, patientSequence = 0;
  const busy = () => state.locked || getBusy();
  root.classList.add('patients-page');

  const heading = el('header', 'page-heading');
  const titleGroup = el('div');
  const title = el('h1', '', '患者工作台'); title.id = 'patients-title'; title.tabIndex = -1;
  titleGroup.append(el('span', 'eyebrow', '患者与记录'), title, el('p', '', '先核对患者身份，再查看已有的医疗记录。'));
  heading.append(titleGroup, el('span', 'page-label', '只读查看'));

  const selector = el('section', 'patient-selector'); selector.id = 'patient-selector'; selector.setAttribute('aria-labelledby', 'patient-select-title');
  const selectHeading = el('div', 'patient-select-heading');
  const selectTitle = el('h2', '', '选择患者'); selectTitle.id = 'patient-select-title';
  selectHeading.append(selectTitle, el('span', '', '姓名相同，请结合 ID 与出生日期核对'));
  const form = el('form', 'patient-search-form');
  const searchLabel = el('label', 'workspace-search'); searchLabel.append(mark('search'));
  const search = el('input'); search.id = 'patient-search'; search.type = 'search'; search.maxLength = 100; search.placeholder = '输入患者姓名或 ID'; search.autocomplete = 'off'; search.setAttribute('aria-label', '搜索患者姓名或 ID');
  searchLabel.append(search);
  const submit = el('button', 'button', '搜索患者'); submit.id = 'patient-search-submit'; submit.type = 'submit'; submit.dataset.patientAction = '';
  form.append(searchLabel, submit);
  const listCaption = el('div', 'patient-list-caption'); listCaption.id = 'patient-list-caption'; listCaption.setAttribute('role', 'status');
  const list = el('div', 'patient-list'); list.id = 'patient-list'; list.setAttribute('aria-label', '患者候选列表');
  selector.append(selectHeading, form, listCaption, list);

  const identity = el('section', 'patient-identity'); identity.id = 'patient-identity'; identity.tabIndex = -1; identity.setAttribute('aria-label', '当前患者身份'); identity.hidden = true;
  const tabs = el('div', 'patient-tabs'); tabs.id = 'patient-tabs'; tabs.setAttribute('role', 'tablist'); tabs.setAttribute('aria-label', '患者资料类别');
  const panel = el('section', 'patient-panel'); panel.id = 'patient-panel'; panel.setAttribute('role', 'tabpanel'); panel.tabIndex = -1;
  sections.forEach(([key, label], index) => {
    const tab = action(label, () => switchSection(key), 'patient-tab');
    tab.id = `patient-tab-${key}`; tab.dataset.patientSection = key; tab.setAttribute('role', 'tab'); tab.setAttribute('aria-controls', 'patient-panel');
    tab.addEventListener('keydown', event => {
      if (busy() || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? sections.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + sections.length) % sections.length;
      switchSection(sections[next][0]); tabs.children[next].focus();
    });
    tabs.append(tab);
  });
  const footnote = el('p', 'page-footnote patient-footnote'); footnote.append(mark('lock'), el('span', '', '仅展示已有记录，不生成医疗结论，也不修改病历。请结合原始记录与专业判断。'));
  root.replaceChildren(heading, identity, tabs, selector, panel, footnote);
  form.addEventListener('submit', event => { event.preventDefault(); if (!busy()) loadList(search.value.trim()); });

  function syncBusy() {
    root.querySelectorAll('[data-patient-action]').forEach(node => { node.disabled = busy(); });
    search.disabled = busy();
  }
  function message(titleText, detailText, { loading = false, retry, kind, compact = false } = {}) {
    const box = el('div', `patient-state${compact ? ' patient-state-compact' : ''}${retry ? ' patient-state-error' : ''}`);
    box.setAttribute('role', retry ? 'alert' : 'status');
    box.append(mark(loading ? 'refresh' : retry ? 'alert' : 'clipboard'), el('h3', '', titleText), el('p', '', detailText));
    if (retry) { const button = action('重新加载', retry); button.dataset.patientRetry = kind; box.append(button); }
    return box;
  }
  function renderList() {
    selector.hidden = !!state.selected;
    list.replaceChildren(); list.setAttribute('aria-busy', String(state.listStatus === 'loading'));
    listCaption.replaceChildren(el('span', '', state.listStatus === 'ok' ? `本次显示 ${state.list.length} 位候选${state.hasMore ? ' · 还有其他结果，请缩小查询范围' : ''}` : ''), el('span', 'patient-source', state.listStatus === 'ok' ? sourceLabel(state.listSource) : ''));
    if (state.listStatus === 'loading') list.append(message('正在读取患者列表', '请稍候，列表加载完成后选择患者。', { loading: true }));
    else if (state.listStatus === 'error') list.append(message('患者列表暂时无法读取', state.listError, { retry: () => loadList(state.query), kind: 'list' }));
    else if (state.listStatus === 'ok' && !state.list.length) list.append(message('没有找到匹配的患者', state.query ? '请核对姓名或 ID，或清空搜索条件再试。' : '当前接口未返回患者记录。'));
    else state.list.forEach(patient => {
      const row = action('', () => choosePatient(patient), 'patient-option'); row.dataset.patientId = patient.id;
      row.setAttribute('aria-label', `选择患者：${text(patient.name)}，ID ${patient.id}`);
      const avatar = el('span', 'patient-avatar', text(patient.name).slice(0, 1));
      const info = el('span', 'patient-option-info'); info.append(el('strong', '', text(patient.name)), el('span', '', `ID ${patient.id}`));
      const demographic = el('span', 'patient-option-demographic'); demographic.append(el('span', '', `出生日期 ${text(patient.birth_date)}`), el('span', '', translated(patient.gender)));
      const arrow = mark('chevron'); arrow.classList.add('patient-option-arrow');
      row.append(avatar, info, demographic, arrow); list.append(row);
    });
  }
  function renderIdentity() {
    identity.hidden = !state.selected; identity.replaceChildren();
    if (!state.selected) return;
    const patient = state.profile?.status === 'ok' ? state.profile.data : state.selected;
    const summary = el('div', 'patient-identity-summary');
    const caption = el('span', 'eyebrow', '当前患者');
    const name = el('h2', '', text(patient.name));
    const meta = el('p', '', `ID ${text(patient.id)}  ·  ${translated(patient.gender)}  ·  出生日期 ${text(patient.birth_date)}`);
    summary.append(caption, name, meta);
    const actions = el('div', 'patient-identity-actions');
    const change = action('更换患者', changePatient); change.id = 'patient-change';
    actions.append(el('span', 'patient-source', sourceLabel(patient.source ?? state.listSource)), change);
    identity.append(summary, actions);
  }
  function renderTabs() {
    [...tabs.children].forEach(tab => { const selected = tab.dataset.patientSection === state.section; tab.setAttribute('aria-selected', String(selected)); tab.tabIndex = selected ? 0 : -1; });
    panel.setAttribute('aria-labelledby', `patient-tab-${state.section}`);
  }
  function renderOverview(patient) {
    const card = el('article', 'patient-overview-card');
    const head = el('div', 'patient-card-heading'); head.append(mark('clipboard'), el('h2', '', '基本资料'));
    const fields = el('dl', 'patient-demographics');
    [['患者姓名', text(patient.name)], ['患者 ID', text(patient.id)], ['出生日期', text(patient.birth_date)], ['性别', translated(patient.gender)], ['联系电话', text(patient.phone)], ['电子邮箱', text(patient.email)], ['联系地址', text(patient.address)]].forEach(([label, value]) => fields.append(field(label, value)));
    card.append(head, fields, el('p', 'patient-record-note', '联系方式及人口学资料来自登记记录，缺失项目显示为“未提供”。'));
    panel.append(card);
    const shortcuts = el('div', 'patient-overview-shortcuts');
    [['medications', 'pill', '用药与过敏', '核对登记用药与过敏记录'], ['labs', 'chart', '检验记录', '查看已有结果与参考范围'], ['history', 'history', '病史与就诊', '浏览病史条目与就诊记录']].forEach(([key, symbol, titleText, description]) => {
      const item = action('', () => switchSection(key), 'patient-shortcut'); item.append(mark(symbol), el('strong', '', titleText), el('span', '', description)); shortcuts.append(item);
    });
    panel.append(shortcuts);
  }
  function recordCard(kind, item) {
    const card = el('article', `patient-record patient-record-${kind}`);
    const titleText = kind === 'medications' ? item.medication : kind === 'allergies' ? item.substance : kind === 'observations' ? item.test_name : kind === 'conditions' ? item.display : item.type;
    card.append(el('h3', '', text(titleText)));
    const fields = el('dl', 'patient-record-fields');
    let values = [];
    if (kind === 'medications') values = [['登记用法', text(item.dosage)], ['状态', translated(item.status)], ['记录日期', date(item.authored_on)], ['药品编码', text(item.medication_code)], ['医嘱意图', translated(item.intent)]];
    else if (kind === 'allergies') values = [['临床状态', translated(item.clinical_status)], ['反应记录', text(item.reactions)], ['严重性分级', translated(item.criticality)], ['类型', translated(item.type)], ['分类', translated(item.category)]];
    else if (kind === 'conditions') values = [['临床状态', translated(item.clinical_status)], ['核实状态', translated(item.verification_status)], ['起始日期', date(item.onset)], ['编码', text(item.code)], ['编码系统', text(item.system)]];
    else if (kind === 'encounters') values = [['状态', translated(item.status)], ['开始时间', date(item.start)], ['结束时间', date(item.end)], ['就诊原因', text(item.reason)]];
    else {
      const result = el('div', 'patient-lab-value'); result.append(el('strong', '', text(item.value)), el('span', '', item.unit ? text(item.unit) : '')); card.append(result);
      values = [['参考范围', text(item.reference_range)], ['记录解释', text(item.interpretation)], ['结果状态', translated(item.status)], ['记录时间', date(item.date)]];
      if (Array.isArray(item.components) && item.components.length) {
        const components = el('div', 'patient-lab-components');
        item.components.forEach(component => { if (!component || typeof component !== 'object') return; const row = el('p'); row.append(el('strong', '', text(component.test_name)), el('span', '', `${text(component.value)}${component.unit ? ` ${text(component.unit)}` : ''}`), el('small', '', `参考范围：${text(component.reference_range)}`)); components.append(row); });
        card.append(components);
      }
    }
    values.forEach(([label, value]) => fields.append(field(label, value)));
    card.append(fields); return card;
  }
  function renderParts(data) {
    partKeys[state.section].forEach(key => {
      const part = data.parts?.[key];
      const block = el('section', 'patient-record-section'); block.dataset.patientPart = key;
      const heading = el('div', 'patient-part-heading'); heading.append(el('h2', '', partNames[key]));
      if (part?.status === 'ok' && Array.isArray(part.items)) heading.append(el('span', '', `本次返回 ${part.items.length} 条${part.has_more ? ' · 还有未展示的记录' : ''}`));
      block.append(heading);
      if (part?.status !== 'ok' || !Array.isArray(part.items)) block.append(message('这部分资料暂时不可用', part?.message ? text(part.message) : '未能读取记录，请稍后重试。', { compact: true, retry: () => loadSection(state.section, true), kind: 'section' }));
      else if (!part.items.length) block.append(message('未返回相关记录', '当前查询没有返回条目，不代表已确认不存在相关病史或风险。', { compact: true }));
      else {
        const records = el('div', 'patient-record-grid');
        part.items.forEach(item => { if (item && typeof item === 'object') records.append(recordCard(key, item)); }); block.append(records);
      }
      panel.append(block);
    });
    if (state.section === 'labs') panel.append(el('p', 'patient-record-note', '结果、单位、参考范围和解释均按原始记录展示；本页不判断异常，也不生成趋势结论。'));
  }
  function renderPanel() {
    panel.replaceChildren(); panel.setAttribute('aria-busy', 'false'); panel.dataset.patientSection = state.section;
    if (!state.selected) { panel.append(message(`选择患者后查看${sections.find(([key]) => key === state.section)[1]}`, '从上方候选中核对并选择一位患者，详情不会自动展开。', { compact: true })); return; }
    if (state.profile?.status === 'loading') { panel.setAttribute('aria-busy', 'true'); panel.append(message('正在读取患者资料', '正在核对所选患者的登记信息。', { loading: true })); return; }
    if (state.profile?.status === 'error') { panel.append(message('患者资料暂时无法读取', state.profile.error, { retry: () => loadProfile(state.selected), kind: 'detail' })); return; }
    if (state.profile?.status !== 'ok') return;
    if (state.section === 'overview') { renderOverview(state.profile.data); return; }
    const detail = state.detail.get(state.section);
    if (!detail || detail.status === 'loading') { panel.setAttribute('aria-busy', 'true'); panel.append(message('正在读取已有记录', '请稍候，记录按当前患者和资料类别加载。', { loading: true })); }
    else if (detail.status === 'error') panel.append(message('记录暂时无法读取', detail.error, { retry: () => loadSection(state.section, true), kind: 'section' }));
    else renderParts(detail.data);
  }
  function render() { renderList(); renderIdentity(); renderTabs(); renderPanel(); syncBusy(); }

  async function loadList(query = '') {
    if (busy()) return;
    const sequence = ++listSequence;
    state.query = query; state.listStatus = 'loading'; state.listError = ''; renderList(); syncBusy();
    try {
      const data = await request(`/patients?q=${encodeURIComponent(query)}&limit=20`);
      if (!data || !Array.isArray(data.items)) throw new Error('患者列表格式异常，请重试。');
      if (sequence !== listSequence) return;
      state.list = data.items.filter(item => item && typeof item === 'object' && typeof item.id === 'string' && item.id).map(item => ({ ...item }));
      state.listStatus = 'ok'; state.listSource = data.source; state.hasMore = data.has_more === true;
      announce(state.list.length ? `已读取 ${state.list.length} 位患者候选，请核对身份后选择。` : '没有找到匹配的患者。');
    } catch (error) { if (sequence === listSequence) { state.listStatus = 'error'; state.list = []; state.listError = failure(error); announce(state.listError); } }
    finally { if (sequence === listSequence) { renderList(); syncBusy(); } }
  }
  async function loadProfile(patient) {
    if (busy()) return;
    const sequence = ++patientSequence, id = patient.id;
    state.profile = { status: 'loading' }; state.detail.clear(); render();
    try {
      const data = await request(`/patients/${encodeURIComponent(id)}`);
      if (sequence !== patientSequence || state.selected?.id !== id) return;
      if (!data || data.id !== id) throw new Error('返回的患者身份与当前选择不一致，请重新选择患者。');
      state.profile = { status: 'ok', data }; render(); announce('患者资料已加载，请核对姓名与 ID。');
      if (state.section !== 'overview') loadSection(state.section);
    } catch (error) { if (sequence === patientSequence && state.selected?.id === id) { state.profile = { status: 'error', error: failure(error) }; render(); announce('患者资料暂时无法读取。'); } }
  }
  async function loadSection(section, force = false) {
    if (busy() || section === 'overview' || !state.selected || state.profile?.status !== 'ok') return;
    const cached = state.detail.get(section);
    if (!force && cached) return;
    const id = state.selected.id, sequence = patientSequence, token = {};
    let settled = false;
    state.detail.set(section, { status: 'loading', token }); renderPanel(); syncBusy();
    try {
      const data = await request(`/patients/${encodeURIComponent(id)}/${section}`);
      if (sequence !== patientSequence || state.selected?.id !== id || state.detail.get(section)?.token !== token) return;
      if (!data || data.patient_id !== id || data.section !== section || !data.parts || typeof data.parts !== 'object') throw new Error('返回记录与当前患者或资料类别不一致，请重试。');
      state.detail.set(section, { status: 'ok', data }); settled = true;
    } catch (error) { if (sequence === patientSequence && state.selected?.id === id && state.detail.get(section)?.token === token) { state.detail.set(section, { status: 'error', error: failure(error) }); settled = true; } }
    finally { if (settled && sequence === patientSequence && state.selected?.id === id && state.section === section) { renderPanel(); syncBusy(); announce('当前资料加载已结束，请核对记录。'); } }
  }
  function choosePatient(patient) {
    if (busy()) return;
    state.selected = patient; state.profile = null; state.detail.clear(); render(); identity.focus(); loadProfile(patient);
  }
  function changePatient() {
    if (busy()) return;
    patientSequence += 1; state.selected = null; state.profile = null; state.detail.clear(); render(); search.focus();
    announce('请选择另一位患者。');
  }
  function switchSection(section) {
    if (busy() || !sections.some(([key]) => key === section)) return;
    const hash = `#patients-${section}`;
    if (location.hash !== hash) history.pushState(null, '', `${location.pathname}${location.search}${hash}`);
    enter(section);
  }
  function enter(section = 'overview') {
    state.section = sections.some(([key]) => key === section) ? section : 'overview';
    renderTabs(); renderPanel(); syncBusy();
    if (!state.entered) { state.entered = true; loadList(''); }
    else if (state.listStatus === 'idle') loadList(state.query);
    if (state.selected && state.profile?.status === 'ok' && state.section !== 'overview') loadSection(state.section);
  }
  render();
  return { enter, setBusy(value) {
    state.locked = value; syncBusy();
    if (!busy() && state.entered && state.listStatus === 'idle') loadList(state.query);
    if (!busy() && state.entered && state.selected && state.profile?.status === 'ok' && state.section !== 'overview') loadSection(state.section);
  } };
}
