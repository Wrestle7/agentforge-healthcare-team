import { textElement as el } from './render.js?v=20261005-boot2';
import { verificationLabels } from './catalog.js?v=20261005-boot2';

// Translate display labels only. Keep every returned value and the original
// ordering; do not summarize, truncate, re-score, or invent missing fields.
const fieldLabels = {
  passed: '是否通过', flags: '提示列表', score: '评分', factors: '评分因素',
  tools_used: '工具使用情况', data_richness: '数据丰富度',
  response_hedging: '不确定性措辞', tool_error_rate: '工具结果情况',
  grounded_claims: '有依据的陈述数', ungrounded_claims: '未匹配到依据的陈述数',
  total_claims: '陈述总数', grounding_rate: '依据匹配比例', details: '详细信息',
  claim: '陈述内容', grounded: '是否匹配到依据', source_tool: '来源工具',
  drugs: '药物列表', drug: '药物', allergy: '过敏信息', issue: '问题说明',
  severity: '风险级别', pattern: '匹配类型', description: '描述', match: '匹配内容',
  mentioned_mg: '回答中提及的剂量（mg）', max_mg: '规则中的最大剂量（mg）',
  note: '备注', source: '来源',
};
const isRecord = value => value !== null && typeof value === 'object' && !Array.isArray(value);
export const checkLabel = key => Object.hasOwn(verificationLabels, key) ? verificationLabels[key] : key;
export function reportedCheckState(key, value) {
  const reported = key === 'overall_safe' && typeof value === 'boolean' ? value : isRecord(value) ? value.passed : null;
  if (reported === false) return { label: '接口报告未通过', kind: 'flagged' };
  if (reported === true) return { label: '接口报告通过', kind: 'reported' };
  return { label: key === 'confidence_scoring' ? '评分详情' : '未提供通过状态', kind: 'unknown' };
}

function readable(value, depth = 0) {
  if (value === null || typeof value !== 'object') return el('span', 'check-value', value === null ? 'null（原值）' : String(value));
  if (depth >= 3) return el('pre', 'check-nested-value', translatedFields(value));
  if (Array.isArray(value)) {
    if (!value.length) return el('span', 'check-value', '空列表（原值 []）');
    const list = el('ol', 'check-values'); value.forEach(item => { const row = el('li'); row.append(readable(item, depth + 1)); list.append(row); }); return list;
  }
  const entries = Object.entries(value);
  if (!entries.length) return el('span', 'check-value', '空对象（原值 {}）');
  const list = el('dl', 'check-fields');
  entries.forEach(([key, item]) => {
    const row = el('div', 'check-field'), field = el('dd'); field.append(readable(item, depth + 1));
    row.append(el('dt', '', Object.hasOwn(fieldLabels, key) ? fieldLabels[key] : key), field); list.append(row);
  }); return list;
}
function translatedFields(value) {
  // Replace JSON property labels, not string contents. Unlike rebuilding an
  // object with translated keys, this cannot drop values on a label collision.
  return JSON.stringify(value, null, 2).replace(/^(\s*)("(?:\\.|[^"\\])*"):/gm, (line, indent, quotedKey) => {
    const key = JSON.parse(quotedKey);
    return Object.hasOwn(fieldLabels, key) ? `${indent}${JSON.stringify(fieldLabels[key])}:` : line;
  });
}

// Display only: do not re-score, infer missing checks, or gate the response here.
export function renderVerification(container, verification) {
  const report = el('div', 'verification-report'); container.append(report);
  if (!isRecord(verification) || !Object.keys(verification).length) {
    report.append(el('p', 'detail-muted', '未提供校验结果。')); return;
  }
  Object.entries(verification).forEach(([key, value]) => {
    const item = el('details', 'verification-check'); item.dataset.check = key;
    item.dataset.detailKey = `check:${key}`;
    const status = reportedCheckState(key, value);
    const summary = el('summary'); summary.dataset.detailFocus = `check:${key}`;
    summary.append(el('span', 'check-name', checkLabel(key)), el('span', `check-state is-${status.kind}`, status.label));
    const body = el('div', 'check-body'); body.append(readable(value));
    const raw = el('details', 'check-raw'); raw.dataset.detailKey = `check-raw:${key}`;
    const rawTitle = el('summary', '', '完整字段（中文标签）'); rawTitle.dataset.detailFocus = `check-raw:${key}`;
    raw.append(rawTitle, el('pre', 'detail-value', translatedFields(value))); body.append(raw);
    item.append(summary, body); report.append(item);
  });
}
