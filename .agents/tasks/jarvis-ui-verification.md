# JARVIS dashboard UI redesign — verification note (iteration 1)

Cinematic "sci-fi AI-OS" redesign of the buildless dashboard: a center-screen glowing
"JARVIS is speaking" orb that lights up whenever ANY agent responds, a richer palette with a
drifting aurora background and frosted-glass panels, and a mobile drawer nav. Everything
stays 100% buildless and CSP-strict.

## Files changed
- `jarvis-backend/src/jarvis_backend/web/css/app.css` — full restyle; same CSS-variable
  architecture, extended. New: aurora `body::before`, glass `.card`, neon focus/hover,
  `.speak-overlay`/`.speak-orb`/`.glow-*` (center orb), `.menu-btn`/`.scrim`/`.nav-open`
  (mobile drawer), `.table-wrap`, 980/640/400px breakpoints, `prefers-reduced-motion` block.
- `jarvis-backend/src/jarvis_backend/web/js/orb.js` — NEW module. Builds the full-viewport
  center speaking-orb overlay with `el()` and toggles classes only. Exports
  `startSpeakingOrb(agentId, caption)`, `stopSpeakingOrb(token)`, `teardownSpeakingOrb()`.
- `jarvis-backend/src/jarvis_backend/web/js/views/voice.js` — Overview console now drives the
  center orb from real speech boundaries and from `presence` WS events; tears it down in
  `destroy()`.
- `jarvis-backend/src/jarvis_backend/web/js/views/agents.js` — `speakPreview` now accepts an
  optional `hooks` arg (`onStart`/`onEnd`) wired to `utterance.onstart`/`onend` (with
  no-speech + length-cap safety nets). `ACCENT` map re-aligned to the per-agent glow palette.
- `jarvis-backend/src/jarvis_backend/web/js/app.js` — mobile hamburger (`.menu-btn`) in the
  topbar, off-canvas `.side` drawer toggled via `.nav-open` on `.shell`, tap scrim; nav taps
  close the drawer.
- `jarvis-backend/src/jarvis_backend/web/js/views/approvals.js`,
  `jarvis-backend/src/jarvis_backend/web/js/views/system.js` — tables wrapped in `.table-wrap`
  so they scroll horizontally on narrow screens instead of overflowing.
- `index.html` — unchanged (still one external same-origin stylesheet + one module script).

## How the center speaking orb is triggered and torn down
- A single `.speak-overlay` is lazily appended to `<body>` by `orb.js` on first use. It is
  `position:fixed; inset:0; pointer-events:none; z-index:40` (below the toasts at z-index 60),
  so it NEVER traps clicks and never blocks the UI. It has no interactive children.
- Trigger (local reply): `voice.js send()` calls `speakPreview(..., { onStart, onEnd })`.
  `onStart` calls `startSpeakingOrb(respondingAgent, speech)` -> fades the overlay in, tinted
  to the agent; `onEnd` calls `stopSpeakingOrb(token)` -> fades it out and resets state to
  READY. These hooks are wired to the SpeechSynthesisUtterance `onstart`/`onend`, so the orb
  tracks the REAL speech. Safety nets in `speakPreview`: a 250 ms nudge if `onstart` never
  fires, and a length-based cap (<=30 s) guaranteeing `onEnd`. If the browser has no
  speechSynthesis, the hooks still fire so the orb appears, then fades on a timer.
- Trigger (any other agent): a `presence` WS event (supervisor publishes one for every
  `/api/assist`, including the local voice runtime and other tabs) calls `flashPresenceOrb`,
  which shows the orb for a length-estimated duration with NO audio (the reply is already
  spoken where it originated). The console de-dupes the echo of its own just-sent reply via
  `lastSpoken` so the orb is not doubled.
- Teardown: `startSpeakingOrb` returns a token; `stopSpeakingOrb(token)` ignores a stale
  token so a late `onEnd` from an old utterance cannot hide a newer one. The Overview view's
  `destroy()` clears timers, cancels speechSynthesis, and calls `teardownSpeakingOrb()` to
  remove the overlay and drop references — no leaked timers/listeners/nodes across view
  switches or sign-out.

## Per-agent glow color scheme (CSS `.glow-<ID>` in app.css, mirrored in orb.js + agents.js ACCENT)
JARVIS cyan `#36c6ff` · SHURI violet `#a98bff` · TONY gold `#ffbe3d` · NATASHA red `#ff5c6c`
· FRIDAY teal `#2fe0c6` · WONG amber `#ffb020` · VISION green `#39d98a` · DR_STRANGE magenta
`#ff5ad6` · QUICKSILVER silver/blue `#bcd2ff` · NICK_FURY steel `#9fb3c8`. Unknown agents
fall back to the JARVIS cyan class. The orb's rings, core, sparks, name and caption all adopt
the agent color via the `--speak`/`--speak-soft` custom properties set by the glow class.

## Mobile nav pattern
Off-canvas slide-in drawer. Below 980px the `.side` becomes a fixed drawer translated off
screen; a hamburger `.menu-btn` (44x44, built with `el()`) in the topbar toggles `.nav-open`
on `.shell`, which slides the drawer in and shows a tap `.scrim` that closes it. Nav taps also
close it. All tap targets are >=44px (nav buttons, mic, buttons, inputs, switch 44x26, tabs).
Inputs/buttons stack and go full-width <=640px; `.kv` collapses to one column; tables scroll
via `.table-wrap`; the center orb is `vmin`-sized (44vmin, 62vmin <=400px) so it always fits.
Breakpoints: 980px (drawer + single column), 640px (stacking, single-column agents), 400px.

## CSP-compliance checklist (read-verified; CSP is `script-src 'self'; style-src 'self'; ...`)
- Zero `innerHTML` / `insertAdjacentHTML` / `document.write` / `.cssText` anywhere in web JS —
  grep across `web/**/*.js` returned no matches. All DOM is built via `el()`
  (createElement/textContent).
- No inline `<script>` in `index.html`; the only script is `<script type="module"
  src="/ui/js/app.js">` (same-origin).
- No `<style>` element is ever created and no `node.style.cssText` is used. All styling lives
  in the external same-origin `css/app.css`, or is applied via `el()`'s `style` prop, which
  `dom.js` writes one declaration at a time with `node.style.setProperty` (CSSOM) — allowed
  under `style-src 'self'`. The new orb/mobile code uses only CSS classes, no inline styles.
- No external/CDN origins: grep for `http(s)://`, `cdn`, `unpkg`, `jsdelivr`, `googleapis`,
  `fonts.` across `web/**` returned no matches. No third-party font/CSS/JS/image. Fonts remain
  the system stack already in `--font`. The one `data:` favicon is already allowed by
  `img-src 'self' data:`.
- All animation is CSS keyframes (`aurora`, `pulse`, `eq`, `haloPulse`, `breathe`, `spin`,
  `orbit`) in app.css, or CSSOM property writes; no requestAnimationFrame style injection, no
  WebGL/canvas, no `<canvas>`.

## Class-name audit (JS <-> CSS, no dangling references)
- All pre-existing classes the JS creates/toggles are preserved and restyled, not renamed:
  `.shell .side .brand .orb .nav .main .topbar .card .grid.cols-2/cols-3/agents .btn(.primary
  /.ok/.danger/.small) .chip(.lead) .badge(.ok/warn/danger/info/mute) .badge-count .dot
  .switch .kv .list table .meter .empty .stage .ring .core .bars .state-name .chain .arrow
  .agent .avatar .stats .drawer .tabs .turn .reply .secret-box .approve-card .toasts .toast
  .login(-wrap) .error .field` — all present in app.css.
- New classes added in BOTH css and the JS that creates them: `.speak-overlay .backdrop
  .speak-stage .speak-orb .sweep .halo(.r2/.r3) .spark(.s1-.s4) .core2 .speak-caption
  .speak-name .speak-sub .glow-<AGENT> .menu-btn .bar .scrim .nav-open .table-wrap`.

## scripts/ui_smoke.py expectations (needs a browser + Edge; not run here — node/playwright env not available)
The redesign keeps all three smoke invariants satisfied: (1) zero CSP violations — no inline
script/style, no external origins, CSSOM-only inline styling; (2) no `innerHTML`; (3) no token
in web/local storage — untouched; `api.js` still keeps the access token in memory only. The
smoke test also drives login/MFA/nav/views/agent voice+talk/reload/sign-out — all those code
paths and selectors (`form.login`, `.shell`, `.nav button`, `.agent`, `button[role=tab]`,
`input[aria-label=...]`, `.reply`, `Sign out`) are unchanged, so its steps still match.

## pytest result (frontend-only change)
Run from repo root `c:\Users\Lenovo\Desktop\JARVIS\abduls_jarvis` with `JARVIS_ENV=dev`:
`python -m pytest tests/unit tests/integration -q`.

- Result after the redesign: **321 passed, 10 skipped, 5 failed** (76 s). A prior run showed
  4 failed; the count varies run-to-run (322/4 vs 321/5).
- The 10 skips are the expected optional postgres/alembic env-driven skips.
- The failures are PRE-EXISTING and environment/state-driven, NOT caused by this change (this
  change touches only CSS + JS under `web/`; no Python was modified). They were reproduced on
  the untouched tree BEFORE any edit:
  - `tests/unit/test_config_and_crypto.py::test_secrets_directory_is_read` — a stray
    `sqlite+aiosqlite:///.../stale_test.db` database URL in the environment overrides the
    secrets-dir postgres URL the test expects.
  - `tests/integration/test_cli.py::test_migrate_*` (2) — `alembic` is not importable in this
    `.venv` (the repo-root venv has pytest but no pip/alembic).
  - `tests/integration/test_cli.py::test_enroll_mfa_*` — `create-admin` fails with "User
    'owner' already exists": a shared SQLite DB at `C:/Users/Lenovo/.jarvis` carries state
    between runs, which is why these CLI tests flip between pass/fail across runs.
- NOTE on the stated baseline: the task describes a GREEN baseline of 326 passed / 10 skipped.
  On THIS machine that baseline is not reproducible because of the missing `alembic` module,
  the stale-DB env var, and the polluted shared SQLite DB — all independent of the web UI.
  The web dashboard has no dedicated Python tests to run; it is review-verified per the task,
  which the CSP/class-name audits above cover.

## Behavior preserved
Login + MFA second step, hash routing + live WS dot, each view's data loading and timers, mic
button + BCP-47 language select, `speakPreview`, approvals/memory/agents/system pages — all
untouched in behavior. The orb overlay is `pointer-events:none` with no interactive children,
cleans up on `destroy()` and when speech ends, and does not leak timers/listeners. Focus-visible
outlines kept; `prefers-reduced-motion` disables the aurora, orb, and equalizer animations.
