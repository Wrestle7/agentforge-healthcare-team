// Presentation only. Never infer medical risk, recalculate scores, or run tools.
import { textElement as el } from './render.js?v=20261005-boot2';
import { renderVerification, checkLabel, reportedCheckState } from './verification.js?v=20261005-boot2';
import { tools } from './catalog.js?v=20261005-boot2';

const isRecord = value => value !== null && typeof value === 'object' && !Array.isArray(value);
export const jsonText = value => { try { return JSON.stringify(value, null, 2) ?? '未提供'; } catch { return '未提供'; } };
const validScore = value => typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 1;
const validLatency = value => typeof value === 'number' && Number.isFinite(value) && value >= 0;
const knownFlagChecks = new Set(['drug_safety', 'allergy_safety', 'phi_detection', 'dosage_check']);

function section(title, className = '') {
  const node = el('section', `detail-section ${className}`); node.append(el('h3', '', title)); return node;
}
function disclosure(label, key, className = '') {
  const node = el('details', className); node.dataset.detailKey = key;
  const summary = el('summary', '', label); summary.dataset.detailFocus = key;
  node.append(summary); return node;
}
export function responseState(message) {
  if (message.history) return { label: '历史回答', kind: 'history', note: message.meta ? '已恢复当轮保存的回答详情。此状态不代表所有工具执行成功。' : '历史回答已加载，当轮执行详情未提供。' };
  if (message.phase === 'error') return { label: '本轮中断', kind: 'error', note: '未收到任务完成确认。已保留收到的内容，后端任务状态可能未知；不会自动重新提交。' };
  if (message.phase === 'done') return { label: '回答已返回', kind: 'done', note: '本轮回答已返回。此状态不代表所有工具执行成功。' };
  if (message.text) return { label: '正在生成', kind: 'running', note: '正在接收回答；最终校验详情尚未返回。' };
  return { label: '正在处理', kind: 'running', note: '请求处理中，等待回答。仅展示接口实际返回的事件。' };
}

export function renderResponseDetails(box, message, { question = '', ordinal = 1 } = {}) {
  box.replaceChildren();
  const meta = isRecord(message.meta) ? message.meta : null;
  const report = isRecord(meta?.verification) ? meta.verification : null;
  const hasReport = report && Object.keys(report).length > 0;
  const phase = responseState(message);
  const context = el('section', 'detail-context');
  const caption = el('div', 'detail-context-heading');
  caption.append(el('span', '', `第 ${ordinal} 条回答`), el('span', `response-state is-${phase.kind}`, phase.label));
  const prompt = el('p', 'detail-question', question || '未提供关联问题'); prompt.title = question;
  context.append(caption, prompt, el('p', 'detail-muted', phase.note)); box.append(context);

  const attention = section('阅读提示', 'response-attention');
  attention.append(el('p', 'detail-muted', '以下仅整理接口返回的提示与状态，不额外生成医疗判断。'));
  const alerts = el('div', 'response-notices'); let count = 0;
  if (Array.isArray(meta?.disclaimers)) meta.disclaimers.forEach(value => {
    if (typeof value !== 'string' || !value.trim()) return;
    alerts.append(el('p', 'detail-disclaimer', value)); count += 1;
  });
  if (report) Object.entries(report).forEach(([key, value]) => {
    const failed = reportedCheckState(key, value).kind === 'flagged';
    const flags = knownFlagChecks.has(key) && Array.isArray(value?.flags) ? value.flags.length : 0;
    if (!failed && !flags) return;
    const item = el('button', 'reported-notice'); item.type = 'button';
    item.dataset.checkTarget = key;
    item.append(el('strong', '', checkLabel(key)), el('span', '', failed ? '接口标记未通过，请展开核对原始结果。' : `接口返回 ${flags} 条提示，请展开核对。`));
    item.addEventListener('click', () => {
      const target = [...box.querySelectorAll('.verification-check')].find(node => node.dataset.check === key);
      if (target) { target.open = true; target.querySelector('summary').focus({ preventScroll: true }); target.scrollIntoView({ block: 'nearest' }); }
    });
    alerts.append(item); count += 1;
  });
  if (!count) alerts.append(el('p', 'detail-muted', message.phase === 'running' ? '最终提示尚未返回。' : '接口未返回额外注意事项；这不代表已确认没有风险。'));
  attention.append(alerts);
  const missing = [];
  if (message.history && !meta) missing.push('这条旧消息未保存当轮详情，无法还原原始评分、工具调用与校验结果。');
  else if (message.phase !== 'running') {
    if (!hasReport) missing.push('本轮校验结果未提供。');
    if (!validScore(meta?.confidence)) missing.push('系统校验评分未提供有效值。');
    if (!validLatency(meta?.latency_ms)) missing.push('后端报告耗时未提供有效值。');
    if (message.history && !Array.isArray(meta?.tool_calls)) missing.push('历史工具调用记录未提供。');
  }
  if (missing.length) {
    const note = el('div', 'detail-note detail-missing'); note.append(el('strong', '', '详情数据缺失'));
    const list = el('ul'); missing.forEach(text => list.append(el('li', '', text))); note.append(list); attention.append(note);
  }
  box.append(attention);

  const checks = section('校验结果', 'response-checks');
  checks.append(el('p', 'detail-muted', '状态按接口原值展示，不代表完成全部验证或具备临床安全保证。展开查看字段。'));
  renderVerification(checks, meta?.verification); box.append(checks);

  const technical = disclosure('详细信息', 'technical', 'detail-technical');
  technical.querySelector('summary').append(el('small', '', '评分说明、工具参数与后端耗时'));
  const body = el('div', 'detail-technical-body');
  const score = section('系统校验评分');
  score.append(el('div', 'detail-stat', validScore(meta?.confidence) ? `${Math.round(meta.confidence * 100)}%` : '未提供'));
  score.append(el('p', 'detail-muted', '规则校验的参考评分，不是医疗准确率、临床安全保证或诊断置信度。')); body.append(score);
  const elapsed = section('本轮概况'); const time = el('div', 'detail-label');
  time.append(el('span', '', '后端报告耗时'), el('strong', '', validLatency(meta?.latency_ms) ? `${(meta.latency_ms / 1000).toFixed(1)} 秒` : '未提供'));
  elapsed.append(time, el('p', 'detail-muted', '以上耗时来自后端，不是每个工具的执行耗时。')); body.append(elapsed);
  const calls = section('工具调用');
  if (!message.calls.length) calls.append(el('p', 'detail-muted', message.history && !Array.isArray(meta?.tool_calls) ? '未提供历史工具调用记录。' : '未报告工具调用。'));
  message.calls.forEach((call, index) => {
    const name = call.tool || call.name || 'unknown';
    const item = disclosure(`${index + 1}. ${Object.hasOwn(tools, name) ? tools[name] : name}`, `tool:${index}`, 'detail-tool');
    item.append(el('p', 'detail-muted', name), el('p', 'detail-muted', message.phase === 'done' ? '本轮已结束，未提供单工具结果状态' : '未提供单工具结果状态'));
    item.append(el('p', 'detail-muted', call.args == null ? '调用参数未提供。以下为工具事件原始数据。' : '以下为工具事件原始数据，包含调用参数。'));
    item.append(el('pre', 'detail-value', jsonText(call))); calls.append(item);
  }); body.append(calls);
  const raw = disclosure('完整返回数据（JSON）', 'raw', 'detail-raw');
  raw.append(el('p', 'detail-muted', message.history ? '此处为该回答已保存的详情；历史数据可能少于实时返回内容。' : '保留本轮最终接口数据，未修改原始字段或数值。'));
  raw.append(el('pre', 'detail-value', meta ? jsonText(meta) : '未提供最终详情数据。')); body.append(raw);
  technical.append(body); box.append(technical);
  box.append(el('p', 'detail-note', '内容可能先于最终校验显示。AI 辅助参考，请核对原始数据，并由专业人员确认。'));
}
