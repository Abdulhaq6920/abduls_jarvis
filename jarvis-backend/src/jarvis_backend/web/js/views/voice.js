// Overview / voice console: runtime state, talk box, live agent chain.
import { api } from '../api.js';
import { clear, el, pretty, timeAgo, toast } from '../dom.js';
import { startSpeakingOrb, stopSpeakingOrb, teardownSpeakingOrb } from '../orb.js';
import { speakPreview } from './agents.js';

// BCP-47 languages offered for browser voice input/output. Edit this list to add more;
// nothing else hardcodes these codes.
const LANGS = [['en-US', 'English'], ['hi-IN', 'Hindi'], ['es-ES', 'Spanish'], ['fr-FR', 'French'], ['de-DE', 'German'], ['ar-SA', 'Arabic'], ['zh-CN', 'Chinese']];

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

  // Browser speech INPUT (Web Speech API only; no third-party JS). SpeechRecognition is
  // Chrome/Edge-only AND needs a secure context (https, or localhost/127.0.0.1), so the mic
  // button degrades gracefully where it is unavailable.
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const secureCtx = window.isSecureContext
    || ['localhost', '127.0.0.1', '[::1]'].includes(location.hostname);
  // document.documentElement.lang is often a bare "en" which some engines reject; only accept
  // a stored full BCP-47 code, else default to en-US.
  const stored = localStorage.getItem('jarvis.voiceLang');
  let sessionLang = (stored && stored.includes('-')) ? stored : 'en-US';
  let recognition = null;
  let listening = false;
  let heardAnything = false;

  const langSelect = el('select', { class: 'btn', 'aria-label': 'Voice language',
    onChange: (e) => { sessionLang = e.target.value; localStorage.setItem('jarvis.voiceLang', sessionLang); } },
    LANGS.map(([code, label]) => el('option', { value: code, selected: code === sessionLang }, label)));

  // 🎤 mic button. A `.listening` class drives the pulsing red "I'm hearing you" state in CSS.
  const mic = el('button', { class: 'btn mic', type: 'button', title: 'Click and speak',
    'aria-label': 'Start voice input', 'aria-pressed': 'false', onClick: toggleMic }, '🎤 Speak');
  if (!SR || !secureCtx) {
    mic.disabled = true;
    mic.title = !SR ? 'Voice input needs Chrome or Edge'
      : 'Voice input needs a secure page (https:// or localhost)';
  }

  // A PERSISTENT note under the controls. A transient toast is easy to miss, and a blocked
  // microphone needs instructions that stay on screen until the user fixes it.
  const micNote = el('div', { class: 'mic-note hidden', role: 'status' });

  function showMicNote(message, kind = 'warn') {
    micNote.textContent = message;
    micNote.className = `mic-note ${kind}`;
  }
  function clearMicNote() {
    micNote.textContent = '';
    micNote.className = 'mic-note hidden';
  }

  function resetMic() {
    listening = false;
    mic.classList.remove('listening');
    mic.setAttribute('aria-pressed', 'false');
    mic.textContent = '🎤 Speak';
  }

  // SpeechRecognition does NOT reliably surface the browser's microphone prompt, which is
  // why "nothing happens / no popup" is the usual symptom. Requesting the audio stream
  // directly makes Chrome/Edge show its standard permission dialog, and gives us an exact
  // reason when access is refused. The stream is closed immediately: we only needed consent.
  async function ensureMicPermission() {
    if (!navigator.mediaDevices?.getUserMedia) return true; // can't pre-check; let SR try
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      stream.getTracks().forEach((track) => track.stop());
      clearMicNote();
      return true;
    } catch (err) {
      const name = err?.name || '';
      showMicNote(
        name === 'NotAllowedError' || name === 'SecurityError'
          ? 'Microphone blocked. Click the camera/lock icon at the left of the address bar, set Microphone to Allow, then reload this page.'
          : name === 'NotFoundError' || name === 'DevicesNotFoundError'
            ? 'No microphone detected. Connect a mic (or check Windows Settings → Privacy → Microphone) and reload.'
            : `Could not open the microphone (${name || 'unknown error'}). Check Windows Settings → Privacy → Microphone.`,
        'error',
      );
      return false;
    }
  }

  async function toggleMic() {
    if (!SR) { showMicNote('Voice input needs Chrome or Edge. Firefox/Safari do not support it.', 'error'); return; }
    if (!secureCtx) { showMicNote('Voice input needs https:// or localhost — open http://localhost:8077 instead of an IP address.', 'error'); return; }
    if (listening) { try { recognition.stop(); } catch { /* already stopping */ } return; }

    // Ask for consent BEFORE starting recognition so the prompt is actually shown.
    mic.disabled = true;
    try {
      const granted = await ensureMicPermission();
      if (!granted) return;
    } finally {
      mic.disabled = false;
    }

    window.speechSynthesis?.cancel(); // barge-in: stop any reply so the mic can hear
    heardAnything = false;
    recognition = new SR();
    recognition.continuous = false;
    recognition.interimResults = true;
    recognition.maxAlternatives = 1;
    recognition.lang = sessionLang;

    recognition.onresult = (event) => {
      let transcript = '';
      for (const result of event.results) transcript += result[0].transcript;
      if (transcript.trim()) { heardAnything = true; input.value = transcript; } // interim text live
    };
    recognition.onend = () => {
      resetMic();
      if (input.value.trim()) send();
      else { toast(heardAnything ? 'Didn’t catch that — try again.' : 'No speech detected. Speak after the mic turns red.', ''); setState('READY', 'Say “Hey JARVIS”', ''); }
    };
    recognition.onerror = (event) => {
      const msg = ({
        'not-allowed': 'Microphone blocked. Click the 🔒/camera icon in the address bar and allow the mic, then reload.',
        'service-not-allowed': 'Microphone blocked by the browser or OS privacy settings.',
        'no-speech': 'No speech detected. Click Speak, wait for red, then talk.',
        'audio-capture': 'No microphone found. Check your mic is connected.',
        'network': 'Speech recognition network error — check your connection.',
        'aborted': '',
      })[event.error] ?? 'Voice input error. Try again.';
      // Permission problems get a persistent note (they need user action to fix);
      // transient ones stay a toast so they clear themselves.
      if (msg) {
        if (event.error === 'not-allowed' || event.error === 'service-not-allowed') showMicNote(msg, 'error');
        else toast(msg, '');
      }
      resetMic(); setState('READY', 'Say “Hey JARVIS”', '');
    };

    try {
      recognition.start();
    } catch (err) {
      // start() throws if a prior session is still winding down; reset cleanly.
      resetMic(); setState('READY', 'Say “Hey JARVIS”', '');
      toast('Mic is busy — click Speak again.', '');
      return;
    }
    listening = true;
    clearMicNote();                       // permission is granted and we are live
    mic.classList.add('listening');
    mic.setAttribute('aria-pressed', 'true');
    mic.textContent = '● Listening…';
    setState('LISTENING', 'Speak now', 'listening');
  }

  // Explain a disabled mic button up front rather than leaving the user guessing why
  // clicking it does nothing (the button is disabled, so toggleMic would never run).
  if (!SR) showMicNote('Voice input needs Chrome or Edge — Firefox and Safari do not support speech recognition.', 'warn');
  else if (!secureCtx) showMicNote('Voice input needs https:// or localhost. Open http://localhost:8077 rather than an IP address.', 'warn');

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

  // Tracks the speech this console just spoke itself, so the matching `presence` WS event
  // (the supervisor publishes one for every /api/assist) does not re-trigger the orb and
  // double it up. Cleared after a short window.
  let lastSpoken = '';
  let presenceOrbTimer = null;

  async function send() {
    const text = input.value.trim(); if (!text) return;
    input.value = ''; setState('THINKING', 'Routing…', 'thinking');
    try {
      const r = await api('/api/assist', { method: 'POST', body: { session_id: 'dashboard', utterance: text } });
      const agents = [...new Set(r.trace.filter((t) => t.agent_id).map((t) => t.agent_id))];
      clear(chain, agents.map((a, i) => [i ? el('span', { class: 'arrow', text: '→' }) : null, el('span', { class: `chip ${a === r.responding_agent ? 'lead' : ''}`, text: pretty(a) })]));
      activity.textContent = `${r.delegation_note ? `${r.delegation_note}  ` : ''}“${r.speech}”`;
      setState('SPEAKING', pretty(r.responding_agent), 'active');
      lastSpoken = r.speech;
      setTimeout(() => { if (lastSpoken === r.speech) lastSpoken = ''; }, 4000);
      // Light up the big center-screen orb tinted to the responding agent, tracking the
      // REAL speech boundaries (onstart/onend) exposed by speakPreview. If this browser has
      // no speechSynthesis the hooks still fire so the orb appears, then fades on a timer.
      let orbToken = null;
      const v = voices[r.responding_agent];
      speakPreview(r.speech, v || { pitch: 1, speed: 1, language: sessionLang }, sessionLang, {
        onStart: () => { orbToken = startSpeakingOrb(r.responding_agent, r.speech); },
        onEnd: () => { stopSpeakingOrb(orbToken); setState('READY', 'Say “Hey JARVIS”', ''); },
      });
    } catch (error) { setState('READY', 'Say “Hey JARVIS”', ''); toast(error.message, 'error'); }
    refresh().catch(() => {});
  }

  // A `presence` event means some agent spoke — e.g. the local voice runtime, or another
  // dashboard tab. Flash the center orb (no audio: that reply is spoken wherever it
  // originated). Skip the echo of our own just-sent request to avoid doubling the orb.
  function flashPresenceOrb(e) {
    if (!e.responding_agent) return;
    if (e.speech && e.speech === lastSpoken) return;
    const token = startSpeakingOrb(e.responding_agent, e.speech || '');
    clearTimeout(presenceOrbTimer);
    presenceOrbTimer = setTimeout(() => stopSpeakingOrb(token), Math.min(12000, 2500 + (e.speech ? e.speech.length * 55 : 0)));
  }
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter') send(); });

  root.append(el('div', { class: 'grid cols-2' },
    el('div', { class: 'stack' },
      el('div', { class: 'card' }, el('h3', { text: 'Voice' }),
        el('div', { class: 'stage' }, ring, stateName, stateSub, bars),
        el('div', { class: 'row' }, input, langSelect, mic, el('button', { class: 'btn primary', type: 'button', onClick: send }, 'Send')),
        micNote),
      el('div', { class: 'card' }, el('h3', { text: 'Agent presence' }), chain, el('div', { style: 'margin-top:12px' }, activity))),
    el('div', { class: 'stack' }, el('div', { class: 'card' }, el('h3', { text: 'Local runtime' }), runtimeBox), tiles)));

  refresh().catch((e) => toast(e.message, 'error'));
  const timer = setInterval(() => refresh().catch(() => {}), 10000);
  return {
    onEvent: (e) => {
      if (e.type === 'presence') flashPresenceOrb(e);
      if (e.type === 'runtime' || e.type === 'approval' || e.type === 'presence') refresh().catch(() => {});
    },
    destroy: () => {
      clearInterval(timer); clearTimeout(presenceOrbTimer);
      window.speechSynthesis?.cancel();
      teardownSpeakingOrb();
    },
  };
}
