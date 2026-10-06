import { showStartupFailure } from './startup.js?v=20261005-boot2';
document.querySelectorAll('[data-view], [data-new-chat]').forEach(node => { node.disabled = true; });
let started = false;
try {
  const { start } = await import('./app.js?v=20261005-boot2');
  started = true;
  await start();
} catch {
  showStartupFailure({ started });
}
