// Center-of-SCREEN "JARVIS is speaking" orb overlay.
//
// A single fixed, full-viewport overlay lives on <body>. It NEVER traps clicks
// (pointer-events:none) and sits below the toasts. It fades in while an agent is speaking
// and out when speech ends. All visuals are CSS (css/app.css, .speak-* / .glow-* classes);
// this module only toggles classes and builds nodes via el() — no innerHTML, no inline
// <style>, no CSSOM style strings beyond the per-property helper in dom.js.
import { el, pretty } from './dom.js';

// Per-agent glow is applied by adding a `glow-<AGENT_ID>` class (defined in app.css). The
// set here mirrors those classes so an unknown agent falls back to the JARVIS cyan.
const GLOW_AGENTS = new Set([
  'JARVIS', 'SHURI', 'TONY', 'NATASHA', 'FRIDAY', 'WONG', 'VISION', 'DR_STRANGE', 'QUICKSILVER', 'NICK_FURY',
]);

let overlay = null;
let nameNode = null;
let subNode = null;
let hideTimer = null;
let activeToken = 0; // guards against a stale end() hiding a newer speech

function ensureOverlay() {
  if (overlay) return overlay;
  nameNode = el('div', { class: 'speak-name' });
  subNode = el('div', { class: 'speak-sub' });
  overlay = el('div', { class: 'speak-overlay', 'aria-hidden': 'true' },
    el('div', { class: 'backdrop' }),
    el('div', { class: 'speak-stage' },
      el('div', { class: 'speak-orb' },
        el('div', { class: 'sweep' }),
        el('div', { class: 'halo' }), el('div', { class: 'halo r2' }), el('div', { class: 'halo r3' }),
        el('div', { class: 'spark s1' }), el('div', { class: 'spark s2' }),
        el('div', { class: 'spark s3' }), el('div', { class: 'spark s4' }),
        el('div', { class: 'core2' })),
      el('div', { class: 'speak-caption' }, nameNode, subNode)));
  document.body.append(overlay);
  return overlay;
}

// Show the center orb tinted to `agentId`, captioned with its name + the spoken text.
export function startSpeakingOrb(agentId, caption) {
  const node = ensureOverlay();
  clearTimeout(hideTimer); hideTimer = null;
  const token = ++activeToken;
  const glow = GLOW_AGENTS.has(agentId) ? `glow-${agentId}` : 'glow-JARVIS';
  node.className = `speak-overlay on ${glow}`;
  nameNode.textContent = pretty(agentId || 'JARVIS');
  subNode.textContent = caption || '';
  return token;
}

// Hide the center orb. Pass the token returned by startSpeakingOrb so a late end() from an
// earlier utterance cannot hide a newer one.
export function stopSpeakingOrb(token) {
  if (!overlay) return;
  if (token !== undefined && token !== activeToken) return;
  clearTimeout(hideTimer); hideTimer = null;
  overlay.classList.remove('on');
}

// Remove the overlay entirely and drop timers. Views call this from destroy().
export function teardownSpeakingOrb() {
  clearTimeout(hideTimer); hideTimer = null;
  activeToken++;
  overlay?.remove();
  overlay = nameNode = subNode = null;
}
