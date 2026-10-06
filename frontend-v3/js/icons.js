const paths = {
  plus: '<path d="M12 5v14M5 12h14"/>',
  history: '<path d="M3 11a9 9 0 1 1 2.6 7M3 4v7h7M12 7v5l3 2"/>',
  patient: '<circle cx="12" cy="8" r="4"/><path d="M4 21v-2a8 8 0 0 1 16 0v2"/>',
  chat: '<path d="M21 11.5a8.5 8.5 0 0 1-8.5 8.5H4l-2 2V11.5a9.5 9.5 0 0 1 19 0Z"/><path d="M7 9h9M7 13h6"/>',
  grid: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
  refresh: '<path d="M20 7v5h-5M4 17v-5h5"/><path d="M5.5 7a7.5 7.5 0 0 1 12.4-2L20 8M4 16l2.1 3A7.5 7.5 0 0 0 18.5 17"/>',
  help: '<circle cx="12" cy="12" r="9"/><path d="M9.5 9a2.5 2.5 0 1 1 4 2c-1 .6-1.5 1-1.5 2M12 17h.01"/>',
  panels: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/>',
  'panel-right': '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M15 4v16"/>',
  'arrow-up-right': '<path d="M7 17 17 7M7 7h10v10"/>',
  'arrow-up': '<path d="M12 19V5m-6 6 6-6 6 6"/>',
  layers: '<path d="m12 3 10 5-10 5L2 8l10-5Zm-9 9 9 5 9-5M3 16l9 5 9-5"/>',
  menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
  close: '<path d="m6 6 12 12M6 18 18 6"/>',
  spark: '<path d="m12 3 2.6 6.4L21 12l-6.4 2.6L12 21l-2.6-6.4L3 12l6.4-2.6L12 3Z"/>',
  shield: '<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3Z"/><path d="m8.5 12 2.5 2.5 4.5-5"/>',
  lock: '<rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3M12 14v3"/>',
  clipboard: '<rect x="5" y="5" width="14" height="16" rx="2"/><rect x="9" y="3" width="6" height="4" rx="1"/><path d="M9 12h6M9 16h4"/>',
  pill: '<path d="m9 4-5 5a6.4 6.4 0 0 0 9 9l5-5a6.4 6.4 0 0 0-9-9Z"/><path d="m7 7 10 10"/>',
  chart: '<path d="M4 3v17h17M8 15l4-6 4 3 5-7"/>',
  calendar: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M7 3v4M17 3v4M3 11h18m-13 5 3 3 5-5"/>',
  chevron: '<path d="m9 5 7 7-7 7"/>',
  copy: '<rect x="8" y="8" width="12" height="13" rx="2"/><path d="M16 8V3H3v13h5"/>',
  up: '<path d="M7 10v11H3V10h4Zm0 0 5-7c2 0 2 2 1 6h6a2 2 0 0 1 2 2l-2 8a2 2 0 0 1-2 2H7"/>',
  down: '<path d="M7 14V3H3v11h4Zm0 0 5 7c2 0 2-2 1-6h6a2 2 0 0 0 2-2l-2-8a2 2 0 0 0-2-2H7"/>',
  trash: '<path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7"/>',
  edit: '<path d="m15 5 4 4M4 20l4-1 12-12a2.8 2.8 0 0 0-4-4L4 15l-1 6 5-2"/>',
  alert: '<path d="m12 3 10 18H2L12 3Zm0 6v5M12 18h.01"/>',
};
export function icon(name) {
  return `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || paths.grid}</svg>`;
}
export function hydrateIcons(root = document) {
  root.querySelectorAll('[data-icon]').forEach(el => { el.innerHTML = icon(el.dataset.icon); });
}
