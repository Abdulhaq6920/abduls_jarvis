"""Entry point for the persistent runtime: `jarvis-local-agent`.

Dev mode (default): a single stdin reader drives BOTH the wake word and the utterance
capture. Type a line containing "hey jarvis" to wake; the rest of that line (or the
next line) is treated as your request. This exercises the full production pipeline
with zero audio hardware. Install the `voice` + `tray` extras and set
JARVIS_WAKE_ENGINE/STT/TTS to light up real microphone, speech, and tray.
"""
from __future__ import annotations

import asyncio
import queue
import sys
import threading

from jarvis_local_agent.backend_client import BackendClient
from jarvis_local_agent.config import RuntimeConfig
from jarvis_local_agent.runtime import JarvisRuntime
from jarvis_local_agent.stt import build_stt
from jarvis_local_agent.tray import TrayController, run_console_tray, run_gui_tray
from jarvis_local_agent.tts import build_tts
from jarvis_local_agent.wake import build_wake_engine


class LineBroker:
    """Single stdin reader feeding a queue. One line can be consumed by the wake
    engine (to detect the phrase) and the same text reused as the utterance in dev."""

    def __init__(self) -> None:
        self._q: queue.Queue[str] = queue.Queue()
        self._pending_utterance: str | None = None
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self) -> None:
        for line in sys.stdin:
            self._q.put(line.rstrip("\n"))

    def wake_line(self) -> str:
        # Blocking-ish read for the wake engine thread.
        try:
            line = self._q.get(timeout=0.3)
        except queue.Empty:
            return ""
        self._pending_utterance = line
        return line

    def capture_utterance(self) -> str:
        # In dev, the utterance is the same line that woke us (strip the wake phrase);
        # if that line was only the wake word, wait for the next line.
        import re
        line = self._pending_utterance or ""
        self._pending_utterance = None
        stripped = re.sub(r"(?i)^.*hey\s+jarvis[,.!:]?\s*", "", line).strip()
        if stripped:
            return stripped
        try:
            return self._q.get(timeout=8).strip()
        except queue.Empty:
            return ""


async def _amain() -> None:
    config = RuntimeConfig.load()
    broker = LineBroker()

    wake = build_wake_engine(config.wake_engine, config.wake_phrase,
                             config.wake_sensitivity, line_source=broker.wake_line)
    stt = build_stt(config.stt_engine)
    tts = build_tts(config.tts_engine)
    backend = BackendClient(config)

    runtime = JarvisRuntime(
        config, wake, stt, tts, backend,
        capture_utterance=broker.capture_utterance,
    )

    controller = TrayController(
        open_dashboard_url=config.backend_url,
        on_pause=runtime.pause_microphone,
        on_resume=runtime.resume_microphone,
        status_text=lambda: f"{runtime.status().conversation_state} · "
                            f"backend={'up' if runtime.status().backend_connected else 'down'}",
        on_quit=lambda: None,
    )
    if not run_gui_tray(controller, paused_getter=lambda: runtime.wake.paused):
        run_console_tray(controller, paused_getter=lambda: runtime.wake.paused)

    await runtime.start()
    print('Ready. Type e.g.:  hey jarvis, ask SHURI to research LangGraph')
    try:
        while True:
            await asyncio.sleep(1)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await runtime.shutdown()


def main() -> None:
    try:
        asyncio.run(_amain())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
