// App shell: login gate, navigation, live events.
import { ApiError, login, logout, openEvents, restoreSession, session } from './api.js';
import { clear, el, toast } from './dom.js';
import { mountAgents } from './views/agents.js';
import { mountApprovals } from './views/approvals.js';
import { mountMemory } from './views/memory.js';
import { mountOverview } from './views/voice.js';
import { mountSystem } from './views/system.js';

const root = document.getElementById('root');
const VIEWS = [
  ['overview', 'Overview', 'Voice console and live status', mountOverview],
  ['agents', 'Agents', 'Roster, voices and activity', mountAgents],
  ['memory', 'Memory', 'What JARVIS remembers', mountMemory],
  ['approvals', 'Approvals', 'Actions waiting for you', mountApprovals],
  ['system', 'System', 'Health, audit and security', mountSystem],
];

let current = null, stopEvents = null, badges = {}, navButtons = {};

function showLogin(message = '') {
  stopEvents?.(); current?.destroy?.(); current = null;
  const username = el('input', { type: 'text', autocomplete: 'username', required: true, maxLength: 64, 'aria-label': 'Username' });
  const password = el('input', { type: 'password', autocomplete: 'current-password', required: true, maxLength: 128, 'aria-label': 'Password' });
  const error = el('div', { class: 'error', role: 'alert', text: message });
  const submit = el('button', { class: 'btn primary', type: 'submit' }, 'Sign in');
  const form = el('form', { class: 'card login stack', onSubmit: async (event) => {
    event.preventDefault(); submit.disabled = true; error.textContent = '';
    try { await login(username.value.trim(), password.value); password.value = ''; showApp(); }
    catch (e) {
      error.textContent = e instanceof ApiError && e.status === 429
        ? `Too many attempts. Try again in ${Math.ceil((Number(e.retryAfter) || 60) / 60)} min.` : e.message;
      submit.disabled = false; password.value = ''; password.focus();
    }
  } },
    el('div', { class: 'brand' }, el('div', { class: 'orb' }), el('h1', { text: 'JARVIS' })),
    el('p', { class: 'muted', style: 'text-align:center;margin:0', text: 'Sign in to continue' }),
    el('label', { class: 'field' }, 'Username', username), el('label', { class: 'field' }, 'Password', password), error, submit,
    el('p', { class: 'faint', style: 'text-align:center;margin:0', text: 'No account yet? Run: python -m jarvis_backend create-admin' }));
  clear(root, el('div', { class: 'login-wrap' }, form));
  username.focus();
}

function setBadge(name, count) {
  badges[name] = count;
  const node = navButtons[name]?.querySelector('.badge-count');
  if (node) { node.textContent = String(count); node.classList.toggle('hidden', !count); }
}

function navigate(id) {
  const [, label, sub, mount] = VIEWS.find((v) => v[0] === id) || VIEWS[0];
  current?.destroy?.();
  Object.entries(navButtons).forEach(([key, node]) => (key === id ? node.setAttribute('aria-current', 'page') : node.removeAttribute('aria-current')));
  const body = el('div');
  clear(content, el('div', { class: 'topbar' }, el('div', {}, el('h2', { text: label }), el('div', { class: 'page-sub', text: sub }))), body);
  current = mount(body, { setBadge, refreshBadges: () => {} }) || null;
  if (location.hash.slice(1) !== id) history.replaceState(null, '', `#${id}`);
}

let content;
function showApp() {
  navButtons = {};
  content = el('main', { class: 'main', id: 'content' });
  const live = el('span', { class: 'dot', title: 'Live updates' });
  const nav = el('nav', { class: 'nav', 'aria-label': 'Main' }, VIEWS.map(([id, label]) => {
    const button = el('button', { type: 'button', onClick: () => navigate(id) }, label, el('span', { class: 'badge-count hidden', text: '0' }));
    navButtons[id] = button; return button;
  }));
  clear(root, el('div', { class: 'shell' },
    el('aside', { class: 'side' }, el('div', { class: 'brand' }, el('div', { class: 'orb' }), el('h1', { text: 'JARVIS' })), nav,
      el('div', { class: 'foot' }, el('div', { class: 'row' }, live, el('span', { text: 'Live' })),
        el('button', { class: 'btn small', type: 'button', style: 'margin-top:10px', onClick: () => logout() }, 'Sign out'))), content));
  stopEvents = openEvents((event) => current?.onEvent?.(event), (up) => live.classList.toggle('ok', up));
  const wanted = location.hash.slice(1);
  navigate(VIEWS.some((v) => v[0] === wanted) ? wanted : 'overview');
}

session.onEnd(() => showLogin('Your session has ended. Please sign in again.'));
window.addEventListener('hashchange', () => { if (session.active && content) { const id = location.hash.slice(1); if (VIEWS.some((v) => v[0] === id)) navigate(id); } });

(async () => {
  clear(root, el('div', { class: 'login-wrap' }, el('div', { class: 'muted', text: 'Loading…' })));
  (await restoreSession()) ? showApp() : showLogin();
})();
