// Same-origin transport. Never retry chat automatically: tools may have side effects.
export class ApiError extends Error {
  constructor(message, status = 0) { super(message); this.name = 'ApiError'; this.status = status; }
}
function statusMessage(status) {
  if (status === 401 || status === 403) return '接口访问未获授权，请检查 API Key 配置。';
  if (status === 429) return '请求过于频繁，请稍后再试。';
  if (status === 404) return '记录不存在或已删除，请刷新会话列表。';
  return `接口暂时不可用（HTTP ${status}），请稍后再试。`;
}
function headers() {
  const value = document.querySelector('meta[name="api-key"]')?.content?.trim();
  return { 'Content-Type': 'application/json', ...(value ? { 'X-API-Key': value } : {}) };
}
export async function request(path, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 15000);
  try {
    const response = await fetch(`/api${path}`, { ...options, headers: headers(), signal: controller.signal, cache: 'no-store' });
    if (!response.ok) throw new ApiError(statusMessage(response.status), response.status);
    return await response.json();
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new ApiError(error.name === 'AbortError' ? '接口请求超时，请稍后再试。' : '无法连接接口，请检查服务和网络。');
  } finally { clearTimeout(timer); }
}

// SSE is delimited by blank lines, not TCP chunks. Support CRLF, multi-line data,
// final unterminated event, comments, and UTF-8 characters split across reads.
export async function consumeSSE(stream, onEvent, onActivity = () => {}) {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = '', data = [], terminal = false;
  function dispatch() {
    if (!data.length) return;
    let packet;
    try { packet = JSON.parse(data.join('\n')); } catch { throw new ApiError('响应格式异常，已停止接收；不会自动重新提交任务。'); }
    data = [];
    if (!packet || typeof packet.event !== 'string' || !packet.data || typeof packet.data !== 'object') {
      throw new ApiError('响应事件不完整，请检查后端接口。');
    }
    if (packet.event === 'error') throw new ApiError('任务执行失败。已保留收到的内容，请核对后再决定是否重新提交。');
    if (packet.event === 'done' && typeof packet.data.response !== 'string') throw new ApiError('任务结束事件缺少回答内容。');
    onEvent(packet.event, packet.data);
    if (packet.event === 'done') terminal = true;
  }
  function line(value) {
    if (value === '') { dispatch(); return; }
    if (value.startsWith(':')) return;
    const colon = value.indexOf(':');
    const field = colon < 0 ? value : value.slice(0, colon);
    let content = colon < 0 ? '' : value.slice(colon + 1);
    if (content.startsWith(' ')) content = content.slice(1);
    if (field === 'data') data.push(content);
  }
  function drain(final = false) {
    while (!terminal) {
      const match = /[\r\n]/.exec(buffer);
      if (!match) break;
      const at = match.index;
      if (!final && buffer[at] === '\r' && at === buffer.length - 1) break;
      const width = buffer[at] === '\r' && buffer[at + 1] === '\n' ? 2 : 1;
      const value = buffer.slice(0, at);
      buffer = buffer.slice(at + width);
      line(value);
    }
    if (final && !terminal) {
      if (buffer) { line(buffer); buffer = ''; }
      dispatch();
    }
  }
  try {
    while (!terminal) {
      const { value, done } = await reader.read();
      if (done) { buffer += decoder.decode(); drain(true); break; }
      onActivity();
      buffer += decoder.decode(value, { stream: true });
      drain();
    }
    if (!terminal) throw new ApiError('连接已中断，未收到任务完成确认。已保留内容，不会自动重新提交。');
  } finally {
    try { await reader.cancel(); } catch { /* stream may already be closed */ }
    reader.releaseLock();
  }
}

export async function streamMessage(message, conversationId, onEvent) {
  const controller = new AbortController();
  let idleTimer;
  const activity = () => { clearTimeout(idleTimer); idleTimer = setTimeout(() => controller.abort(), 120000); };
  activity();
  try {
    const response = await fetch('/api/chat/stream', {
      method: 'POST', headers: headers(), signal: controller.signal,
      body: JSON.stringify({ message, conversation_id: conversationId }),
    });
    if (!response.ok) throw new ApiError(statusMessage(response.status), response.status);
    if (!response.body || !response.headers.get('content-type')?.includes('text/event-stream')) throw new ApiError('接口没有返回预期的流式响应。');
    await consumeSSE(response.body, onEvent, activity);
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new ApiError(error.name === 'AbortError'
      ? '长时间未收到响应，已停止接收。后端任务可能仍在执行，请先核对结果，不要直接重复提交。'
      : '网络连接中断，已保留收到的内容。后端任务状态未知，不会自动重新提交。');
  } finally { clearTimeout(idleTimer); }
}
