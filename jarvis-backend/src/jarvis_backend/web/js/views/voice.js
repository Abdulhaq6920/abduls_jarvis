// Overview / voice console: runtime state, talk box, live agent chain.
import { api } from '../api.js';
import { clear, el, pretty, timeAgo, toast } from '../dom.js';
import { speakPreview } from './agents.js';

export function mountOverview(root, ctx) {
  const ring = el('div', { class: 'ring' }, el('div', { class: 'core' }));
  const bars = el('div', { class: 'bars', 'aria-hidden': 'true' }, Array.from({ length: 7 }, () => el('i')));
  const stateName = el('div', { class: 'state-name', text: 'READY' });
  const stateSub = el('div', { class: 'muted', text: 'Say “Hey JARVIS”' });
  const chain = el('div', { class: 'chain' }, el('span', { class: 'chip lead', text: 'JARVIS' }));
  const activity = el('div', { class: 'muted', text: 'Idle.' });
  const runtimeBox = el('div', {}, el('span', { class: 'muted', text: 'Checking runtime…' }));
  const tiles = el('div', { class: 'grid cols-3' });
  const input = el('input', { type: 'text', maxLength: 500, 'aria-label': 'Type a request',
    placeholder: 'Try: ask SHURI to research LangGraph   |   remember that my car is blue' });
  let voices = {};

  const setState = (name, sub, mode) => {
    stateName.textContent = name; stateSub.textContent = sub;
    ring.className = `ring ${mode || ''} ${mode ? 'active' : ''}`; bars.className = `bars ${mode ? 'on' : ''}`;
  };

  function drawRuntime(rt) {
    const on = rt?.connected;
    clear(runtimeBox,
      el('div', { class: 'row' }, el('span', { class: `dot ${on ? 'ok' : 'warn'}` }),
        el('b', { text: on ? 'Local runtime connected' : 'Local runtime not connected' })),
      on
        ? el('dl', { class: 'kv', style: 'margin-top:10px' },
          el('dt', { text: 'State' }), el('dd', { text: rt.conversation_state }),
          el('dt', { text: 'Microphone' }), el('dd', {}, rt.microphone_paused ? el('span', { class: 'badge warn', text: 'PAUSED' }) : el('span', { class: 'badge ok', text: 'LISTENING FOR WAKE WORD' })),
          el('dt', { text: 'Wake phrase' }), el('dd', { text: `“${rt.wake_word}”` }),
          el('dt', { text: 'Last heartbeat' }), el('dd', { text: `${rt.last_seen_seconds}s ago` }))
        : el('p', { class: 'muted', text: 'Start jarvis-voice on your PC. It reports its real microphone state here.' }));
    if (on) {
      const map = { LISTENING: ['LISTENING', 'Speak now', 'listening'], PROCESSING: ['THINKING', 'Routing your request', 'thinking'],
        RESPONDING: ['SPEAKING', 'Responding', 'active'], PAUSED: ['MIC PAUSED', 'Microphone is off', ''] };
      const [n, s, m] = map[rt.conversation_state] || ['READY', 'Say “Hey JARVIS”', ''];
      setState(n, s, m);
    }
  }

  async function refresh() {
    const [status, rt, vs] = await Promise.all([api('/api/system/status'), api('/api/runtime'), api('/api/voices')]);
    voices = Object.fromEntries(vs.map((v) => [v.agent_id, v]));
    drawRuntime(rt);
    clear(tiles,
      tile('Agents', status.agents), tile('Pending approvals', status.pending_approvals, status.pending_approvals ? 'warn' : ''),
      tile('Scheduler', status.scheduler_running ? 'running' : 'stopped', status.scheduler_running ? 'ok' : 'warn'));
    ctx.setBadge('approvals', status.pending_approvals);
  }
  const tile = (label, value, tone = '') => el('div', { class: 'card' }, el('h3', { text: label }),
    el('div', { class: 'state-name', style: 'font-size:22px;letter-spacing:1px' }, String(value), tone ? el('span', { class: `badge ${tone}`, style: 'margin-left:10px' }, tone === 'ok' ? 'OK' : 'CHECK') : null));

  async function send() {
    const text = input.value.trim(); if (!text) return;
    input.value = ''; setState('THINKING', 'Routing…', 'thinking');
    try {
      const r = await api('/api/assist', { method: 'POST', body: { session_id: 'dashboard', utterance: text } });
      const agents = [...new Set(r.trace.filter((t) => t.agent_id).map((t) => t.agent_id))];
      clear(chain, agents.map((a, i) => [i ? el('span', { class: 'arrow', text: '→' }) : null, el('span', { class: `chip ${a === r.responding_agent ? 'lead' : ''}`, text: pretty(a) })]));
      activity.textContent = `${r.delegation_note ? `${r.delegation_note}  ` : ''}“${r.speech}”`;
      setState('SPEAKING', pretty(r.responding_agent), 'active');
      const v = voices[r.responding_agent]; if (v) speakPreview(r.speech, v);
      setTimeout(() => setState('READY', 'Say “Hey JARVIS”', ''), 2500);
    } catch (error) { setState('READY', 'Say “Hey JARVIS”', ''); toast(error.message, 'error'); }
    refresh().catch(() => {});
  }
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter') send(); });

  root.append(el('div', { class: 'grid cols-2' },
    el('div', { class: 'stack' },
      el('div', { class: 'card' }, el('h3', { text: 'Voice' }),
        el('div', { class: 'stage' }, ring, stateName, stateSub, bars),
        el('div', { class: 'row' }, input, el('button', { class: 'btn primary', type: 'button', onClick: send }, 'Send'))),
      el('div', { class: 'card' }, el('h3', { text: 'Agent presence' }), chain, el('div', { style: 'margin-top:12px' }, activity))),
    el('div', { class: 'stack' }, el('div', { class: 'card' }, el('h3', { text: 'Local runtime' }), runtimeBox), tiles)));

  refresh().catch((e) => toast(e.message, 'error'));
  const timer = setInterval(() => refresh().catch(() => {}), 10000);
  return { onEvent: (e) => { if (e.type === 'runtime' || e.type === 'approval' || e.type === 'presence') refresh().catch(() => {}); },
    destroy: () => clearInterval(timer) };
}
