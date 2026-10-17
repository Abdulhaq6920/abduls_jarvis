// The Agents page: roster grid + a detail drawer (Overview / Voice / Activity / Talk).
import { api } from '../api.js';
import { clear, clock, el, initial, pretty, timeAgo, toast } from '../dom.js';

const ACCENT = {
  JARVIS: '#36c6ff', FRIDAY: '#7ee0c0', VISION: '#c9a6ff', NATASHA: '#ff8fa3', WONG: '#ffcf6b',
  TONY: '#ff9d4d', SHURI: '#9af0ff', QUICKSILVER: '#d7e3ff', NICK_FURY: '#ff6b6b', DR_STRANGE: '#8ad6a8',
};

export function speakPreview(text, voice, langOverride) {
  // Browser approximation of the agent's voice (the real audio is produced by the local
  // runtime). Pitch/speed come from the saved profile so edits are audible straight away.
  // An optional langOverride lets the Overview console speak the reply in the language the
  // user picked for voice input, falling back to the agent's own language.
  if (!('speechSynthesis' in window)) { toast('This browser has no speech synthesis.', 'error'); return; }
  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.pitch = Math.min(2, Math.max(0, voice.pitch));
  utterance.rate = Math.min(2, Math.max(0.5, voice.speed));
  utterance.lang = langOverride || voice.language || 'en-US';
  const prefix = utterance.lang.slice(0, 2).toLowerCase();
  const match = (window.speechSynthesis.getVoices() || []).find((v) => v.lang && v.lang.toLowerCase().startsWith(prefix));
  if (match) utterance.voice = match;
  window.speechSynthesis.speak(utterance);
}

export function mountAgents(root, ctx) {
  let agents = [];
  let selected = null;
  let tab = 'overview';
  const grid = el('div', { class: 'grid agents' });
  const drawer = el('div', { class: 'drawer' });

  // `background: true` refreshes data + the roster grid ONLY. Redrawing the detail drawer
  // would destroy in-progress work (unsaved slider edits, a reply the user is reading), so
  // timers and live events must never do it. User actions call load() and redraw fully.
  async function load({ background = false } = {}) {
    agents = await api('/api/agents');
    if (!selected) selected = agents[0]?.spec.id ?? null;
    if (background) clear(grid, agents.map(card));
    else draw();
  }

  function card(agent) {
    const { spec, voice, stats } = agent;
    const color = ACCENT[spec.id] || '#36c6ff';
    return el('button', {
      class: `card agent ${selected === spec.id ? 'selected' : ''} ${agent.enabled ? '' : 'off'}`, type: 'button',
      'aria-pressed': String(selected === spec.id), onClick: () => { selected = spec.id; tab = 'overview'; draw(); },
    },
      el('div', { class: 'row' },
        el('div', { class: 'avatar', style: `background:${color}` }, initial(spec.display_name)),
        el('div', { class: 'grow' }, el('h4', { text: spec.display_name }),
          el('span', { class: 'muted' }, el('span', { class: `dot ${agent.enabled ? 'ok' : ''}` }), ' ',
            agent.enabled ? 'Active' : 'Disabled'))),
      el('div', { class: 'role', text: spec.role }),
      el('div', { class: 'row wrap' }, spec.personality.slice(0, 3).map((p) => el('span', { class: 'chip', text: p }))),
      el('div', { class: 'stats' },
        el('div', {}, el('b', { text: String(stats.turns) }), el('span', { text: 'tasks' })),
        el('div', {}, el('b', { text: stats.avg_latency_ms == null ? '–' : `${Math.round(stats.avg_latency_ms)}ms` }), el('span', { text: 'avg time' })),
        el('div', {}, el('b', { text: timeAgo(stats.last_active) }), el('span', { text: 'last active' }))),
      el('div', { class: 'faint mono', text: `voice: ${voice.voice_id}` }));
  }

  function draw() {
    clear(grid, agents.map(card));
    const agent = agents.find((a) => a.spec.id === selected);
    clear(drawer, agent ? detail(agent) : null);
  }

  function detail(agent) {
    const tabs = [['overview', 'Overview'], ['voice', 'Voice'], ['activity', 'Activity'], ['talk', 'Talk to agent']];
    const body = el('div');
    const render = { overview, voice, activity, talk }[tab];
    render(body, agent);
    return el('div', { class: 'card' },
      el('div', { class: 'row spread' },
        el('div', { class: 'row' }, el('h3', { text: pretty(agent.spec.id) })),
        el('label', { class: 'row muted', title: agent.can_disable ? '' : 'JARVIS is the supervisor and cannot be disabled' },
          agent.enabled ? 'Enabled' : 'Disabled',
          el('span', { class: 'switch' },
            el('input', { type: 'checkbox', checked: agent.enabled, disabled: !agent.can_disable,
              'aria-label': `Enable ${agent.spec.display_name}`, onChange: (e) => toggle(agent, e.target.checked) }),
            el('span')))),
      el('div', { class: 'tabs', role: 'tablist' }, tabs.map(([id, label]) =>
        el('button', { type: 'button', role: 'tab', 'aria-selected': String(tab === id), onClick: () => { tab = id; draw(); } }, label))),
      body);
  }

  async function toggle(agent, enabled) {
    try {
      await api(`/api/agents/${agent.spec.id}/enabled`, { method: 'PUT', body: { enabled } });
      toast(`${agent.spec.display_name} ${enabled ? 'enabled' : 'disabled'}`, 'ok');
    } catch (error) { toast(error.message, 'error'); }
    await load();
  }

  function overview(body, { spec }) {
    body.append(el('dl', { class: 'kv' },
      el('dt', { text: 'Role' }), el('dd', { text: spec.role }),
      el('dt', { text: 'Personality' }), el('dd', {}, spec.personality.map((p) => el('span', { class: 'chip', text: p }))),
      el('dt', { text: 'Capabilities' }), el('dd', {}, spec.capabilities.map((c) => el('span', { class: 'chip', text: c }))),
      el('dt', { text: 'Local tools' }), el('dd', {}, spec.tools.length ? spec.tools.map((t) => el('span', { class: 'chip', text: t })) : el('span', { class: 'muted', text: 'none (no computer access)' })),
      el('dt', { text: 'Routing hints' }), el('dd', {}, spec.routing_keywords.map((k) => el('span', { class: 'chip', text: k })))),
      el('p', { class: 'faint', text: 'Personality shapes how an agent talks. It never changes what it is allowed to do: every action still passes the policy gate and your approval.' }));
  }

  function voice(body, agent) {
    const v = agent.voice;
    const state = { ...v };
    const output = (key, fmt) => el('span', { class: 'mono', text: fmt(state[key]) });
    const speedOut = output('speed', (n) => `${Number(n).toFixed(2)}×`);
    const pitchOut = output('pitch', (n) => Number(n).toFixed(2));
    const slider = (key, out) => el('input', { type: 'range', min: 0.5, max: 2, step: 0.05, value: state[key],
      'aria-label': key, onInput: (e) => { state[key] = Number(e.target.value); out.textContent = key === 'speed' ? `${state[key].toFixed(2)}×` : state[key].toFixed(2); } });
    const style = el('input', { type: 'text', value: state.style, maxLength: 48, onInput: (e) => { state.style = e.target.value; } });
    const voiceId = el('input', { type: 'text', value: state.voice_id, maxLength: 64, pattern: '[a-z0-9_\\-]+', onInput: (e) => { state.voice_id = e.target.value; } });
    const enabled = el('input', { type: 'checkbox', checked: state.enabled, onChange: (e) => { state.enabled = e.target.checked; } });

    const save = async () => {
      try {
        await api(`/api/agents/${agent.spec.id}/voice`, { method: 'PUT',
          body: { speed: state.speed, pitch: state.pitch, style: state.style, voice_id: state.voice_id, enabled: state.enabled } });
        toast('Voice saved', 'ok'); await load();
      } catch (error) { toast(error.message, 'error'); }
    };
    const reset = async () => {
      try { await api(`/api/agents/${agent.spec.id}/voice`, { method: 'DELETE' }); toast('Voice reset to default', 'ok'); await load(); }
      catch (error) { toast(error.message, 'error'); }
    };
    body.append(el('div', { class: 'grid cols-2' },
      el('div', { class: 'stack' },
        el('label', { class: 'field' }, el('span', { class: 'row spread' }, 'Speed', speedOut), slider('speed', speedOut)),
        el('label', { class: 'field' }, el('span', { class: 'row spread' }, 'Pitch', pitchOut), slider('pitch', pitchOut)),
        el('label', { class: 'field' }, 'Style', style)),
      el('div', { class: 'stack' },
        el('label', { class: 'field' }, 'Voice id (synthetic voice name)', voiceId),
        el('dl', { class: 'kv' }, el('dt', { text: 'Provider' }), el('dd', { class: 'mono', text: v.voice_provider }),
          el('dt', { text: 'Language' }), el('dd', { class: 'mono', text: v.language })),
        el('label', { class: 'row muted' }, enabled, 'Speak responses aloud for this agent'))),
      el('div', { class: 'row', style: 'margin-top:14px' },
        el('button', { class: 'btn primary', type: 'button', onClick: save }, 'Save voice'),
        el('button', { class: 'btn', type: 'button', onClick: () => speakPreview(`Hello. This is ${agent.spec.display_name}.`, state) }, 'Preview'),
        el('button', { class: 'btn', type: 'button', onClick: reset }, 'Reset to default')),
      el('p', { class: 'faint', text: 'Preview uses your browser voice as an approximation of pitch and speed. The local runtime renders the real agent voice. Voices are original synthetic voices, not imitations of any real person.' }));
  }

  function activity(body, agent) {
    const list = el('div', { class: 'list' }, el('div', { class: 'empty', text: 'Loading…' }));
    body.append(list);
    api(`/api/agents/${agent.spec.id}/activity?limit=20`).then((turns) => {
      clear(list, turns.length ? turns.map((t) => el('div', { class: 'turn' },
        el('div', { class: 'row spread' }, el('span', { class: 'faint', text: clock(t.created_at) }), el('span', { class: 'faint', text: `${t.latency_ms} ms` })),
        el('div', { class: 'u', text: `You: ${t.utterance}` }), el('div', { class: 'a', text: `${agent.spec.display_name}: ${t.speech}` })))
        : el('div', { class: 'empty', text: 'No activity yet. Use “Talk to agent” to give it something to do.' }));
    }).catch((e) => clear(list, el('div', { class: 'error', text: e.message })));
  }

  function talk(body, agent) {
    const input = el('input', { type: 'text', maxLength: 500, placeholder: `Ask ${agent.spec.display_name} to…`, 'aria-label': 'Task' });
    const out = el('div');
    const send = async () => {
      const text = input.value.trim(); if (!text) return;
      input.value = ''; clear(out, el('div', { class: 'muted', text: 'Working…' }));
      try {
        const r = await api('/api/assist', { method: 'POST', body: { session_id: 'dashboard', utterance: `ask ${pretty(agent.spec.id)} to ${text}` } });
        const chain = [...new Set(r.trace.filter((t) => t.agent_id).map((t) => t.agent_id))];
        clear(out, el('div', { class: 'reply' },
          el('div', { class: 'chain' }, chain.map((a, i) => [i ? el('span', { class: 'arrow', text: '→' }) : null, el('span', { class: `chip ${a === r.responding_agent ? 'lead' : ''}`, text: pretty(a) })])),
          el('p', { text: r.speech }),
          r.pending_approval_id ? el('span', { class: 'badge warn', text: 'Waiting for your approval' }) : null,
          el('div', { class: 'row' }, el('button', { class: 'btn small', type: 'button', onClick: () => speakPreview(r.speech, agent.voice) }, '🔊 Speak'))));
        speakPreview(r.speech, agent.voice);
      } catch (error) { clear(out, el('div', { class: 'error', text: error.message })); }
      ctx.refreshBadges?.();
    };
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter') send(); });
    body.append(el('div', { class: 'row' }, input, el('button', { class: 'btn primary', type: 'button', onClick: send }, 'Send')), out,
      el('p', { class: 'faint', text: 'Runs through the same supervisor, policy gate and approval flow as a spoken request.' }));
  }

  root.append(el('div', { class: 'stack' }, grid, drawer));
  load().catch((e) => toast(e.message, 'error'));
  const timer = setInterval(() => load({ background: true }).catch(() => {}), 20000);
  return { onEvent: (event) => { if (event.type === 'presence') load({ background: true }).catch(() => {}); },
    destroy: () => clearInterval(timer) };
}
