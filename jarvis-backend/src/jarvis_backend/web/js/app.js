// App shell: login gate, navigation, live events.
import { ApiError, login, logout, mfaLogin, openEvents, restoreSession, session } from './api.js';
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
  const describe = (e) => (e instanceof ApiError && e.status === 429
    ? `Too many attempts. Try again in ${Math.ceil((Number(e.retryAfter) || 60) / 60)} min.` : e.message);
  const form = el('form', { class: 'card login stack', onSubmit: async (event) => {
    event.preventDefault(); submit.disabled = true; error.textContent = '';
    try {
      const result = await login(username.value.trim(), password.value); password.value = '';
      if (result && result.mfa_required) { showMfaStep(result.mfa_token); return; }
      showApp();
    } catch (e) {
      error.textContent = describe(e);
      submit.disabled = false; password.value = ''; password.focus();
    }
  } },
    el('div', { class: 'brand' }, el('div', { class: 'orb' }), el('h1', { text: 'JARVIS' })),
    el('p', { class: 'muted', style: 'text-align:center;margin:0', text: 'Sign in to continue' }),
    el('label', { class: 'field' }, 'Username', username), el('label', { class: 'field' }, 'Password', password), error, submit,
    el('p', { class: 'faint', style: 'text-align:center;margin:0', text: 'No account yet? Run: python -m jarvis_backend create-admin' }));
  clear(root, el('div', { class: 'login-wrap' }, form));
  username.focus();

  // Second step for MFA accounts: a 6-digit code (or a recovery code) redeems the challenge.
  function showMfaStep(mfaToken) {
    const code = el('input', { type: 'text', inputmode: 'numeric', autocomplete: 'one-time-code',
      required: true, maxLength: 14, 'aria-label': 'Authentication code', placeholder: '123456' });
    const mfaError = el('div', { class: 'error', role: 'alert' });
    const verify = el('button', { class: 'btn primary', type: 'submit' }, 'Verify');
    const mfaForm = el('form', { class: 'card login stack', onSubmit: async (event) => {
      event.preventDefault(); verify.disabled = true; mfaError.textContent = '';
      try { await mfaLogin(mfaToken, code.value.trim()); showApp(); }
      catch (e) { mfaError.textContent = describe(e); verify.disabled = false; code.value = ''; code.focus(); }
    } },
      el('div', { class: 'brand' }, el('div', { class: 'orb' }), el('h1', { text: 'JARVIS' })),
      el('p', { class: 'muted', style: 'text-align:center;margin:0', text: 'Enter the code from your authenticator app' }),
      el('label', { class: 'field' }, 'Authentication code', code), mfaError, verify,
      el('p', { class: 'faint', style: 'text-align:center;margin:0', text: 'Lost your device? Enter one of your recovery codes.' }));
    clear(root, el('div', { class: 'login-wrap' }, mfaForm));
    code.focus();
  }
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
  clear(content, el('div', { class: 'topbar' },
    el('div', { class: 'row' }, menuButton, el('div', {}, el('h2', { text: label }), el('div', { class: 'page-sub', text: sub })))), body);
  current = mount(body, { setBadge, refreshBadges: () => {} }) || null;
  if (location.hash.slice(1) !== id) history.replaceState(null, '', `#${id}`);
}

let content, shell;
// Mobile: the sidebar is an off-canvas drawer. These toggle the .nav-open class on .shell
// (all positioning is CSS); on desktop the class is inert because the drawer rules only
// apply under the max-width:980px media query.
function openNav() { shell?.classList.add('nav-open'); menuButton?.setAttribute('aria-expanded', 'true'); }
function closeNav() { shell?.classList.remove('nav-open'); menuButton?.setAttribute('aria-expanded', 'false'); }
let menuButton = null;

function showApp() {
  navButtons = {};
  content = el('main', { class: 'main', id: 'content' });
  const live = el('span', { class: 'dot', title: 'Live updates' });
  const nav = el('nav', { class: 'nav', 'aria-label': 'Main' }, VIEWS.map(([id, label]) => {
    const button = el('button', { type: 'button', onClick: () => { navigate(id); closeNav(); } }, label, el('span', { class: 'badge-count hidden', text: '0' }));
    navButtons[id] = button; return button;
  }));
  menuButton = el('button', { class: 'menu-btn', type: 'button', 'aria-label': 'Toggle navigation', 'aria-expanded': 'false',
    onClick: () => (shell?.classList.contains('nav-open') ? closeNav() : openNav()) },
    el('span', { class: 'bar' }), el('span', { class: 'bar' }), el('span', { class: 'bar' }));
  const scrim = el('div', { class: 'scrim', onClick: closeNav });
  shell = el('div', { class: 'shell' }, scrim,
    el('aside', { class: 'side' }, el('div', { class: 'brand' }, el('div', { class: 'orb' }), el('h1', { text: 'JARVIS' })), nav,
      el('div', { class: 'foot' }, el('div', { class: 'row' }, live, el('span', { text: 'Live' })),
        el('button', { class: 'btn small', type: 'button', style: 'margin-top:10px', onClick: () => logout() }, 'Sign out'))), content);
  clear(root, shell);
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
