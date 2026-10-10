// Approvals: the human gate. Shows exactly what will run before anything is granted.
import { api } from '../api.js';
import { clear, clock, el, pretty, timeAgo, toast } from '../dom.js';

const TONE = { PENDING: 'warn', GRANTED: 'info', EXECUTING: 'info', EXECUTED: 'ok', FAILED: 'danger', DENIED: 'mute', EXPIRED: 'mute' };
const RISK = { SAFE: 'ok', RESTRICTED: 'warn', DANGEROUS: 'danger' };

export function mountApprovals(root, ctx) {
  const pending = el('div', { class: 'stack' });
  const history = el('div');

  async function load() {
    const all = await api('/api/approvals?limit=100');
    const waiting = all.filter((a) => a.status === 'PENDING');
    ctx.setBadge('approvals', waiting.length);
    clear(pending, waiting.length ? waiting.map(card)
      : el('div', { class: 'empty', text: 'Nothing is waiting for you. Risky actions will appear here before they run.' }));
    const past = all.filter((a) => a.status !== 'PENDING');
    clear(history, past.length ? el('table', {},
      el('thead', {}, el('tr', {}, ['When', 'Agent', 'Action', 'Status'].map((h) => el('th', { text: h })))),
      el('tbody', {}, past.map((a) => el('tr', {},
        el('td', { class: 'faint', text: clock(a.created_at) }), el('td', { text: pretty(a.agent_id) }),
        el('td', { text: a.summary }),
        el('td', {}, el('span', { class: `badge ${TONE[a.status] || 'mute'}`, text: a.status }),
          a.result && a.result.error ? el('div', { class: 'faint', text: a.result.error }) : null))))) :
      el('div', { class: 'empty', text: 'No history yet.' }));
  }

  const card = (a) => el('div', { class: 'approve-card' },
    el('div', { class: 'row spread' },
      el('div', { class: 'row' }, el('b', { text: pretty(a.agent_id) }), el('span', { class: `badge ${RISK[a.risk] || 'warn'}`, text: a.risk })),
      el('span', { class: 'faint', text: timeAgo(a.created_at) })),
    el('p', { class: 'mono', style: 'margin:10px 0', text: a.summary }),
    el('div', { class: 'faint', text: 'Review the exact action above. It runs on your PC only after you approve, and your local agent re-checks it again before executing.' }),
    el('div', { class: 'row', style: 'margin-top:12px' },
      el('button', { class: 'btn ok', type: 'button', onClick: () => decide(a, true) }, 'Approve'),
      el('button', { class: 'btn danger', type: 'button', onClick: () => decide(a, false) }, 'Deny')));

  async function decide(a, granted) {
    try { await api(`/api/approvals/${a.id}/decision`, { method: 'POST', body: { granted } }); toast(granted ? 'Approved' : 'Denied', granted ? 'ok' : ''); }
    catch (e) { toast(e.message, 'error'); }
    await load();
  }

  root.append(el('div', { class: 'stack' }, el('div', { class: 'card' }, el('h3', { text: 'Waiting for your decision' }), pending),
    el('div', { class: 'card' }, el('h3', { text: 'History' }), history)));
  load().catch((e) => toast(e.message, 'error'));
  const timer = setInterval(() => load().catch(() => {}), 8000);
  return { onEvent: (e) => { if (e.type === 'approval') load().catch(() => {}); }, destroy: () => clearInterval(timer) };
}
