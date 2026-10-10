"""Microphone + offline wake word + offline STT check.

Verifies the real voice stack without the backend or overlay:
  1. mic opens and reports a live level
  2. the local wake word fires on "Hey JARVIS"
  3. Vosk transcribes a spoken sentence

    python tests/mic_check.py
"""
from __future__ import annotations

import sys
import threading
import time

from jarvis_local_agent.voice_local import (
    MicStream,
    VoskSTTProvider,
    VoskWakeWordEngine,
)


def main() -> None:
    print("1) opening microphone…")
    mic = MicStream()
    mic.start()
    time.sleep(1.0)

    print("   sampling input level for 3s — make some noise:")
    peak = 0.0
    end = time.time() + 3.0
    while time.time() < end:
        lvl = mic.last_level
        peak = max(peak, lvl)
        bar = "#" * int(lvl * 50)
        sys.stdout.write(f"\r   [{bar:<50}] {lvl:.3f}")
        sys.stdout.flush()
        time.sleep(0.05)
    print(f"\n   peak level: {peak:.3f}  ->", "OK" if peak > 0.01 else "NO SIGNAL")

    print("\n2) loading offline model + arming wake word…")
    fired = threading.Event()
    wake = VoskWakeWordEngine("hey jarvis", 0.6, mic=mic)
    wake.start(on_wake=fired.set)
    print('   say "Hey JARVIS" now (20s window)…')

    end = time.time() + 20
    while time.time() < end and not fired.is_set():
        time.sleep(0.1)

    if not fired.is_set():
        print("   wake word NOT detected.")
        mic.stop()
        return
    print("   WAKE DETECTED")

    print("\n3) now say a sentence (e.g. 'research langgraph')…")
    wake.suspended = True
    stt = VoskSTTProvider(mic)
    text = stt.capture_utterance()
    print(f"   transcribed: '{text}'")

    wake.stop()
    mic.stop()
    print("\ndone.")


if __name__ == "__main__":
    main()
