# Implementation Plan — Natural-language voice (dashboard mic + desktop language config)

Grounded in: `web/js/views/voice.js`, `web/js/views/agents.js`, `web/js/dom.js`,
`jarvis-local-agent/.../voice_local.py`, `config.py`, `voice_app.py`, and the existing
local-agent unit tests. Baseline GREEN: 313 passed, 9 skipped under `JARVIS_ENV=dev`.

Key verification facts discovered during exploration:
- `node` is NOT on PATH in this environment, so JS is verified by careful review only
  (no `node --check`): confirm el()/textContent only, no innerHTML, SR-absent degrades.
- The repo `.venv` CAN import `vosk` and `sounddevice`, but `voice_local` imports them
  lazily inside methods, so module import stays safe. Tests that touch wake-grammar logic
  must still guard with `pytest.importorskip` where they would otherwise need vosk at import.
- Python test command: `JARVIS_ENV=dev pytest tests/unit tests/integration -q`.

Decisions made (no design doc provided):
- BCP-47 language selection in the browser is a module-level `sessionLang` variable seeded
  from `document.documentElement.lang || 'en-US'`, with optional non-sensitive localStorage
  persistence (`jarvis.voiceLang`). Rationale: no backend contract change, survives view
  remounts, degrades to page language.
- `speakPreview` is extended to accept an optional language override and to pick a matching
  `speechSynthesis` voice when present, falling back to the default voice. Rationale: keeps
  per-agent pitch/rate intact while honoring the selected language.
- Desktop model config uses two fields (`vosk_model_lang`, `vosk_model_path`) with path
  winning when set. Rationale: matches the two documented Vosk load paths (lang code vs.
  downloaded model directory) and the existing flat `JARVIS_` field style.

---

- [ ] 1. Add a language `<select>` + mic toggle button to the dashboard voice card and wire
      browser SpeechRecognition into the existing `send()` flow in `mountOverview`.
      Build the `<select>` with `el()` from a small editable `LANGS` array (en-US, hi-IN,
      es-ES, fr-FR, de-DE, ar-SA, zh-CN). A module var `sessionLang` seeds from
      `document.documentElement.lang || 'en-US'` (optional localStorage persist). Add a mic
      button next to Send: `const SR = window.SpeechRecognition || window.webkitSpeechRecognition;`
      if undefined, disable the button + toast "Voice input needs Chrome or Edge". On click:
      toggle semantics (click while listening stops), `window.speechSynthesis.cancel()`
      (barge-in), create recognition with `continuous=false, interimResults=true,
      lang=sessionLang`, reflect `setState('LISTENING','Speak now','listening')`, write interim
      into `input`, on final set `input.value` and call `send()`. Handle `onerror`/`onend` to
      reset button + state back to READY. Set `aria-label` and `aria-pressed` on the button.
      Files: `jarvis-backend/src/jarvis_backend/web/js/views/voice.js`
      Verify: careful review (node unavailable) — only `el()`/`textContent`, no `innerHTML`,
      no inline script/style string; confirm SR-absent path disables cleanly; load dashboard in
      Chrome/Edge and confirm mic captures speech, fills input, sends, and reply is spoken.

- [ ] 2. Extend `speakPreview` in `agents.js` to honor a selected language and pick a matching
      system voice, without breaking per-agent pitch/rate, then pass `sessionLang` from the
      voice.js call site.
      Add optional `langOverride` param; set `utterance.lang = langOverride || voice.language ||
      'en-US'`; from `window.speechSynthesis.getVoices()` choose the first voice whose `lang`
      starts with the chosen code, else leave default. In `voice.js send()` call
      `speakPreview(r.speech, v, sessionLang)`.
      Files: `jarvis-backend/src/jarvis_backend/web/js/views/agents.js`,
      `jarvis-backend/src/jarvis_backend/web/js/views/voice.js`
      Verify: careful review; confirm existing `speakPreview(text, voice)` callers still work
      (new param optional); confirm reply speaks in the selected language when a voice exists.

- [ ] 3. Add Vosk model + TTS-hint settings to `RuntimeConfig`.
      Add `vosk_model_lang: str = "en-us"`, `vosk_model_path: str = ""`, and
      `tts_voice_hint: str = ""` in the voice section, matching the flat field style.
      Files: `jarvis-local-agent/src/jarvis_local_agent/config.py`
      Verify: `JARVIS_ENV=dev pytest tests/unit/test_config_and_crypto.py tests/unit/test_local_security.py -q` passes.

- [ ] 4. Make `get_model()` accept a language code OR explicit model path, and derive the Vosk
      wake grammar from the configured `wake_phrase`.
      Change `get_model(lang="en-us", model_path="")` to call `vosk.Model(model_path=...)` when
      a path is set else `vosk.Model(lang=...)`; cache per (lang, path). In
      `VoskWakeWordEngine._grammar()` build tokens from the configured `wake_phrase` words
      (plus the phrase itself and `[unk]`) instead of hardcoded `jarvis`/`hey jarvis`; keep the
      energy gate and `_matches` logic. Thread model lang/path into the recognizer builds.
      Files: `jarvis-local-agent/src/jarvis_local_agent/voice_local.py`
      Verify: added unit tests in step 7 pass; module still imports with no mic/vosk at import.

- [ ] 5. Allow `SapiTTSProvider` to prefer a configured voice hint, with English SAPI fallback.
      Accept an optional `voice_hint` (substring/lang) in `__init__`; when set, prefer the first
      SAPI voice whose name/lang contains the hint; keep the existing david/zira male/female
      selection and the per-agent `VOICE_MAP` rate/volume.
      Files: `jarvis-local-agent/src/jarvis_local_agent/voice_local.py`
      Verify: review + step 7 tests (hint selection is pure string logic, testable without SAPI).

- [ ] 6. Thread the new config into `VoiceApp._arun`.
      Pass `vosk_model_lang`/`vosk_model_path` into the model load used by
      `VoskWakeWordEngine`/`VoskSTTProvider`, and `tts_voice_hint` into `SapiTTSProvider(...)`.
      Keep single-MicStream ownership and `RuntimeConfig.load()` usage intact.
      Files: `jarvis-local-agent/src/jarvis_local_agent/voice_app.py`
      Verify: `JARVIS_ENV=dev pytest tests/unit/test_launchers.py -q` passes (launcher invariants).

- [ ] 7. Add unit tests for the config + wake-grammar changes.
      Follow the AST/text-inspection style of `test_launchers.py` and the `RuntimeConfig(...)`
      construction style of `test_local_security.py`. Cover: `RuntimeConfig` picks up
      `JARVIS_VOSK_MODEL_LANG` / `JARVIS_VOSK_MODEL_PATH` (via monkeypatch env, `_env_file=None`);
      `VoskWakeWordEngine._grammar()` derives tokens from a custom `wake_phrase` (e.g. "ok jarvis")
      and not hardcoded English, still containing `[unk]`. Guard any vosk-needing path with
      `pytest.importorskip`.
      Files: `tests/unit/test_voice_config.py` (new)
      Verify: `JARVIS_ENV=dev pytest tests/unit -q` passes; full suite stays green.

- [ ] 8. Document the new env vars and the browser voice path.
      Add `JARVIS_VOSK_MODEL_LANG`, `JARVIS_VOSK_MODEL_PATH`, and the TTS voice hint to the
      local-agent `.env.example`; add a Vosk-language section to `docs/PROVIDERS.md` (how to
      download a model from alphacephei and point `JARVIS_VOSK_MODEL_PATH` at it; browser mic is
      the zero-install multi-language path). Update `SKILL.md` (dashboard mic, multi-language,
      Chrome/Edge; desktop Vosk STT language configurable) and `docs/PRODUCTION_PLAN.md` Phase 2.
      Files: `jarvis-local-agent/.env.example`, `docs/PROVIDERS.md`, `SKILL.md`,
      `docs/PRODUCTION_PLAN.md`
      Verify: review for accuracy; `JARVIS_ENV=dev pytest tests/unit tests/integration -q` still green.

## Constraints (do not regress)
No CSP changes, no inline script/style, no innerHTML, no CDN/third-party JS — Web Speech API
only. Do not change the /api/assist contract, supervisor, policy gate, local validator, or
auth/MFA. Keep single-MicStream ownership and the wake-word energy gate. Graceful degradation
everywhere. Python 3.10 compat and per-file style.
