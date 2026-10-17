"""Voice-first launcher: real microphone + centre-screen wave overlay.

Run this to actually *talk* to JARVIS:

    jarvis-voice          (or: python -m jarvis_local_agent.voice_app)

Threading model -- Tk is not thread-safe and insists on the main thread, so:

    main thread      WaveOverlay.run()        Tk mainloop, renders the HUD
    "jarvis-rt"      asyncio event loop       JarvisRuntime, backend calls
    "wake-vosk"      wake-word spotting       owns nothing but reads mic frames
    "amp-pump"       mic level -> overlay     drives live waveform motion
    sounddevice cb   audio callback           fills the frame queue

All state reaches the overlay through its thread-safe queue.
"""
from __future__ import annotations

import asyncio
import sys
import threading
import time

from jarvis_shared.conversation import ConversationState
from jarvis_shared.log import get_logger, setup_logging

from jarvis_local_agent import single_instance
from jarvis_local_agent.backend_client import BackendClient, BackendError
from jarvis_local_agent.config import RuntimeConfig
from jarvis_local_agent.overlay import WaveOverlay
from jarvis_local_agent.runtime import JarvisRuntime
from jarvis_local_agent.voice_local import (
    MicStream,
    SapiTTSProvider,
    VoskSTTProvider,
    VoskWakeWordEngine,
)

log = get_logger(__name__)

BANNER = r"""
════════════════════════════════════════════════════════════
   J A R V I S   ·   voice runtime
════════════════════════════════════════════════════════════
   Wake word : "Hey JARVIS"        (detected locally)
   Mic       : {mic}
   STT / TTS : Vosk (offline) / Windows SAPI
   Overlay   : centre screen, always on top, click-through

   Try saying:
     "Hey JARVIS"
     "Hey JARVIS, ask SHURI to research LangGraph"
     "Hey JARVIS, check my Gmail"
     "JARVIS, stop"          (interrupts speech)

   Ctrl+C in this window to quit.
════════════════════════════════════════════════════════════
"""


class VoiceApp:
    def __init__(self) -> None:
        self.config = RuntimeConfig.load()
        self.overlay = WaveOverlay()
        self.mic = MicStream()
        self.runtime: JarvisRuntime | None = None
        self._stop = threading.Event()
        self._speaking = False
        self._current_agent = "JARVIS"

    # ------------------------------------------------------------- overlay hooks
    def _on_state(self, state: ConversationState) -> None:
        if state in (ConversationState.WAKE_DETECTED, ConversationState.LISTENING):
            # Don't yank the HUD out of "speaking" mid-sentence.
            if not self._speaking:
                self.overlay.show("listening", caption="Speak now")
        elif state == ConversationState.PROCESSING:
            self.overlay.show("thinking", caption="Routing your request")
        elif state == ConversationState.RESPONDING:
            self.overlay.show("speaking", agent=self._current_agent)
        elif state == ConversationState.PAUSED:
            self.overlay.show("stopped", caption="Microphone paused")
        elif state == ConversationState.IDLE:
            self.overlay.hide()

    def _on_speaking(self, agent: str, text: str) -> None:
        self._speaking = True
        self._current_agent = agent
        snippet = text if len(text) <= 64 else text[:61] + "…"
        self.overlay.show("speaking", agent=agent, caption=snippet)

    def _on_speaking_end(self) -> None:
        self._speaking = False

    def _on_heard(self, text: str) -> None:
        if text:
            print(f"\n[HEARD] {text}")
            self.overlay.show("thinking", caption=f'"{text}"')
        else:
            print("\n[HEARD] (nothing)")

    # --------------------------------------------------------------- amp pumping
    def _amp_pump(self) -> None:
        """Feed real audio level to the overlay so the waves track your voice."""
        while not self._stop.is_set():
            lvl = self.mic.last_level
            if self._speaking:
                # While JARVIS talks the mic is quiet; synthesise lively motion
                # so the waveform reads as speech rather than flatlining.
                lvl = 0.45 + 0.35 * abs((time.time() * 3.3) % 2 - 1)
            self.overlay.feed_amplitude(lvl)
            time.sleep(0.04)

    # -------------------------------------------------------------------- runtime
    async def _arun(self) -> None:
        wake = VoskWakeWordEngine(self.config.wake_phrase,
                                  self.config.wake_sensitivity, mic=self.mic,
                                  vosk_model_lang=self.config.vosk_model_lang,
                                  vosk_model_path=self.config.vosk_model_path)
        stt = VoskSTTProvider(self.mic,
                              no_speech_seconds=self.config.no_speech_timeout_s,
                              vosk_model_lang=self.config.vosk_model_lang,
                              vosk_model_path=self.config.vosk_model_path)
        try:
            tts = SapiTTSProvider(self.config.tts_voice_hint)
        except Exception as exc:
            print(f"[warn] SAPI TTS unavailable ({exc}); falling back to console.")
            from jarvis_local_agent.tts import DevTTSProvider
            tts = DevTTSProvider(enable_audio=False)

        try:
            backend = BackendClient(self.config)
        except BackendError as exc:
            log.error("cannot start: %s", exc)
            print(f"[error] {exc}")
            self._stop.set()
            return
        if not backend.has_credentials:
            msg = ("No device credentials. Register this PC on the dashboard (System > Local agents), "
                   "then run: python -m jarvis_local_agent.credentials set")
            log.error(msg)
            print(f"[error] {msg}")
        self.runtime = JarvisRuntime(
            self.config, wake, stt, tts, backend,
            capture_utterance=stt.capture_utterance,
            on_state_change=self._on_state,
            on_speaking=self._on_speaking,
            on_speaking_end=self._on_speaking_end,
            on_heard=self._on_heard,
        )

        if not await backend.ping():
            log.error("backend unreachable", extra={"url": self.config.backend_url})
            print(f"[error] Backend unreachable at {self.config.backend_url}.")
            print("        Start it with:  python -m jarvis_backend serve")
        print("[voice] loading offline speech model…")
        await self.runtime.start()
        print("[voice] listening for the wake word.\n")
        self.overlay.show("idle")
        self.overlay.hide()

        try:
            while not self._stop.is_set():
                await asyncio.sleep(0.3)
        finally:
            await self.runtime.shutdown()

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._arun())
        except Exception as exc:
            print(f"[runtime error] {exc}")
            import traceback
            traceback.print_exc()

    # ----------------------------------------------------------------------- main
    def run(self) -> None:
        try:
            import sounddevice as sd
            mic_name = sd.query_devices(kind="input")["name"]
        except Exception:
            mic_name = "unavailable"
        print(BANNER.format(mic=mic_name))

        threading.Thread(target=self._thread_main, daemon=True,
                         name="jarvis-rt").start()
        threading.Thread(target=self._amp_pump, daemon=True,
                         name="amp-pump").start()
        try:
            self.overlay.run()           # blocks on the main thread
        except KeyboardInterrupt:
            pass
        finally:
            self._stop.set()
            self.mic.stop()


def main() -> None:
    config = RuntimeConfig()
    setup_logging("voice", level=config.log_level, fmt=config.log_format,
                  log_dir=config.data_dir / "logs")
    if not single_instance.acquire():
        print("JARVIS is already running. Check the system tray.")
        sys.exit(0)
    try:
        VoiceApp().run()
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
