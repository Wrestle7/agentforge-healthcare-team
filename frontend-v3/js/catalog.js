export const tools = {
  patient_summary: '查询患者摘要', drug_interaction_check: '检查用药相互作用',
  symptom_lookup: '查询症状信息', provider_search: '查找医务人员',
  appointment_availability: '查询可预约时间', fda_drug_safety: '查询 FDA 药品安全信息',
  drug_recall_check: '查询药品召回', clinical_trials_search: '查询临床试验',
  allergy_check: '核对用药与过敏记录', record_vitals: '记录生命体征',
  care_gap_analysis: '分析预防筛查缺口', update_care_gap: '更新预防筛查记录',
  insurance_coverage_check: '查询保险覆盖信息', lab_results_analysis: '分析检验结果',
};
// Shortcuts prepare a question; the Agent still chooses which tools to call.
// These warnings describe tool side effects, not a backend permission boundary.
export const toolTemplates = {
  patient_summary: { prompt: '请查询患者【姓名或 ID】的病史、用药与过敏记录，并整理摘要。' },
  drug_interaction_check: { prompt: '请查询患者【姓名或 ID】当前登记的用药，检查潜在相互作用，并说明检查范围。' },
  symptom_lookup: { prompt: '请查询以下症状的相关参考信息：【症状描述】。请说明信息局限，不要给出确诊结论。' },
  provider_search: { prompt: '请查找【医生姓名或专科】对应的医务人员，列出系统中已有的专业与联系信息。' },
  appointment_availability: { prompt: '请查询【医生姓名】在【日期 YYYY-MM-DD】的可预约时间，仅查询，不创建预约。' },
  fda_drug_safety: {
    prompt: '请查询【药品通用名】的 FDA 安全信息，包括警告、禁忌和不良反应；仅查询，不将报告写入病历。',
    writeLabel: '可选写入', warning: '该工具支持保存报告。本模板仅请求查询、不写入病历；如需保存，请先核对患者和操作。',
  },
  drug_recall_check: { prompt: '请查询【药品通用名】的 FDA 召回记录，并说明查询结果的适用范围。' },
  clinical_trials_search: { prompt: '请查询与【疾病或研究主题】相关、正在招募的临床试验，并提供可核对的登记信息。' },
  allergy_check: { prompt: '请核对患者【姓名或 ID】当前登记的用药与过敏记录，列出潜在冲突及检查依据。' },
  record_vitals: {
    prompt: '请为患者【姓名或 ID】记录实测血压：收缩压【收缩压数值】mmHg，舒张压【舒张压数值】mmHg。仅记录我提供的数据，不补充其他指标。',
    writeLabel: '写入记录', warning: '发送后可能写入 OpenEMR 病历。请先核对患者、实测数值和单位，不要使用示例数据。',
  },
  care_gap_analysis: {
    prompt: '请分析患者【姓名或 ID】的预防筛查记录，列出可能缺失或待核实的项目。',
    writeLabel: '可能写入', warning: '该分析工具可能创建筛查缺口记录或更新逾期状态，不是只读查询。请确认后再发送。',
  },
  update_care_gap: {
    prompt: '请更新患者【姓名或 ID】的【筛查项目名称】记录，操作为【操作】。操作请选择 completed（已完成）、declined（已拒绝）或 reset（重置）中的一项。',
    writeLabel: '写入记录', warning: '该工具会修改预防筛查记录。请核对患者、项目和目标状态；仅在确认需要修改时发送。',
  },
  insurance_coverage_check: {
    prompt: '请查询患者【姓名或 ID】当前登记用药的保险覆盖信息，并说明数据来源和缺失信息。',
    writeLabel: '可能写入', warning: '该工具可能保存保险覆盖查询记录，不是完全只读。请确认后再发送。',
  },
  lab_results_analysis: { prompt: '请查询患者【姓名或 ID】的检验结果，列出异常指标及已有的变化趋势。' },
};
export const suggestions = [
  { title: '患者摘要', icon: 'clipboard', description: '汇总病史、用药与过敏记录，让关键信息一目了然。', ...toolTemplates.patient_summary },
  { title: '用药检查', icon: 'pill', description: '基于已有记录，检查药物之间的潜在相互作用。', ...toolTemplates.drug_interaction_check },
  { title: '检验分析', icon: 'chart', description: '查看检验记录、参考范围与已有的变化趋势。', ...toolTemplates.lab_results_analysis },
  { title: '预防筛查', icon: 'calendar', description: '整理适用的预防筛查与记录缺口，供人工核实。', ...toolTemplates.care_gap_analysis },
];
export const verificationLabels = {
  allergy_safety: '过敏风险检查', confidence_scoring: '评分构成',
  claim_verification: '事实依据核对', phi_detection: '敏感信息检查',
  dosage_check: '剂量检查', overall_safe: '综合规则状态',
  drug_safety: '药物安全校验', hallucination: '事实依据校验', hallucination_check: '事实依据校验',
  confidence: '置信度评估', confidence_score: '系统评分', scope: '回答范围',
  scope_check: '回答范围', phi: '敏感信息检查', phi_check: '敏感信息检查',
  output_validation: '输出检查', claim_grounding: '依据匹配',
};
