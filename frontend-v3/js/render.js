// Dependencies are vendored and pinned; no CDN request is made in the browser.
let parser;
export function renderMarkdown(container, text) {
  if (!window.markdownit || !window.DOMPurify) { container.textContent = text; return; }
  parser ||= window.markdownit({ html: false, linkify: false, breaks: true, typographer: false });
  // Do not load external images: even a harmless-looking image URL can leak data.
  parser.renderer.rules.image = (tokens, index) => `[图片：${tokens[index].content || '未加载'}]`;
  const clean = window.DOMPurify.sanitize(parser.render(text), {
    ALLOWED_TAGS: ['p','br','strong','em','s','del','h1','h2','h3','h4','h5','h6','ul','ol','li','blockquote','pre','code','a','hr','table','thead','tbody','tr','th','td'],
    ALLOWED_ATTR: ['href','title','start','colspan','rowspan'],
    ALLOW_DATA_ATTR: false,
  });
  container.innerHTML = clean;
  container.querySelectorAll('a').forEach(a => {
    try {
      const target = new URL(a.getAttribute('href') || '', location.href);
      if (!['http:', 'https:'].includes(target.protocol)) { a.removeAttribute('href'); return; }
      a.target = '_blank'; a.rel = 'noopener noreferrer'; a.referrerPolicy = 'no-referrer';
    } catch { a.removeAttribute('href'); }
  });
  container.querySelectorAll('table').forEach(table => {
    const wrapper = document.createElement('div'); wrapper.className = 'table-scroll';
    wrapper.tabIndex = 0; wrapper.setAttribute('aria-label', '回答中的表格，可横向滚动');
    table.replaceWith(wrapper); wrapper.append(table);
  });
}
export function textElement(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = String(text);
  return node;
}
export function contentText(content) {
  if (typeof content === 'string') return content;
  if (Array.isArray(content)) return content.map(block => typeof block === 'string' ? block : block?.text || '').join('\n');
  return '';
}
